"""Process loop shared by the connector images.

Each image passes its source id, the env var that holds the harness URL, and
a factory that builds the source client. The loop polls, then reconciles.
`POLL_SECONDS` overrides the interval. HR still sets `P0_POLL_SECONDS`.
"""

import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from prometheus_client import REGISTRY, Gauge, start_http_server
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from connector_core import metrics
from connector_core.base import SourceConnector
from connector_core.queue import SqsPublisher
from connector_core.state import State
from connector_core.store import SnapshotStore
from connector_core.sync import Syncer
from ingest_contract.events import RAW_BUCKET, TIER_QUEUES
from shared.contracts.source_contract import SourceContract
from shared.logging import configure, log_event
from shared.registry import load_registry
from shared.telemetry import init_telemetry


def select_source(contracts: list[SourceContract], source_id: str) -> SourceContract:
    """Return one contract. A connector process does not start any other source."""
    for contract in contracts:
        if contract.source_id == source_id:
            return contract
    raise SystemExit(f"refusing to start: the {source_id} contract is not in the registry")


class Ready:
    """Dependencies a connector process needs after startup."""

    def __init__(
        self,
        contract: SourceContract,
        state: State,
        snapshots: SnapshotStore,
        connector: SourceConnector,
        publisher: SqsPublisher,
        log: logging.Logger,
    ) -> None:
        self.contract = contract
        self.state = state
        self.snapshots = snapshots
        self.connector = connector
        self.publisher = publisher
        self.log = log


def prepare(
    service: str,
    source_id: str,
    harness_env: str,
    factory: Callable[[str], SourceConnector],
) -> Ready:
    """Load the contract and block until Postgres, S3, the harness, and the tier queue answer."""
    log = logging.getLogger(service)
    configure(service, "ingestion")
    init_telemetry(service)
    port = int(os.environ.get("METRICS_PORT", "9100"))
    start_http_server(port)
    contract = select_source(load_registry(), source_id)
    log_event(
        log,
        "registered source",
        source_id=contract.source_id,
        freshness_tier=contract.freshness_tier.value,
    )
    _up().labels(worker=service).set(1)
    state, snapshots, connector, publisher = _wait_until_ready(contract, harness_env, factory, log)
    return Ready(contract, state, snapshots, connector, publisher, log)


def run_poll(
    service: str,
    source_id: str,
    harness_env: str,
    factory: Callable[[str], SourceConnector],
) -> None:
    """Block until dependencies answer, then poll and reconcile until the process stops."""
    ready = prepare(service, source_id, harness_env, factory)
    contract = ready.contract
    state = ready.state
    snapshots = ready.snapshots
    connector = ready.connector
    publisher = ready.publisher
    log = ready.log
    interval = _poll_seconds()
    reconcile_every = int(os.environ.get("RECONCILE_SECONDS", "60"))
    workers = int(os.environ.get("SYNC_WORKERS", "8"))
    last_reconcile = 0.0
    while True:
        started = time.monotonic()
        syncer = Syncer(connector, contract, state, snapshots, workers, publisher)
        try:
            syncer.run()
        except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the process
            log_event(log, "sync crashed", level=logging.ERROR, source_id=source_id, error=str(exc))
        if time.monotonic() - last_reconcile >= reconcile_every:
            try:
                syncer.reconcile()
            except Exception as exc:  # noqa: BLE001 - one bad sweep must not kill the process
                log_event(
                    log,
                    "reconcile crashed",
                    level=logging.ERROR,
                    source_id=source_id,
                    error=str(exc),
                )
            last_reconcile = time.monotonic()
        time.sleep(max(0.0, interval - (time.monotonic() - started)))


def postgres_engine():
    """ingestion_rw engine. Connectors never use the gateway_ro password."""
    user = os.environ.get("POSTGRES_USER", "ingestion_rw")
    password = os.environ["INGESTION_DB_PASSWORD"]
    host = os.environ.get("POSTGRES_HOST", "postgres")
    database = os.environ.get("POSTGRES_DB", "kbs")
    return create_engine(
        f"postgresql+psycopg://{user}:{password}@{host}:5432/{database}",
        connect_args={"options": "-csearch_path=kbs"},
    )


def sqs_client():
    """SQS against floci. The publisher resolves the tier queue URL once."""
    return boto3.client(
        "sqs",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


def s3_client():
    """S3 with path-style addressing so floci bucket names resolve."""
    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        config=Config(s3={"addressing_style": "path"}),
    )


def _poll_seconds() -> int:
    """POLL_SECONDS wins. HR still uses P0_POLL_SECONDS because Scheduler cannot own P0."""
    if os.environ.get("POLL_SECONDS"):
        return int(os.environ["POLL_SECONDS"])
    return int(os.environ.get("P0_POLL_SECONDS", "10"))


def _up() -> Gauge:
    """Reuse kbs_worker_up when tests import more than one connector in-process."""
    existing = REGISTRY._names_to_collectors.get("kbs_worker_up")
    if isinstance(existing, Gauge):
        return existing
    return Gauge("kbs_worker_up", "1 when the worker has loaded its contracts", ["worker"])


def _wait_until_ready(
    contract: SourceContract,
    harness_env: str,
    factory: Callable[[str], SourceConnector],
    log: logging.Logger,
) -> tuple[State, SnapshotStore, SourceConnector, SqsPublisher]:
    """Block until Postgres, the bucket, the harness, and the tier queue answer."""
    delay = 2.0
    source_id = contract.source_id
    while True:
        try:
            state = State(postgres_engine())
            if not state.ping():
                raise RuntimeError("sync_state is not there yet")
            metrics.DEPENDENCY.labels(dependency="postgres").set(1)
        except (SQLAlchemyError, OSError, RuntimeError) as exc:
            metrics.DEPENDENCY.labels(dependency="postgres").set(0)
            log_event(log, "waiting for postgres", level=logging.WARNING, error=str(exc))
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        try:
            client = s3_client()
            client.head_bucket(Bucket=os.environ.get("RAW_BUCKET", RAW_BUCKET))
            metrics.DEPENDENCY.labels(dependency="s3").set(1)
        except (BotoCoreError, ClientError, OSError) as exc:
            metrics.DEPENDENCY.labels(dependency="s3").set(0)
            log_event(log, "waiting for the snapshot bucket", level=logging.WARNING, error=str(exc))
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        base = os.environ.get(harness_env, "")
        if not base:
            log_event(log, "harness url missing", level=logging.ERROR, source_id=source_id)
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        try:
            _check_health(base)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            metrics.DEPENDENCY.labels(dependency="harness").set(0)
            log_event(
                log,
                "waiting for harness",
                level=logging.WARNING,
                source_id=source_id,
                error=str(exc),
            )
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        metrics.DEPENDENCY.labels(dependency="harness").set(1)
        try:
            publisher = SqsPublisher(sqs_client(), contract.freshness_tier.value)
        except (BotoCoreError, ClientError, OSError, ValueError) as exc:
            metrics.DEPENDENCY.labels(dependency="sqs").set(0)
            log_event(
                log,
                "waiting for the tier queue",
                level=logging.WARNING,
                source_id=source_id,
                queue=TIER_QUEUES.get(contract.freshness_tier.value, ""),
                error=str(exc),
            )
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        metrics.DEPENDENCY.labels(dependency="sqs").set(1)
        snapshots = SnapshotStore(client, os.environ.get("RAW_BUCKET", RAW_BUCKET))
        return state, snapshots, factory(base), publisher


def _check_health(base: str) -> None:
    """GET /health on the harness. A 4xx means that source is not ready."""
    with urllib.request.urlopen(base.rstrip("/") + "/health", timeout=5) as response:
        if response.status >= 400:
            raise OSError(f"harness health returned {response.status}")
