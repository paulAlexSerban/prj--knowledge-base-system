"""Ingestion worker.

Loads the contract registry and refuses to start when it is invalid. Drains
the P0 queue before P1 before P2. Each event is written to Postgres and Neo4j.
The process waits until those stores and the snapshot bucket answer.
"""

import logging
import os
import time

import boto3

from ingestion_worker import metrics
from ingestion_worker.consumer import Consumer
from knowledge_store import metrics as store_metrics
from knowledge_store.runtime import open_store, ready
from shared.logging import configure, log_event
from shared.registry import load_registry
from shared.telemetry import init_telemetry

LOG = logging.getLogger("ingestion-worker")
DEPTH_SECONDS = 10


def main() -> None:
    """Drain P0 then P1 then P2 until stopped. One bad poll must not kill the process."""
    configure("ingestion-worker", "ingestion")
    init_telemetry("ingestion-worker")
    contracts = load_registry()
    port = int(os.environ.get("METRICS_PORT", "9101"))
    from prometheus_client import start_http_server

    start_http_server(port)
    log_event(LOG, "registry loaded", sources=len(contracts))
    metrics.UP.labels(worker="ingestion").set(1)
    fail = os.environ.get("FAIL_OBJECT_ID", "").strip() or None
    if fail:
        log_event(LOG, "poison flag set", object_id=fail)
    store = _wait_for_store()
    consumer = Consumer(_sqs_client(), store, fail_object_id=fail)
    last_depth = 0.0
    last_index = 0.0
    reconcile_every = int(os.environ.get("RECONCILE_INDEX_SECONDS", "60"))
    while True:
        try:
            if not consumer.queues_ready():
                metrics.QUEUES.set(0)
                store_metrics.DEPENDENCY.labels(dependency="sqs").set(0)
                time.sleep(2)
                continue
            metrics.QUEUES.set(1)
            store_metrics.DEPENDENCY.labels(dependency="sqs").set(1)
            consumer.poll_once()
            now = time.monotonic()
            if now - last_depth >= DEPTH_SECONDS:
                consumer.observe_depths()
                last_depth = now
            if now - last_index >= reconcile_every:
                store.reconcile_index()
                last_index = now
        except Exception as exc:  # noqa: BLE001 - one bad poll must not kill the process
            log_event(LOG, "poll crashed", level=logging.ERROR, error=str(exc))
            time.sleep(2)


def _wait_for_store():
    """Block until Postgres, Neo4j, and the snapshot bucket answer."""
    delay = 2.0
    while True:
        try:
            store = open_store()
        except Exception as exc:  # noqa: BLE001 - missing config or a driver error
            log_event(LOG, "waiting for the knowledge store", level=logging.WARNING, error=str(exc))
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        if ready(store):
            log_event(LOG, "knowledge store ready")
            return store
        log_event(LOG, "waiting for postgres, neo4j, or the snapshot bucket", level=logging.WARNING)
        time.sleep(delay)
        delay = min(delay * 2, 30)


def _sqs_client():
    """SQS against floci."""
    return boto3.client(
        "sqs",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


if __name__ == "__main__":
    main()
