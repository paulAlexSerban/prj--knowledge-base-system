"""HR connector process.

Loads the contract registry and refuses to start when it is invalid. This
process runs HR only. Sales, Product, and Engineering get their own
connectors when those connectors exist. The P0 target is 30 seconds and
EventBridge Scheduler cannot run faster than once a minute, so the loop
defaults to 10 seconds (`P0_POLL_SECONDS`).

Each cycle writes raw snapshots to S3 and records them in Postgres. Deletes
are found by the reconciliation sweep (`RECONCILE_SECONDS`, default 60),
because the HR harness has no delete feed.
"""

import logging
import os
import time

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from prometheus_client import REGISTRY, Gauge, start_http_server
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from connector_core import metrics
from connector_core.queue import SqsPublisher
from connector_core.state import State
from connector_core.store import SnapshotStore
from connector_core.sync import Syncer
from connector_hr.client import HrConnector
from ingest_contract.events import RAW_BUCKET, TIER_QUEUES
from shared.contracts.source_contract import SourceContract
from shared.logging import configure, log_event
from shared.registry import load_registry

LOG = logging.getLogger("connector-hr")
UP = REGISTRY._names_to_collectors.get("kbs_worker_up") or Gauge(
    "kbs_worker_up", "1 when the worker has loaded its contracts", ["worker"]
)


def select_hr(contracts: list[SourceContract]) -> SourceContract:
    """Return the HR contract. This process does not start any other source."""
    for contract in contracts:
        if contract.source_id == "hr":
            return contract
    raise SystemExit("refusing to start: the hr contract is not in the registry")


def main() -> None:
    configure("connector-hr", "ingestion")
    port = int(os.environ.get("METRICS_PORT", "9100"))
    start_http_server(port)
    contract = select_hr(load_registry())
    log_event(
        LOG,
        "registered source",
        source_id=contract.source_id,
        freshness_tier=contract.freshness_tier.value,
    )
    UP.labels(worker="connector-hr").set(1)
    state, snapshots, connector, publisher = _wait_until_ready(contract)
    interval = int(os.environ.get("P0_POLL_SECONDS", "10"))
    reconcile_every = int(os.environ.get("RECONCILE_SECONDS", "60"))
    workers = int(os.environ.get("SYNC_WORKERS", "8"))
    last_reconcile = 0.0
    while True:
        started = time.monotonic()
        syncer = Syncer(connector, contract, state, snapshots, workers, publisher)
        try:
            syncer.run()
        except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the process
            log_event(LOG, "sync crashed", level=logging.ERROR, source_id="hr", error=str(exc))
        if time.monotonic() - last_reconcile >= reconcile_every:
            try:
                syncer.reconcile()
            except Exception as exc:  # noqa: BLE001 - one bad sweep must not kill the process
                log_event(
                    LOG, "reconcile crashed", level=logging.ERROR, source_id="hr", error=str(exc)
                )
            last_reconcile = time.monotonic()
        time.sleep(max(0.0, interval - (time.monotonic() - started)))


def _wait_until_ready(
    contract: SourceContract,
) -> tuple[State, SnapshotStore, HrConnector, SqsPublisher]:
    """Block until Postgres, the bucket, and the HR harness answer. Metrics stay up."""
    delay = 2.0
    while True:
        try:
            state = State(_postgres_engine())
            if not state.ping():
                raise RuntimeError("sync_state is not there yet")
            metrics.DEPENDENCY.labels(dependency="postgres").set(1)
        except (SQLAlchemyError, OSError, RuntimeError) as exc:
            metrics.DEPENDENCY.labels(dependency="postgres").set(0)
            log_event(LOG, "waiting for postgres", level=logging.WARNING, error=str(exc))
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        try:
            client = _s3_client()
            client.head_bucket(Bucket=os.environ.get("RAW_BUCKET", RAW_BUCKET))
            metrics.DEPENDENCY.labels(dependency="s3").set(1)
        except (BotoCoreError, ClientError, OSError) as exc:
            metrics.DEPENDENCY.labels(dependency="s3").set(0)
            log_event(LOG, "waiting for the snapshot bucket", level=logging.WARNING, error=str(exc))
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        base = os.environ.get("HARNESS_HR_URL", "")
        if not base:
            log_event(LOG, "harness url missing", level=logging.ERROR, source_id=contract.source_id)
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        http = httpx.Client(base_url=base, timeout=30)
        try:
            http.get("/health").raise_for_status()
        except httpx.HTTPError as exc:
            metrics.DEPENDENCY.labels(dependency="harness").set(0)
            log_event(
                LOG,
                "waiting for harness",
                level=logging.WARNING,
                source_id=contract.source_id,
                error=str(exc),
            )
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        metrics.DEPENDENCY.labels(dependency="harness").set(1)
        try:
            publisher = SqsPublisher(_sqs_client(), contract.freshness_tier.value)
        except (BotoCoreError, ClientError, OSError, ValueError) as exc:
            http.close()
            metrics.DEPENDENCY.labels(dependency="sqs").set(0)
            log_event(
                LOG,
                "waiting for the tier queue",
                level=logging.WARNING,
                source_id=contract.source_id,
                queue=TIER_QUEUES.get(contract.freshness_tier.value, ""),
                error=str(exc),
            )
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        metrics.DEPENDENCY.labels(dependency="sqs").set(1)
        snapshots = SnapshotStore(client, os.environ.get("RAW_BUCKET", RAW_BUCKET))
        return state, snapshots, HrConnector(http), publisher


def _postgres_engine():
    user = os.environ.get("POSTGRES_USER", "ingestion_rw")
    password = os.environ["INGESTION_DB_PASSWORD"]
    host = os.environ.get("POSTGRES_HOST", "postgres")
    database = os.environ.get("POSTGRES_DB", "kbs")
    return create_engine(
        f"postgresql+psycopg://{user}:{password}@{host}:5432/{database}",
        connect_args={"options": "-csearch_path=kbs"},
    )


def _sqs_client():
    return boto3.client(
        "sqs",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        config=Config(s3={"addressing_style": "path"}),
    )
