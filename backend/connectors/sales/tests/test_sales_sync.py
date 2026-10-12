"""Sales snapshots. S3 is moto, state is SQLite, the harness is the real app."""

from datetime import UTC, datetime, timedelta
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
from connector_sales.client import SalesConnector
from harness_sales.app import create_app
from harness_sales.store import Store
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
    store = Store(tmp_path / "sales.db", clock=Clock())
    app = create_app(store)
    http = httpx.Client(transport=_AppTransport(app), base_url="http://sales")
    return SalesConnector(http, backoff=0), TestClient(app)


@pytest.fixture
def contract():
    found = load_contracts(ROOT / "infrastructure" / "config" / "sources")
    return next(item for item in found if item.source_id == "sales")


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
    assert first.written == 40
    assert state.count_objects("sales") == 40
    key = object_key("sales", "deal", "deal-northwind-q3", "json")
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    assert b"epic-checkout-retry" in body
    assert b"commission_rate" not in body
    assert "account_team" in s3.head_object(Bucket=BUCKET, Key=key)["Metadata"][METADATA_ACL]
    second = syncer.run()
    assert second.ok
    assert second.written == 0
    assert state.get_watermark("sales") not in ("", None)


def test_edit_and_acl_change_rewrite_the_snapshot(harness, contract, state, s3):
    _connector, api = harness
    syncer = _syncer(harness, contract, state, s3)
    syncer.run()
    deal_id = "deal-northwind-q3"
    key = object_key("sales", "deal", deal_id, "json")
    api.patch(f"/admin/deals/{deal_id}", json={"arr": 500_000})
    edited = syncer.run()
    assert edited.written == 1
    assert b"500000" in s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    api.put(f"/admin/deals/{deal_id}/acl", json={"native_acl": ["sales_leadership"]})
    changed = syncer.run()
    assert changed.written == 1
    assert "sales_leadership" in s3.head_object(Bucket=BUCKET, Key=key)["Metadata"][METADATA_ACL]


def test_feed_delete_removes_the_object_before_the_event(harness, contract, state, s3):
    connector, api = harness
    publisher = RecordingPublisher()
    snapshots = SnapshotStore(s3, BUCKET)
    syncer = Syncer(connector, contract, state, snapshots, 1, publisher)
    syncer.run()
    deal_id = "deal-proseware-q3"
    key = object_key("sales", "deal", deal_id, "json")
    api.delete(f"/admin/deals/{deal_id}")

    def publish(event) -> None:
        if event.event_type.value == "delete":
            assert event.object_id == deal_id
            assert not snapshots.exists(key)
        publisher.events.append(event)

    publisher.publish = publish
    report = syncer.run()
    assert report.ok
    assert state.get_object("sales", deal_id) is None
    assert not snapshots.exists(key)
    assert any(event.event_type.value == "delete" for event in publisher.events)
