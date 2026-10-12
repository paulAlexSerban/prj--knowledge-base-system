"""Read the product hook queue. A bad body stays on the queue and lands in the DLQ."""

import logging

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from ingest_contract.hooks import HOOK_QUEUE, HookNotice
from shared.logging import log_event

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

    def receive(self, wait: int = 10) -> list[tuple[HookNotice, str]]:
        response = self.client.receive_message(
            QueueUrl=self.url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=wait,
        )
        found: list[tuple[HookNotice, str]] = []
        for message in response.get("Messages", []):
            handle = message["ReceiptHandle"]
            try:
                notice = HookNotice.from_json(message["Body"])
            except ValidationError as exc:
                log_event(LOG, "invalid hook message", level=logging.ERROR, error=str(exc))
                continue
            found.append((notice, handle))
        return found

    def ack(self, handle: str) -> None:
        self.client.delete_message(QueueUrl=self.url, ReceiptHandle=handle)
