"""Ingestion worker stub.

Checks that the tier queues exist on floci and then idles. It does not
embed or write to Postgres or Neo4j.
"""

import logging
import os
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from prometheus_client import Gauge, start_http_server

from backend.messaging.events import P0_QUEUE, P1_QUEUE, P2_QUEUE

LOG = logging.getLogger("ingestion-worker")
UP = Gauge("kbs_worker_up", "1 when the worker process is up", ["worker"])
QUEUES = Gauge("kbs_queues_reachable", "1 when P0, P1, and P2 queues all answer")


def queues_reachable() -> bool:
    client = boto3.client(
        "sqs",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )
    try:
        for name in (P0_QUEUE, P1_QUEUE, P2_QUEUE):
            client.get_queue_url(QueueName=name)
    except (BotoCoreError, ClientError, OSError) as exc:
        LOG.warning("queues not reachable yet: %s", exc)
        return False
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    port = int(os.environ.get("METRICS_PORT", "9101"))
    start_http_server(port)
    UP.labels(worker="ingestion").set(1)
    while True:
        QUEUES.set(1 if queues_reachable() else 0)
        time.sleep(int(os.environ.get("QUEUE_CHECK_SECONDS", "10")))


if __name__ == "__main__":
    main()
