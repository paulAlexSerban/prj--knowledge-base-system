"""Read the product hook queue. A bad body stays on the queue and lands in the DLQ."""

import logging

from botocore.exceptions import BotoCoreError, ClientError
from prometheus_client import Gauge
from pydantic import ValidationError

from ingest_contract.hooks import HOOK_DLQ, HOOK_QUEUE, HookNotice
from shared.logging import log_event
from shared.telemetry import sqs_parent

HOOK_DEPTH = Gauge(
    "kbs_hook_queue_depth",
    "Visible messages on the product hook queue or its dead-letter queue",
    ["queue"],
)

LOG = logging.getLogger("connector-product")


class HookQueue:
    def __init__(self, client: object) -> None:
        self.client = client
        self.url = ""

    def ready(self) -> bool:
        try:
            self.url = self.client.get_queue_url(QueueName=HOOK_QUEUE)["QueueUrl"]
        except (BotoCoreError, ClientError, OSError):
            return False
        return True

    def receive(self, wait: int = 10) -> list[tuple[HookNotice, str, object]]:
        response = self.client.receive_message(
            QueueUrl=self.url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=wait,
            MessageAttributeNames=["All"],
        )
        found: list[tuple[HookNotice, str, object]] = []
        for message in response.get("Messages", []):
            handle = message["ReceiptHandle"]
            try:
                notice = HookNotice.from_json(message["Body"])
            except ValidationError as exc:
                log_event(LOG, "invalid hook message", level=logging.ERROR, error=str(exc))
                continue
            found.append((notice, handle, sqs_parent(message)))
        return found

    def ack(self, handle: str) -> None:
        self.client.delete_message(QueueUrl=self.url, ReceiptHandle=handle)

    def observe(self) -> None:
        for name in (HOOK_QUEUE, HOOK_DLQ):
            try:
                url = self.client.get_queue_url(QueueName=name)["QueueUrl"]
                attributes = self.client.get_queue_attributes(
                    QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
                )
            except (BotoCoreError, ClientError, OSError) as exc:
                log_event(
                    LOG, "hook depth failed", level=logging.WARNING, queue=name, error=str(exc)
                )
                continue
            visible = int(attributes.get("Attributes", {}).get("ApproximateNumberOfMessages", "0"))
            HOOK_DEPTH.labels(queue=name).set(visible)
