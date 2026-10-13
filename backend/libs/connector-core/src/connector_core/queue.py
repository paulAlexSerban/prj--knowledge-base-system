"""Send one change event to the FIFO queue for a source's freshness tier."""

from opentelemetry.trace import SpanKind

from ingest_contract.events import TIER_QUEUES, ChangeEvent
from shared.telemetry import span, sqs_message_attributes


class SqsPublisher:
    """boto3 SQS publisher. The queue URL is resolved once, at construction."""

    def __init__(self, client: object, tier: str) -> None:
        try:
            queue_name = TIER_QUEUES[tier]
        except KeyError as exc:
            raise ValueError(f"unknown freshness tier {tier}") from exc
        self.client = client
        self.queue_name = queue_name
        self.queue_url = client.get_queue_url(QueueName=queue_name)["QueueUrl"]

    def publish(self, event: ChangeEvent) -> None:
        with span(
            "sqs.publish",
            span_kind=SpanKind.PRODUCER,
            queue=self.queue_name,
            source_id=event.source_id,
            event_type=event.event_type.value,
        ):
            kwargs: dict[str, object] = {
                "QueueUrl": self.queue_url,
                "MessageBody": event.to_json(),
                "MessageGroupId": event.message_group_id(),
                "MessageDeduplicationId": event.dedup_id(),
            }
            attributes = sqs_message_attributes()
            if attributes:
                kwargs["MessageAttributes"] = attributes
            self.client.send_message(**kwargs)
