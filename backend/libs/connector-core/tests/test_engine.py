"""Sync engine against a fake connector. S3 is moto and state is SQLite."""

from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from connector_core import metrics
from connector_core.base import ConnectorError, FetchResult, SourceConnector
from connector_core.state import State
from connector_core.store import SnapshotStore, object_key
from connector_core.sync import Syncer
from ingest_contract.events import RAW_BUCKET
from shared.contracts.validate import load_contracts

BUCKET = RAW_BUCKET


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "infrastructure" / "config" / "sources").is_dir():
            return parent
    raise RuntimeError("repository root not found")


class FakeConnector(SourceConnector):
    source_id = "hr"

    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.fetches = 0

    def discover(self) -> dict:
        return {}

    def list_changes(self, updated_since: str | None, page_token: str | None) -> dict:
        return {
            "items": self.items,
            "server_time": "2026-10-11T12:00:00Z",
            "next_page_token": None,
        }

    def fetch(self, object_type: str, object_id: str) -> FetchResult:
        self.fetches += 1
        raise ConnectorError("fetch is not used for this person")

    def get_acl(self, object_type: str, object_id: str) -> list[str]:
        return ["hr_employee"]

    def list_ids(self) -> dict:
        return {"ids": {}}

    def snapshot_body(self, item: dict) -> tuple[bytes, str, str]:
        return b'{"person_id":"emp-1"}', "application/json", "json"


def _item(**extra: object) -> dict:
    record = {
        "person_id": "emp-1",
        "version": 1,
        "updated_at": "2026-01-15T09:00:00.000000Z",
        "native_acl": ["hr_employee"],
        "full_name": "Ada",
    }
    record.update(extra)
    return {"object_type": "person", "id": "emp-1", "record": record}


@pytest.fixture
def contract():
    found = load_contracts(_repo_root() / "infrastructure" / "config" / "sources")
    return next(item for item in found if item.source_id == "hr")


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
        client.put_bucket_versioning(
            Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"}
        )
        yield client


def test_sync_writes_once_and_skips_the_same_version(contract, state, s3) -> None:
    connector = FakeConnector([_item()])
    syncer = Syncer(connector, contract, state, SnapshotStore(s3, BUCKET), workers=1)
    first = syncer.run()
    assert first.ok
    assert first.written == 1
    assert connector.fetches == 0
    state.mark_success("hr", "2026-01-15T09:00:00.000000Z")
    second = syncer.run()
    assert second.skipped == 1
    assert second.written == 0
    key = object_key("hr", "person", "emp-1", "json")
    versions = s3.list_object_versions(Bucket=BUCKET, Prefix=key)["Versions"]
    assert len([item for item in versions if item["Key"] == key]) == 1


def test_excluded_field_is_refused(contract, state, s3) -> None:
    syncer = Syncer(
        FakeConnector([_item(salary="hidden")]),
        contract,
        state,
        SnapshotStore(s3, BUCKET),
        workers=1,
    )
    report = syncer.run()
    assert not report.ok
    assert state.count_objects("hr") == 0
    assert state.get_watermark("hr") is None


def test_s3_error_leaves_the_watermark(contract, state, s3) -> None:
    class Down(SnapshotStore):
        def put(self, key, body, content_type, metadata):
            raise ClientError({"Error": {"Code": "500", "Message": "down"}}, "PutObject")

    report = Syncer(
        FakeConnector([_item()]), contract, state, Down(s3, BUCKET), workers=1
    ).run()
    assert not report.ok
    assert state.get_watermark("hr") is None


class RecordingPublisher:
    def __init__(self) -> None:
        self.events = []
        self.seen_state = []

    def publish(self, event) -> None:
        self.events.append(event)


class BoomPublisher:
    def publish(self, event) -> None:
        raise ClientError({"Error": {"Code": "500", "Message": "queue down"}}, "SendMessage")


def test_publish_happens_before_the_state_row(contract, state, s3) -> None:
    publisher = RecordingPublisher()

    def publish(event) -> None:
        publisher.events.append(event)
        assert state.get_object("hr", "emp-1") is None

    publisher.publish = publish
    syncer = Syncer(
        FakeConnector([_item()]),
        contract,
        state,
        SnapshotStore(s3, BUCKET),
        workers=1,
        publisher=publisher,
    )
    report = syncer.run()
    assert report.ok
    assert publisher.events[0].event_type.value == "create"
    assert publisher.events[0].s3_pointer == object_key("hr", "person", "emp-1", "json")
    assert state.get_object("hr", "emp-1") is not None
    assert metrics.PUBLISHED.labels(source_id="hr", event_type="create")._value.get() >= 1


def test_acl_change_and_update_use_different_event_types(contract, state, s3) -> None:
    publisher = RecordingPublisher()
    connector = FakeConnector([_item()])
    syncer = Syncer(
        connector, contract, state, SnapshotStore(s3, BUCKET), workers=1, publisher=publisher
    )
    syncer.run()
    connector.items = [_item(native_acl=["hr_policy_admin"])]
    syncer.run()
    connector.items = [_item(native_acl=["hr_policy_admin"], version=2)]
    syncer.run()
    kinds = [event.event_type.value for event in publisher.events]
    assert kinds == ["create", "acl_change", "update"]


def test_enqueue_failure_does_not_advance_state(contract, state, s3) -> None:
    report = Syncer(
        FakeConnector([_item()]),
        contract,
        state,
        SnapshotStore(s3, BUCKET),
        workers=1,
        publisher=BoomPublisher(),
    ).run()
    assert not report.ok
    assert state.count_objects("hr") == 0
    assert state.get_watermark("hr") is None
    assert metrics.ERRORS.labels(source_id="hr", stage="enqueue")._value.get() >= 1


def test_delete_is_published_after_the_key_is_gone(contract, state, s3) -> None:
    publisher = RecordingPublisher()
    snapshots = SnapshotStore(s3, BUCKET)
    syncer = Syncer(
        FakeConnector([_item()]), contract, state, snapshots, workers=1, publisher=publisher
    )
    syncer.run()
    key = object_key("hr", "person", "emp-1", "json")

    def publish(event) -> None:
        assert event.event_type.value == "delete"
        assert not snapshots.exists(key)
        assert state.get_object("hr", "emp-1") is not None
        publisher.events.append(event)

    publisher.publish = publish
    syncer.reconcile()
    assert state.get_object("hr", "emp-1") is None
    assert any(event.event_type.value == "delete" for event in publisher.events)


def test_delete_enqueue_failure_keeps_the_row(contract, state, s3) -> None:
    publisher = RecordingPublisher()
    syncer = Syncer(
        FakeConnector([_item()]),
        contract,
        state,
        SnapshotStore(s3, BUCKET),
        workers=1,
        publisher=publisher,
    )
    syncer.run()
    syncer.publisher = BoomPublisher()
    syncer.reconcile()
    assert state.get_object("hr", "emp-1") is not None


def test_unreachable_bucket_is_an_error_and_deletes_nothing(contract, state, s3) -> None:
    class Down(SnapshotStore):
        def list_object_ids(self, source_id):
            raise ClientError({"Error": {"Code": "500", "Message": "down"}}, "ListObjects")

    before = metrics.ERRORS.labels(source_id="hr", stage="s3_put")._value.get()
    Syncer(FakeConnector([]), contract, state, Down(s3, BUCKET), workers=1).reconcile()
    assert state.count_objects("hr") == 0
    assert metrics.ERRORS.labels(source_id="hr", stage="s3_put")._value.get() == before + 1
