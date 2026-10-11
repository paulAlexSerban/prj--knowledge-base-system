"""Queue consume, order, dead-letter, and replay.

SQS is moto, the same way the snapshot tests fake S3. `make test` does not
start floci. The manual gate runs the same path against floci.
"""

import json
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from ingest_contract.events import (
    P0_DLQ,
    P0_QUEUE,
    P1_DLQ,
    P1_QUEUE,
    P2_DLQ,
    P2_QUEUE,
    ChangeEvent,
)
from ingestion_worker.consumer import MAX_RECEIVE, Consumer
from ingestion_worker.replay import replay_queue
from ingestion_worker.store import MemoryStore

QUEUES = (P0_QUEUE, P1_QUEUE, P2_QUEUE)
DLQS = (P0_DLQ, P1_DLQ, P2_DLQ)


def _event(object_id: str, version: str, event_type: str = "update") -> ChangeEvent:
    return ChangeEvent(
        source_id="hr",
        object_id=object_id,
        event_type=event_type,
        version=version,
        content_hash=f"hash-{version}",
        occurred_at=datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
        s3_pointer=f"raw/hr/person/{object_id}.json",
    )


def _send(client, queue: str, event: ChangeEvent, dedup: str | None = None) -> None:
    url = client.get_queue_url(QueueName=queue)["QueueUrl"]
    client.send_message(
        QueueUrl=url,
        MessageBody=event.to_json(),
        MessageGroupId=event.message_group_id(),
        MessageDeduplicationId=dedup or event.dedup_id(),
    )


@pytest.fixture
def sqs():
    with mock_aws():
        client = boto3.client("sqs", region_name="us-east-1")
        dlq_arns = {}
        for name in DLQS:
            url = client.create_queue(
                QueueName=name,
                Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "true"},
            )["QueueUrl"]
            dlq_arns[name] = client.get_queue_attributes(
                QueueUrl=url, AttributeNames=["QueueArn"]
            )["Attributes"]["QueueArn"]
        for name, dlq in zip(QUEUES, DLQS, strict=True):
            client.create_queue(
                QueueName=name,
                Attributes={
                    "FifoQueue": "true",
                    "ContentBasedDeduplication": "true",
                    "VisibilityTimeout": "60",
                    "RedrivePolicy": json.dumps(
                        {"deadLetterTargetArn": dlq_arns[dlq], "maxReceiveCount": MAX_RECEIVE}
                    ),
                },
            )
        yield client


def test_create_then_update_arrive_in_order(sqs) -> None:
    store = MemoryStore()
    consumer = Consumer(sqs, store, visibility_timeout=0)
    _send(sqs, P0_QUEUE, _event("emp-1", "1", "create"))
    _send(sqs, P0_QUEUE, _event("emp-1", "2", "update"))
    # Real FIFO delivers one in-flight message per group. moto may return both.
    # Either way the store must apply create before update.
    for _ in range(2):
        if len(store.applied) >= 2:
            break
        assert consumer.poll_once() >= 1
    assert [event.version for event in store.applied] == ["1", "2"]
    assert store.objects[("hr", "emp-1")].version == "2"


def test_p0_is_drained_before_p1(sqs) -> None:
    store = MemoryStore()
    consumer = Consumer(sqs, store, visibility_timeout=0)
    _send(sqs, P1_QUEUE, _event("deal-1", "1"))
    _send(sqs, P0_QUEUE, _event("policy-1", "1"))
    consumer.poll_once()
    assert list(store.objects) == [("hr", "policy-1")]
    consumer.poll_once()
    assert ("hr", "deal-1") in store.objects


def test_replay_of_the_same_event_does_not_duplicate(sqs) -> None:
    store = MemoryStore()
    consumer = Consumer(sqs, store, visibility_timeout=0)
    event = _event("emp-1", "1", "create")
    _send(sqs, P0_QUEUE, event)
    _send(sqs, P0_QUEUE, event, dedup=f"{event.dedup_id()}-again")
    consumer.poll_once()
    consumer.poll_once()
    assert len(store.applied) == 1
    assert len(store.objects) == 1


def test_rejected_message_reaches_the_dlq_and_replays_once(sqs) -> None:
    store = MemoryStore()
    consumer = Consumer(sqs, store, fail_object_id="emp-9", visibility_timeout=0)
    _send(sqs, P0_QUEUE, _event("emp-9", "1", "create"))
    for _ in range(MAX_RECEIVE + 3):
        consumer.poll_once()
        depth = _visible(sqs, P0_DLQ)
        if depth >= 1:
            break
    assert _visible(sqs, P0_DLQ) == 1
    assert store.objects == {}
    consumer.fail_object_id = None
    assert replay_queue(sqs, P0_DLQ, P0_QUEUE) == 1
    assert _visible(sqs, P0_DLQ) == 0
    consumer.poll_once()
    assert store.objects[("hr", "emp-9")].version == "1"
    assert len(store.applied) == 1


def test_invalid_body_is_not_acknowledged(sqs) -> None:
    store = MemoryStore()
    consumer = Consumer(sqs, store, visibility_timeout=0)
    url = sqs.get_queue_url(QueueName=P0_QUEUE)["QueueUrl"]
    sqs.send_message(
        QueueUrl=url,
        MessageBody="not-json",
        MessageGroupId="hr:bad",
        MessageDeduplicationId="bad-1",
    )
    consumer.poll_once()
    assert store.applied == []
    again = sqs.receive_message(
        QueueUrl=url,
        MaxNumberOfMessages=1,
        VisibilityTimeout=0,
        AttributeNames=["ApproximateReceiveCount"],
    )
    assert again["Messages"][0]["Body"] == "not-json"


def _visible(client, name: str) -> int:
    url = client.get_queue_url(QueueName=name)["QueueUrl"]
    attributes = client.get_queue_attributes(
        QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
    )
    return int(attributes["Attributes"]["ApproximateNumberOfMessages"])
