"""Product snapshots. S3 is moto, state is SQLite, the harness is the real app."""

from pathlib import Path

import boto3
import httpx
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from connector_core.state import State
from connector_core.store import METADATA_ACL, SnapshotStore, object_key
from connector_core.sync import Syncer
from connector_product.apply import apply_notice
from connector_product.client import ProductConnector
from connector_product.queue import HOOK_DEPTH, HookQueue
from harness_product.app import create_app
from harness_product.notify import Notifier
from harness_product.store import Store
from ingest_contract.events import RAW_BUCKET
from ingest_contract.hooks import HOOK_DLQ, HOOK_QUEUE, HookNotice
from shared.contracts.validate import load_contracts

BUCKET = RAW_BUCKET
ROOT = Path(__file__).resolve().parents[4]
SPEC = "spec-checkout"


class _AppTransport(httpx.BaseTransport):
    def __init__(self, app) -> None:
        self.app = TestClient(app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.app.request(
            request.method, request.url.path, params=dict(request.url.params)
        )
        return httpx.Response(
            response.status_code, headers=dict(response.headers), content=response.content
        )


class RecordingPublisher:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


@pytest.fixture
def harness(tmp_path):
    store = Store(tmp_path / "product.db", tmp_path / "specs")
    app = create_app(store, notifier=Notifier(None))
    http = httpx.Client(transport=_AppTransport(app), base_url="http://product")
    return ProductConnector(http, backoff=0), TestClient(app)


@pytest.fixture
def contract():
    found = load_contracts(ROOT / "infrastructure" / "config" / "sources")
    return next(item for item in found if item.source_id == "product")


@pytest.fixture
def state():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    stored = State(engine)
    stored.create_tables()
    return stored


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        yield client


def _apply(harness, contract, state, s3, notice, publisher=None):
    connector, _api = harness
    return apply_notice(connector, contract, state, SnapshotStore(s3, BUCKET), publisher, notice)


def _notice(event_type: str, object_id: str = SPEC) -> HookNotice:
    return HookNotice(source_id="product", object_id=object_id, event_type=event_type)


def test_hook_writes_markdown_and_a_repeat_is_a_noop(harness, contract, state, s3):
    key = object_key("product", "spec", SPEC, "md")
    first = _apply(harness, contract, state, s3, _notice("create"))
    assert first.ok
    assert first.written == 1
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    assert body.startswith(b"# Checkout")
    assert b"epic-checkout-retry" in body
    assert b"salary" not in body
    assert "product_org" in s3.head_object(Bucket=BUCKET, Key=key)["Metadata"][METADATA_ACL]
    second = _apply(harness, contract, state, s3, _notice("update"))
    assert second.ok
    assert second.written == 0
    assert second.skipped == 1
    versions = s3.list_object_versions(Bucket=BUCKET, Prefix=key)["Versions"]
    assert len(versions) == 1


def test_edit_hook_rewrites_the_snapshot(harness, contract, state, s3):
    _apply(harness, contract, state, s3, _notice("create"))
    _connector, api = harness
    edited = api.put(
        f"/admin/specs/{SPEC}",
        json={"body": "# Checkout\n\n## Card payments\n\nChanged retry copy.\n"},
    )
    assert edited.status_code == 200
    report = _apply(harness, contract, state, s3, _notice("update"))
    assert report.written == 1
    key = object_key("product", "spec", SPEC, "md")
    assert b"Changed retry copy." in s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()


def test_delete_hook_removes_the_object_before_the_event(harness, contract, state, s3):
    connector, api = harness
    publisher = RecordingPublisher()
    snapshots = SnapshotStore(s3, BUCKET)
    apply_notice(connector, contract, state, snapshots, publisher, _notice("create"))
    api.delete(f"/admin/specs/{SPEC}")
    key = object_key("product", "spec", SPEC, "md")

    def publish(event) -> None:
        if event.event_type.value == "delete":
            assert event.object_id == SPEC
            assert not snapshots.exists(key)
        publisher.events.append(event)

    publisher.publish = publish
    report = apply_notice(connector, contract, state, snapshots, publisher, _notice("delete"))
    assert report.ok
    assert state.get_object("product", SPEC) is None
    assert not snapshots.exists(key)


def test_reconcile_recovers_a_spec_with_no_hook(harness, contract, state, s3):
    connector, _api = harness
    syncer = Syncer(connector, contract, state, SnapshotStore(s3, BUCKET), 1)
    syncer.reconcile()
    assert state.count_objects("product") == 12
    key = object_key("product", "spec", "spec-billing", "md")
    assert b"# Billing" in s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()


def test_update_for_a_deleted_spec_removes_the_snapshot(harness, contract, state, s3):
    connector, api = harness
    snapshots = SnapshotStore(s3, BUCKET)
    apply_notice(connector, contract, state, snapshots, None, _notice("create"))
    api.delete(f"/admin/specs/{SPEC}")
    report = apply_notice(connector, contract, state, snapshots, None, _notice("update"))
    assert report.ok
    assert state.get_object("product", SPEC) is None
    assert not snapshots.exists(object_key("product", "spec", SPEC, "md"))


def test_invalid_queue_body_is_not_returned(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with mock_aws():
        client = boto3.client("sqs", region_name="us-east-1")
        url = client.create_queue(
            QueueName=HOOK_QUEUE,
            Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "false"},
        )["QueueUrl"]
        client.send_message(
            QueueUrl=url,
            MessageBody='{"not": "a notice"}',
            MessageGroupId="product:bad",
            MessageDeduplicationId="bad-1",
        )
        hooks = HookQueue(client)
        assert hooks.ready()
        assert hooks.receive(wait=0) == []
        depth = client.get_queue_attributes(
            QueueUrl=url, AttributeNames=["ApproximateNumberOfMessagesNotVisible"]
        )["Attributes"]["ApproximateNumberOfMessagesNotVisible"]
        assert depth == "1"


def test_hook_depth_counts_visible_messages(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with mock_aws():
        client = boto3.client("sqs", region_name="us-east-1")
        attributes = {"FifoQueue": "true", "ContentBasedDeduplication": "false"}
        url = client.create_queue(QueueName=HOOK_QUEUE, Attributes=attributes)["QueueUrl"]
        client.create_queue(QueueName=HOOK_DLQ, Attributes=attributes)
        client.send_message(
            QueueUrl=url,
            MessageBody='{"source_id":"product","object_id":"spec-checkout","event_type":"update"}',
            MessageGroupId="product:spec-checkout",
            MessageDeduplicationId="depth-1",
        )
        hooks = HookQueue(client)
        assert hooks.ready()
        hooks.observe()
        assert HOOK_DEPTH.labels(queue=HOOK_QUEUE)._value.get() == 1
        assert HOOK_DEPTH.labels(queue=HOOK_DLQ)._value.get() == 0
