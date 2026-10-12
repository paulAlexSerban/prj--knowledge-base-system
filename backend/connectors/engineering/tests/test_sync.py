"""Engineering snapshots. S3 is moto, state is SQLite, the harness is the real app."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import httpx
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from connector_core.base import ConnectorError
from connector_core.http import HarnessClient
from connector_core.state import State
from connector_core.store import METADATA_ACL, SnapshotStore, object_key
from connector_core.sync import Syncer
from connector_engineering.client import EngineeringConnector
from harness_engineering.app import create_app
from harness_engineering.store import Store
from ingest_contract.events import RAW_BUCKET
from shared.contracts.validate import load_contracts

BUCKET = RAW_BUCKET
ROOT = Path(__file__).resolve().parents[4]


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


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
    store = Store(tmp_path / "eng.db", clock=Clock())
    app = create_app(store)
    http = httpx.Client(transport=_AppTransport(app), base_url="http://engineering")
    return EngineeringConnector(http, backoff=0), TestClient(app)


@pytest.fixture
def contract():
    found = load_contracts(ROOT / "infrastructure" / "config" / "sources")
    return next(item for item in found if item.source_id == "engineering")


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


def _syncer(harness, contract, state, s3, publisher=None) -> Syncer:
    connector, _api = harness
    return Syncer(connector, contract, state, SnapshotStore(s3, BUCKET), 1, publisher)


def test_initial_sync_then_second_run_writes_nothing(harness, contract, state, s3):
    syncer = _syncer(harness, contract, state, s3)
    first = syncer.run()
    assert first.ok
    assert first.written == 25
    assert state.count_objects("engineering") == 25
    key = object_key("engineering", "epic", "epic-checkout-retry", "json")
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    assert b"epic-checkout-retry" in body
    assert b"salary" not in body
    head = s3.head_object(Bucket=BUCKET, Key=key)
    assert "project_member" in head["Metadata"][METADATA_ACL]
    second = syncer.run()
    assert second.ok
    assert second.written == 0


def test_edit_and_acl_change_rewrite_the_snapshot(harness, contract, state, s3):
    _connector, api = harness
    syncer = _syncer(harness, contract, state, s3)
    syncer.run()
    key = object_key("engineering", "epic", "epic-checkout-retry", "json")
    before = s3.head_object(Bucket=BUCKET, Key=key)["Metadata"][METADATA_ACL]
    api.patch("/admin/epics/epic-checkout-retry", json={"status": "released"})
    edited = syncer.run()
    assert edited.written == 1
    assert b"released" in s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    api.put(
        "/admin/epics/epic-checkout-retry/acl",
        json={"native_acl": ["eng_leadership"]},
    )
    changed = syncer.run()
    assert changed.written == 1
    after = s3.head_object(Bucket=BUCKET, Key=key)["Metadata"][METADATA_ACL]
    assert after != before
    assert "eng_leadership" in after


def test_delete_removes_the_object_before_the_event(harness, contract, state, s3):
    _connector, api = harness
    publisher = RecordingPublisher()
    snapshots = SnapshotStore(s3, BUCKET)
    connector, _api = harness
    syncer = Syncer(connector, contract, state, snapshots, 1, publisher)
    syncer.run()
    key = object_key("engineering", "epic", "epic-platform-cost", "json")
    api.delete("/admin/epics/epic-platform-cost")

    def publish(event) -> None:
        if event.event_type.value == "delete":
            assert not snapshots.exists(key)
        publisher.events.append(event)

    publisher.publish = publish
    syncer.reconcile()
    assert state.get_object("engineering", "epic-platform-cost") is None
    assert not snapshots.exists(key)
    assert any(event.event_type.value == "delete" for event in publisher.events)


def test_a_4xx_is_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, text="missing")

    client = HarnessClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://engineering"),
        backoff=0,
    )
    with pytest.raises(ConnectorError, match="404"):
        client.get("/epics/missing")
    assert calls["n"] == 1
