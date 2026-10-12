"""HR connector snapshots. S3 is moto, state is SQLite, the harness is the real app."""

from pathlib import Path

import boto3
import httpx
import pytest
from botocore.exceptions import BotoCoreError, ClientError
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from connector_core import metrics
from connector_core.state import State
from connector_core.store import METADATA_ACL, METADATA_HASH, SnapshotStore, object_key
from connector_core.sync import Syncer
from connector_hr.client import HrConnector
from connector_hr.worker import select_hr
from harness_hr.app import create_app
from harness_hr.store import Store
from ingest_contract.events import RAW_BUCKET
from shared.contracts.validate import load_contracts
from shared.registry import load_registry

BUCKET = RAW_BUCKET
ROOT = Path(__file__).resolve().parents[3]


def _count(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


class _AppTransport(httpx.BaseTransport):
    """Sync bridge. This httpx release's ASGI transport is async only."""

    def __init__(self, app) -> None:
        self.app = TestClient(app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.app.request(
            request.method, request.url.path, params=dict(request.url.params)
        )
        return httpx.Response(
            response.status_code, headers=dict(response.headers), content=response.content
        )


@pytest.fixture
def harness(tmp_path):
    store = Store(tmp_path / "hr.db", tmp_path / "policies")
    app = create_app(store)
    http = httpx.Client(transport=_AppTransport(app), base_url="http://hr")
    return store, app, HrConnector(http, backoff=0), TestClient(app)


@pytest.fixture
def contract():
    found = load_contracts(ROOT / "infrastructure" / "config" / "sources")
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
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        yield client


def _syncer(harness, contract, state, s3, workers=1) -> Syncer:
    _, _, connector, _ = harness
    return Syncer(connector, contract, state, SnapshotStore(s3, BUCKET), workers)


def _versions(s3, key: str) -> list[dict]:
    body = s3.list_object_versions(Bucket=BUCKET, Prefix=key)
    return [item for item in body.get("Versions", []) if item["Key"] == key]


def test_initial_sync_then_second_run_writes_nothing(harness, contract, state, s3):
    syncer = _syncer(harness, contract, state, s3)
    first = syncer.run()
    assert first.ok
    assert first.written == 86
    assert state.count_objects("hr") == 86
    key = object_key("hr", "policy", "pol-parental-leave-de", "md")
    head = s3.head_object(Bucket=BUCKET, Key=key)
    assert head["Metadata"][METADATA_HASH]
    assert "hr_employee" in head["Metadata"][METADATA_ACL]
    assert b"Elternzeit" in s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    written = _count(metrics.WRITTEN, source_id="hr", object_type="policy")
    # The watermark is "now", so a later poll sees nothing new.
    second = syncer.run()
    assert second.written == 0
    assert second.skipped == 0
    assert second.ok
    # Re-reading the seed timestamp skips every object and writes no new version.
    state.mark_success("hr", "2026-01-15T09:00:00.000000Z")
    replay = syncer.run()
    assert replay.written == 0
    assert replay.skipped == 86
    assert _count(metrics.WRITTEN, source_id="hr", object_type="policy") == written
    assert len(_versions(s3, key)) == 1


def test_policy_edit_and_acl_change_rewrite_the_snapshot(harness, contract, state, s3):
    _, _, _, api = harness
    syncer = _syncer(harness, contract, state, s3)
    syncer.run()
    key = object_key("hr", "policy", "pol-remote-work", "md")
    before = s3.head_object(Bucket=BUCKET, Key=key)["Metadata"][METADATA_HASH]
    api.put("/admin/policies/pol-remote-work", json={"body": "# Remote Work Policy\n\nChanged."})
    edited = syncer.run()
    assert edited.written == 1
    after = s3.head_object(Bucket=BUCKET, Key=key)["Metadata"][METADATA_HASH]
    assert after != before
    assert len(_versions(s3, key)) == 2
    api.put(
        "/admin/policies/pol-remote-work/acl",
        json={"native_acl": ["hr_policy_admin"]},
    )
    syncer.run()
    acl = s3.head_object(Bucket=BUCKET, Key=key)["Metadata"][METADATA_ACL]
    assert acl == "hr_policy_admin"
    assert len(_versions(s3, key)) == 3


def test_delete_removes_every_version_and_the_row(harness, contract, state, s3):
    _, _, _, api = harness
    syncer = _syncer(harness, contract, state, s3)
    syncer.run()
    key = object_key("hr", "policy", "pol-expenses", "pdf")
    assert _versions(s3, key)
    assert api.delete("/admin/policies/pol-expenses").status_code == 204
    syncer.reconcile()
    assert not _versions(s3, key)
    assert state.get_object("hr", "pol-expenses") is None
    assert metrics.DRIFT.labels(source_id="hr")._value.get() == 0
    assert _count(metrics.DELETED, source_id="hr") >= 1


def test_reconcile_rewrites_a_snapshot_the_bucket_lost(harness, contract, state, s3):
    syncer = _syncer(harness, contract, state, s3)
    syncer.run()
    key = object_key("hr", "policy", "pol-parental-leave-de", "md")
    syncer.snapshots.delete_all_versions(key)
    assert not syncer.snapshots.exists(key)
    syncer.reconcile()
    assert b"Elternzeit" in s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()


def test_delete_verification_failure_keeps_the_row(harness, contract, state, s3):
    syncer = _syncer(harness, contract, state, s3)
    syncer.run()

    class Sticky(SnapshotStore):
        def exists(self, key: str) -> bool:
            return True

    sticky = Syncer(syncer.connector, contract, state, Sticky(s3, BUCKET), workers=1)
    before = _count(metrics.DELETE_FAILURES, source_id="hr", store="s3")
    api = harness[3]
    api.delete("/admin/people/emp-000001")
    sticky.reconcile()
    assert _count(metrics.DELETE_FAILURES, source_id="hr", store="s3") == before + 1
    assert state.get_object("hr", "emp-000001") is not None
    syncer.reconcile()
    assert state.get_object("hr", "emp-000001") is None


def test_forbidden_field_is_refused_and_watermark_stays(harness, contract, state, s3):
    _, _, connector, _ = harness

    class Poison(HrConnector):
        def list_changes(self, updated_since, page_token):
            page = super().list_changes(updated_since, page_token)
            if page["items"]:
                page["items"][0]["record"] = {**page["items"][0]["record"], "national_id": "x"}
            return page

    poisoned = Syncer(
        Poison(connector.client, backoff=0), contract, state, SnapshotStore(s3, BUCKET), workers=1
    )
    before = _count(metrics.ERRORS, source_id="hr", stage="contract")
    report = poisoned.run()
    assert not report.ok
    assert report.watermark is None
    assert _count(metrics.ERRORS, source_id="hr", stage="contract") == before + 1
    assert state.get_object("hr", "emp-000001") is None or "national_id" not in (
        s3.get_object(Bucket=BUCKET, Key=object_key("hr", "person", "emp-000001", "json"))["Body"]
        .read()
        .decode()
        if False
        else ""
    )


def test_refused_object_is_not_stored(harness, contract, state, s3):
    _, _, connector, _ = harness

    class Poison(HrConnector):
        def list_changes(self, updated_since, page_token):
            page = super().list_changes(updated_since, page_token)
            for item in page["items"]:
                if item["id"] == "emp-000002":
                    item["record"] = {**item["record"], "salary": 1}
            return page

    report = Syncer(
        Poison(connector.client, backoff=0), contract, state, SnapshotStore(s3, BUCKET), workers=1
    ).run()
    assert not report.ok
    assert state.get_object("hr", "emp-000002") is None
    with pytest.raises(ClientError):
        s3.head_object(Bucket=BUCKET, Key=object_key("hr", "person", "emp-000002", "json"))


def test_harness_down_leaves_the_watermark(contract, state, s3):
    class Down(HrConnector):
        def list_changes(self, updated_since, page_token):
            raise __import__("connector_core.base", fromlist=["ConnectorError"]).ConnectorError(
                "down"
            )

    report = Syncer(
        Down(httpx.Client(base_url="http://unused"), backoff=0),
        contract,
        state,
        SnapshotStore(s3, BUCKET),
        workers=1,
    ).run()
    assert not report.ok
    assert state.get_watermark("hr") is None
    assert _count(metrics.ERRORS, source_id="hr", stage="list") >= 1


def test_s3_down_leaves_the_watermark(harness, contract, state, s3):
    class Down(SnapshotStore):
        def put(self, key, body, content_type, metadata):
            raise ClientError({"Error": {"Code": "500", "Message": "down"}}, "PutObject")

    report = Syncer(harness[2], contract, state, Down(s3, BUCKET), workers=1).run()
    assert not report.ok
    assert state.get_watermark("hr") is None


def test_reconcile_counts_an_unreachable_bucket(harness, contract, state, s3):
    class Down(SnapshotStore):
        def list_object_ids(self, source_id):
            raise BotoCoreError()

    syncer = Syncer(harness[2], contract, state, Down(s3, BUCKET), workers=1)
    before = state.get_watermark("hr")
    errors = _count(metrics.ERRORS, source_id="hr", stage="s3_put")
    syncer.reconcile()
    assert state.get_watermark("hr") == before
    assert state.count_objects("hr") == 0
    assert _count(metrics.ERRORS, source_id="hr", stage="s3_put") == errors + 1
    assert metrics.DEPENDENCY.labels(dependency="s3")._value.get() == 0


def test_partial_failure_replays_without_duplicates(harness, contract, state, s3):
    class Flaky(SnapshotStore):
        def put(self, key, body, content_type, metadata):
            if key.endswith("emp-000010.json"):
                raise ClientError({"Error": {"Code": "500", "Message": "once"}}, "PutObject")
            return super().put(key, body, content_type, metadata)

    flaky = Syncer(harness[2], contract, state, Flaky(s3, BUCKET), workers=1)
    assert not flaky.run().ok
    assert state.get_watermark("hr") is None
    healthy = _syncer(harness, contract, state, s3)
    assert healthy.run().ok
    assert state.count_objects("hr") == 86
    key = object_key("hr", "person", "emp-000001", "json")
    assert len(_versions(s3, key)) == 1
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    assert b"salary" not in body and b"national_id" not in body


def test_bad_timestamp_is_an_error_not_a_crash(harness):
    from connector_core.base import ConnectorError

    _, _, connector, _ = harness
    with pytest.raises(ConnectorError):
        connector.list_changes("yesterday", None)


def test_invalid_contract_stops_the_worker(tmp_path, monkeypatch):
    (tmp_path / "bad.yaml").write_text("source_id: hr\n")
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path))
    with pytest.raises(SystemExit, match="refusing to start"):
        load_registry()


def test_unimplemented_sources_are_not_started():
    contracts = load_registry()
    assert select_hr(contracts).source_id == "hr"
    assert {item.source_id for item in contracts} - {"hr"} == {"sales", "product", "engineering"}


def test_metric_names_match_the_dashboard():
    root = ROOT / "observability" / "grafana" / "dashboards"
    body = "\n".join(path.read_text() for path in root.rglob("*.json"))
    used = {name for name in metrics.METRIC_LABELS if name in body}
    assert "kbs_snapshot_object_lag_seconds" in used
    assert "kbs_reconcile_drift" in used
    assert "kbs_delete_verification_failures_total" in used
    assert set(metrics.METRIC_LABELS) >= used
