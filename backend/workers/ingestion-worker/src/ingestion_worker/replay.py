"""Move dead-letter messages back onto their tier queue.

FIFO dedup keeps an id for five minutes after the original send. Replaying
with that same id is accepted and then dropped, so the replay id is the
original id plus the dead-letter message id. The message group stays the
same, so per-object order is unchanged.
"""

from ingest_contract.events import DLQ_FOR, fit_sqs_id
from shared.telemetry import forwarded_traceparent

PAIRS = tuple((dlq, tier) for tier, dlq in DLQ_FOR.items())


def replay_queue(client: object, dlq_name: str, tier_name: str, limit: int = 100) -> int:
    """Receive from the DLQ, send to the tier queue, then delete. Returns the count moved."""
    dlq_url = client.get_queue_url(QueueName=dlq_name)["QueueUrl"]
    tier_url = client.get_queue_url(QueueName=tier_name)["QueueUrl"]
    moved = 0
    while moved < limit:
        response = client.receive_message(
            QueueUrl=dlq_url,
            MaxNumberOfMessages=min(10, limit - moved),
            WaitTimeSeconds=1,
            VisibilityTimeout=30,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )
        messages = response.get("Messages", [])
        if not messages:
            return moved
        for message in messages:
            attributes = message.get("Attributes", {})
            group = attributes.get("MessageGroupId") or "replay"
            original = attributes.get("MessageDeduplicationId") or message["MessageId"]
            send: dict[str, object] = {
                "QueueUrl": tier_url,
                "MessageBody": message["Body"],
                "MessageGroupId": fit_sqs_id(group),
                "MessageDeduplicationId": fit_sqs_id(
                    f"{original}:replay:{message['MessageId']}"
                ),
            }
            forwarded = forwarded_traceparent(message)
            if forwarded:
                send["MessageAttributes"] = forwarded
            client.send_message(**send)
            client.delete_message(QueueUrl=dlq_url, ReceiptHandle=message["ReceiptHandle"])
            moved += 1
            if moved >= limit:
                return moved
    return moved


def replay_all(client: object, limit: int = 100) -> dict[str, int]:
    """Replay every tier DLQ. The limit is per queue."""
    return {dlq: replay_queue(client, dlq, tier, limit) for dlq, tier in PAIRS}
