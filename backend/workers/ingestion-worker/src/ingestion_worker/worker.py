"""Ingestion worker.

Loads the contract registry and refuses to start when it is invalid. Drains
the P0 queue before P1 before P2. Writes go through the store interface.
Until the knowledge store is wired, the process logs each event.
"""

import logging
import os
import time

import boto3

from ingestion_worker import metrics
from ingestion_worker.consumer import Consumer
from ingestion_worker.store import LoggingStore
from shared.logging import configure, log_event
from shared.registry import load_registry

LOG = logging.getLogger("ingestion-worker")
DEPTH_SECONDS = 10


def main() -> None:
    configure("ingestion-worker", "ingestion")
    contracts = load_registry()
    port = int(os.environ.get("METRICS_PORT", "9101"))
    from prometheus_client import start_http_server

    start_http_server(port)
    log_event(LOG, "registry loaded", sources=len(contracts))
    metrics.UP.labels(worker="ingestion").set(1)
    fail = os.environ.get("FAIL_OBJECT_ID", "").strip() or None
    if fail:
        log_event(LOG, "poison flag set", object_id=fail)
    consumer = Consumer(_sqs_client(), LoggingStore(), fail_object_id=fail)
    last_depth = 0.0
    while True:
        try:
            if not consumer.queues_ready():
                metrics.QUEUES.set(0)
                time.sleep(2)
                continue
            metrics.QUEUES.set(1)
            consumer.poll_once()
            if time.monotonic() - last_depth >= DEPTH_SECONDS:
                consumer.observe_depths()
                last_depth = time.monotonic()
        except Exception as exc:  # noqa: BLE001 - one bad poll must not kill the process
            log_event(LOG, "poll crashed", level=logging.ERROR, error=str(exc))
            time.sleep(2)


def _sqs_client():
    return boto3.client(
        "sqs",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


if __name__ == "__main__":
    main()
