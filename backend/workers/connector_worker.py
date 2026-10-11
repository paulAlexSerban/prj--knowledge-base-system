"""HR connector worker.

Loads the contract registry and refuses to start when it is invalid. Sources
without a connector are logged and left alone. HR is polled in-process: the
P0 target is 30 seconds and EventBridge Scheduler cannot run faster than once
a minute, so the loop defaults to 10 seconds (`P0_POLL_SECONDS`).

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
from prometheus_client import Gauge, start_http_server
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from backend.apis.registry import load_registry
from backend.connectors.registry import CONNECTORS, build_connector
from backend.messaging.events import RAW_BUCKET
from backend.services import connector_metrics as metrics
from backend.services.snapshot_store import SnapshotStore
from backend.services.snapshot_sync import Syncer
from backend.services.sync_state import State
from shared.contracts.source_contract import SourceContract
from shared.logging import configure, log_event

LOG = logging.getLogger("connector-worker")
UP = Gauge("kbs_worker_up", "1 when the worker has loaded its contracts", ["worker"])


def partition(contracts: list[SourceContract]) -> tuple[list[SourceContract], list[SourceContract]]:
    ready, pending = [], []
    for contract in contracts:
        (ready if contract.source_id in CONNECTORS else pending).append(contract)
    return ready, pending


def main() -> None:
    configure("connector-worker", "ingestion")
    port = int(os.environ.get("METRICS_PORT", "9100"))
    start_http_server(port)
    contracts = load_registry()
    ready, pending = partition(contracts)
    for contract in contracts:
        log_event(
            LOG,
            "registered source",
            source_id=contract.source_id,
            freshness_tier=contract.freshness_tier.value,
        )
    for contract in pending:
        log_event(LOG, "no connector yet", source_id=contract.source_id)
    UP.labels(worker="connector").set(1)
    if not ready:
        while True:
            time.sleep(30)
    state, snapshots, connectors = _wait_until_ready(ready)
    interval = int(os.environ.get("P0_POLL_SECONDS", "10"))
    reconcile_every = int(os.environ.get("RECONCILE_SECONDS", "60"))
    workers = int(os.environ.get("SYNC_WORKERS", "8"))
    last_reconcile = 0.0
    while True:
        started = time.monotonic()
        for contract in ready:
            syncer = Syncer(connectors[contract.source_id], contract, state, snapshots, workers)
            try:
                syncer.run()
            except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the process
                log_event(
                    LOG,
                    "sync crashed",
                    level=logging.ERROR,
                    source_id=contract.source_id,
                    error=str(exc),
                )
            if time.monotonic() - last_reconcile >= reconcile_every:
                try:
                    syncer.reconcile()
                except Exception as exc:  # noqa: BLE001 - one bad sweep must not kill the process
                    log_event(
                        LOG,
                        "reconcile crashed",
                        level=logging.ERROR,
                        source_id=contract.source_id,
                        error=str(exc),
                    )
                last_reconcile = time.monotonic()
        time.sleep(max(0.0, interval - (time.monotonic() - started)))


def _wait_until_ready(contracts: list[SourceContract]) -> tuple[State, SnapshotStore, dict]:
    """Block until Postgres, the bucket, and each harness answer. Metrics stay up."""
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
        connectors = {}
        failed = False
        for contract in contracts:
            base = os.environ.get(f"HARNESS_{contract.source_id.upper()}_URL", "")
            if not base:
                log_event(
                    LOG, "harness url missing", level=logging.ERROR, source_id=contract.source_id
                )
                failed = True
                break
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
                failed = True
                break
            built = build_connector(contract.source_id, http)
            if built is None:
                failed = True
                break
            connectors[contract.source_id] = built
        if failed:
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        metrics.DEPENDENCY.labels(dependency="harness").set(1)
        snapshots = SnapshotStore(client, os.environ.get("RAW_BUCKET", RAW_BUCKET))
        return state, snapshots, connectors


def _postgres_engine():
    user = os.environ.get("POSTGRES_USER", "ingestion_rw")
    password = os.environ["INGESTION_DB_PASSWORD"]
    host = os.environ.get("POSTGRES_HOST", "postgres")
    database = os.environ.get("POSTGRES_DB", "kbs")
    return create_engine(
        f"postgresql+psycopg://{user}:{password}@{host}:5432/{database}",
        connect_args={"options": "-csearch_path=kbs"},
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


if __name__ == "__main__":
    main()
