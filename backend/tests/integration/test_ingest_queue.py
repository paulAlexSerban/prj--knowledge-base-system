"""Queue consume, order, dead-letter, and replay.

SQS is moto, the same way the snapshot tests fake S3. `make test` does not
start floci. The manual gate runs the same path against floci.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import text

from ingest_contract.events import (
    P0_DLQ,
    P0_QUEUE,
    P1_DLQ,
    P1_QUEUE,
    P2_DLQ,
    P2_QUEUE,
    RAW_BUCKET,
    ChangeEvent,
)
from ingestion_worker.consumer import MAX_RECEIVE, Consumer
from ingestion_worker.replay import replay_queue
from ingestion_worker.store import MemoryStore
from knowledge_store.embedder import HashEmbedder
from knowledge_store.graph import GraphWriter
from knowledge_store.postgres import ChunkTable
from knowledge_store.store import KnowledgeStore
from shared.contracts.validate import load_contracts
from shared.logging import configure

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


def test_handled_log_names_the_decision_and_drops_the_payload(sqs, capsys) -> None:
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    try:
        configure("ingestion-worker", "ingestion")
        consumer = Consumer(sqs, MemoryStore(), visibility_timeout=0)
        _send(sqs, P0_QUEUE, _event("emp-1", "1", "create"))
        url = sqs.get_queue_url(QueueName=P0_QUEUE)["QueueUrl"]
        sqs.send_message(
            QueueUrl=url,
            MessageBody=(
                '{"salary":90000,"compensation":1,"ssn":"111","national_id":"NID",'
                '"api_key":"sk-secret","content":"Ada Lovelace chunk text"}'
            ),
            MessageGroupId="hr:poison",
            MessageDeduplicationId="poison-salary",
        )
        for _ in range(4):
            consumer.poll_once()
    finally:
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)
    raw = capsys.readouterr().out
    assert '"decision": "ok"' in raw
    assert '"decision": "invalid"' in raw
    assert '"event_type": "create"' in raw
    assert "request_id" in raw
    for forbidden in (
        "salary",
        "compensation",
        "ssn",
        "national_id",
        "90000",
        "sk-secret",
        "Ada Lovelace",
        "chunk text",
    ):
        assert forbidden not in raw


class _GraphOnce:
    """Fail the first graph write. Later calls go to Neo4j."""

    def __init__(self, inner: GraphWriter, admin) -> None:
        self.inner = inner
        self.admin = admin
        self.chunks_before_write: list[int] = []
        self.attempts = 0

    def write(self, *args, **kwargs) -> None:
        with self.admin.connect() as connection:
            count = connection.execute(text("SELECT count(*) AS n FROM chunks")).one().n
        self.chunks_before_write.append(int(count))
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("neo4j down")
        self.inner.write(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        self.inner.delete(*args, **kwargs)

    def set_acl(self, *args, **kwargs) -> None:
        self.inner.set_acl(*args, **kwargs)

    def count_node(self, source_id: str, object_id: str) -> int:
        return self.inner.count_node(source_id, object_id)


def test_graph_failure_leaves_the_message_and_the_replay_writes_once(
    sqs, database, neo4j_driver
) -> None:
    with database["admin"].begin() as connection:
        connection.execute(text("TRUNCATE chunks, tombstones, source_objects"))
    neo4j_driver.execute_query("MATCH (n) DETACH DELETE n")
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=RAW_BUCKET)
    key = "raw/hr/policy/pol-queue.md"
    body = (
        b"# Leave\n\n## Eligibility\n\nParental leave in Germany is sixteen weeks.\n\n"
        b"## Notice\n\nTell the manager four weeks ahead.\n"
    )
    client.put_object(
        Bucket=RAW_BUCKET,
        Key=key,
        Body=body,
        Metadata={"kbs-content-hash": "hash-q", "kbs-version": "1", "kbs-native-acl": "hr_employee"},
    )
    graph = _GraphOnce(GraphWriter(neo4j_driver), database["admin"])
    root = Path(__file__).resolve().parents[3]
    contracts = load_contracts(root / "infrastructure" / "config" / "sources")
    store = KnowledgeStore(
        ChunkTable(database["writer"], database["reader"]),
        graph,
        client,
        RAW_BUCKET,
        HashEmbedder(),
        {item.source_id: item for item in contracts},
    )
    consumer = Consumer(sqs, store, visibility_timeout=0)
    event = ChangeEvent(
        source_id="hr",
        object_id="pol-queue",
        event_type="create",
        version="1",
        content_hash="hash-q",
        occurred_at=datetime(2026, 1, 15, 9, 0, tzinfo=UTC),
        s3_pointer=key,
    )
    _send(sqs, P0_QUEUE, event)
    assert consumer.poll_once() == 0
    assert graph.chunks_before_write == [2]
    assert graph.count_node("hr", "pol-queue") == 0
    with database["admin"].connect() as connection:
        after_failure = connection.execute(text("SELECT count(*) AS n FROM chunks")).one().n
    assert after_failure == 2
    assert consumer.poll_once() == 1
    assert graph.count_node("hr", "pol-queue") == 1
    with database["admin"].connect() as connection:
        after_replay = connection.execute(text("SELECT count(*) AS n FROM chunks")).one().n
    assert after_replay == 2
    assert graph.attempts == 2


def _visible(client, name: str) -> int:
    url = client.get_queue_url(QueueName=name)["QueueUrl"]
    attributes = client.get_queue_attributes(
        QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
    )
    return int(attributes["Attributes"]["ApproximateNumberOfMessages"])
