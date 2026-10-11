"""Drain P0, then P1, then P2.

A handler error leaves the message in flight. After the visibility timeout
it is received again. Once ApproximateReceiveCount passes the redrive
limit, this process copies the body to the dead-letter queue and deletes
it. That covers a floci build that accepts a redrive policy and does not
apply it. When floci does apply it, the message is already gone before
this count is reached.
"""

import logging
from datetime import UTC, datetime

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from ingest_contract.events import DLQ_FOR, TIER_ORDER, ChangeEvent, fit_sqs_id
from ingestion_worker import metrics
from ingestion_worker.store import Store, dispatch
from shared.logging import log_event

LOG = logging.getLogger("ingestion-worker")
MAX_RECEIVE = 5


class HandlerError(Exception):
    """The message must be retried. FAIL_OBJECT_ID raises this on purpose."""


class Consumer:
    def __init__(
        self,
        client: object,
        store: Store,
        fail_object_id: str | None = None,
        visibility_timeout: int | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.fail_object_id = fail_object_id
        self.visibility_timeout = visibility_timeout
        self._urls: dict[str, str] = {}

    def queues_ready(self) -> bool:
        try:
            for name in (*TIER_ORDER, *DLQ_FOR.values()):
                self.queue_url(name)
        except (BotoCoreError, ClientError, OSError) as exc:
            log_event(LOG, "queues not reachable yet", level=logging.WARNING, error=str(exc))
            self._urls.clear()
            return False
        return True

    def queue_url(self, name: str) -> str:
        if name not in self._urls:
            self._urls[name] = self.client.get_queue_url(QueueName=name)["QueueUrl"]
        return self._urls[name]

    def poll_once(self) -> int:
        """One batch from the highest tier that has messages. P0 is long-polled."""
        for index, name in enumerate(TIER_ORDER):
            messages = self._receive(name, 1 if index == 0 else 0)
            if not messages:
                continue
            failed_groups: set[str] = set()
            handled = 0
            for message in messages:
                group = message.get("Attributes", {}).get("MessageGroupId", "")
                # A failed message blocks later ones in its group. Real FIFO
                # would not have delivered them yet. moto sometimes does.
                if group and group in failed_groups:
                    continue
                if self._handle_message(name, message):
                    handled += 1
                elif group:
                    failed_groups.add(group)
            return handled
        return 0

    def observe_depths(self) -> None:
        for name in TIER_ORDER:
            self._set_depth(metrics.QUEUE_DEPTH, name)
        for name in DLQ_FOR.values():
            self._set_depth(metrics.DLQ_DEPTH, name)

    def _set_depth(self, gauge, name: str) -> None:
        try:
            attributes = self.client.get_queue_attributes(
                QueueUrl=self.queue_url(name),
                AttributeNames=["ApproximateNumberOfMessages"],
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            log_event(LOG, "queue depth failed", level=logging.WARNING, queue=name, error=str(exc))
            return
        gauge.labels(queue=name).set(
            int(attributes.get("Attributes", {}).get("ApproximateNumberOfMessages", "0"))
        )

    def _receive(self, name: str, wait: int) -> list[dict]:
        kwargs = {
            "QueueUrl": self.queue_url(name),
            "MaxNumberOfMessages": 10,
            "WaitTimeSeconds": wait,
            "AttributeNames": ["All"],
        }
        if self.visibility_timeout is not None:
            kwargs["VisibilityTimeout"] = self.visibility_timeout
        try:
            response = self.client.receive_message(**kwargs)
        except (BotoCoreError, ClientError, OSError) as exc:
            log_event(LOG, "receive failed", level=logging.WARNING, queue=name, error=str(exc))
            return []
        return list(response.get("Messages", []))

    def _handle_message(self, queue_name: str, message: dict) -> bool:
        """True when the message was deleted. False when it stays in flight."""
        count = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        if count > MAX_RECEIVE:
            return self._move_to_dlq(queue_name, message)
        try:
            event = ChangeEvent.from_json(message["Body"])
        except (ValidationError, ValueError) as exc:
            metrics.EVENTS.labels(source_id="unknown", event_type="invalid", result="invalid").inc()
            log_event(
                LOG,
                "invalid event",
                level=logging.ERROR,
                request_id=message.get("MessageId", ""),
                error=str(exc),
            )
            return False
        try:
            if self.fail_object_id and event.object_id == self.fail_object_id:
                raise HandlerError(f"FAIL_OBJECT_ID matched {event.object_id}")
            dispatch(self.store, event)
        except Exception as exc:  # noqa: BLE001 - leave the message for SQS to retry
            metrics.EVENTS.labels(
                source_id=event.source_id,
                event_type=event.event_type.value,
                result="retry",
            ).inc()
            log_event(
                LOG,
                "event retry",
                level=logging.WARNING,
                source_id=event.source_id,
                object_id=event.object_id,
                event_type=event.event_type.value,
                request_id=message.get("MessageId", ""),
                error=str(exc),
            )
            return False
        self._delete(queue_name, message)
        metrics.EVENTS.labels(
            source_id=event.source_id,
            event_type=event.event_type.value,
            result="ok",
        ).inc()
        metrics.EVENT_LAG.labels(event.source_id).observe(_lag(event.occurred_at))
        log_event(
            LOG,
            "event handled",
            source_id=event.source_id,
            object_id=event.object_id,
            event_type=event.event_type.value,
            request_id=message.get("MessageId", ""),
        )
        return True

    def _move_to_dlq(self, queue_name: str, message: dict) -> bool:
        dlq_name = DLQ_FOR[queue_name]
        attributes = message.get("Attributes", {})
        group = attributes.get("MessageGroupId") or "poison"
        try:
            self.client.send_message(
                QueueUrl=self.queue_url(dlq_name),
                MessageBody=message["Body"],
                MessageGroupId=fit_sqs_id(group),
                MessageDeduplicationId=fit_sqs_id(f"dlq-{message['MessageId']}"),
            )
            self._delete(queue_name, message)
        except (BotoCoreError, ClientError, OSError) as exc:
            log_event(
                LOG,
                "dead letter move failed",
                level=logging.ERROR,
                queue=dlq_name,
                request_id=message.get("MessageId", ""),
                error=str(exc),
            )
            return False
        log_event(
            LOG,
            "event dead lettered",
            request_id=message.get("MessageId", ""),
            queue=dlq_name,
        )
        return True

    def _delete(self, queue_name: str, message: dict) -> None:
        self.client.delete_message(
            QueueUrl=self.queue_url(queue_name),
            ReceiptHandle=message["ReceiptHandle"],
        )


def _lag(occurred_at: datetime) -> float:
    moment = occurred_at if occurred_at.tzinfo is not None else occurred_at.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - moment).total_seconds())
