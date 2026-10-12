from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from harness_engineering.app import create_app
from harness_engineering.catalog import QUESTION_EPICS, SEED_AT
from harness_engineering.store import Store

FORBIDDEN = {"salary", "compensation", "ssn", "national_id", "commission_rate"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def client(tmp_path) -> TestClient:
    store = Store(tmp_path / "eng.db", clock=Clock())
    return TestClient(create_app(store))


def all_changes(client: TestClient, since: str | None = None, limit: int = 100) -> list[dict]:
    items: list[dict] = []
    token = None
    while True:
        params: dict[str, str | int] = {"limit": limit}
        if since:
            params["updated_since"] = since
        if token:
            params["page_token"] = token
        body = client.get("/changes", params=params).json()
        items += body["items"]
        token = body["next_page_token"]
        if not token:
            return items


def _keys(node: object) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {key for value in node.values() for key in _keys(value)}
    if isinstance(node, list):
        return {key for value in node for key in _keys(value)}
    return set()


def test_seed_loads_epics(client):
    body = client.get("/discover").json()
    assert body["counts"] == {"epic": 25}
    assert body["capabilities"] == ["updated_since"]
    assert client.get("/health").json()["capability"] == "updated_since"


def test_question_epics_are_unreleased(client):
    by_id = {item["epic_id"]: item for item in client.get("/epics").json()["items"]}
    for epic_id in QUESTION_EPICS:
        assert by_id[epic_id]["status"] in {"planned", "in_progress"}
    assert by_id["epic-checkout-wallets"]["status"] == "released"
    assert by_id["epic-checkout-retry"]["native_acl"] == ["project_member", "roadmap_readers"]
    assert by_id["epic-identity-sso"]["native_acl"] == ["eng_leadership"]
    assert by_id["epic-platform-cost"]["native_acl"] == ["eng_leadership"]
    assert by_id["epic-billing-usage"]["native_acl"] == ["eng_leadership"]
    assert "roadmap_readers" in client.get("/discover").json()["native_acls"]


def test_changes_page_a_shared_timestamp_without_loss(client):
    small = all_changes(client, since=SEED_AT, limit=4)
    big = all_changes(client, since=SEED_AT, limit=500)
    assert [item["id"] for item in small] == [item["id"] for item in big]
    assert len(small) == 25
    assert len({item["id"] for item in small}) == 25
    # One microsecond later excludes the seed. The bound is inclusive.
    assert all_changes(client, since="2026-01-15T09:00:00.000001Z") == []


def test_change_items_carry_the_fields_the_connector_reads(client):
    item = all_changes(client, limit=1)[0]
    assert item["object_type"] == "epic"
    assert item["record"]["version"] == item["version"]
    assert item["record"]["updated_at"] == item["updated_at"]
    assert item["record"]["native_acl"]


def test_no_read_path_returns_forbidden_fields(client):
    epic_id = "epic-checkout-retry"
    for path in (
        "/discover",
        "/changes",
        "/ids",
        "/epics",
        f"/epics/{epic_id}",
        f"/epics/{epic_id}/acl",
    ):
        assert not _keys(client.get(path).json()) & FORBIDDEN, path


def test_status_change_bumps_version_and_acl_change_does_not(client):
    epic_id = "epic-checkout-retry"
    before = client.get(f"/epics/{epic_id}").json()
    edited = client.patch(f"/admin/epics/{epic_id}", json={"status": "released"}).json()
    assert edited["version"] == before["version"] + 1
    assert edited["status"] == "released"
    closed = client.put(
        f"/admin/epics/{epic_id}/acl", json={"native_acl": ["eng_leadership"]}
    ).json()
    assert closed["version"] == edited["version"]
    assert closed["native_acl"] == ["eng_leadership"]
    assert closed["updated_at"] != edited["updated_at"]
    ids = {row["id"]: row["version"] for row in client.get("/ids").json()["ids"]["epic"]}
    assert ids[epic_id] == edited["version"]


def test_delete_is_visible_only_as_a_missing_id(client):
    epic_id = "epic-platform-cost"
    assert client.delete(f"/admin/epics/{epic_id}").status_code == 204
    assert client.get(f"/epics/{epic_id}").status_code == 404
    ids = {row["id"] for row in client.get("/ids").json()["ids"]["epic"]}
    assert epic_id not in ids
    assert epic_id not in {item["id"] for item in all_changes(client, since=SEED_AT)}
    assert client.get("/discover").json()["counts"]["epic"] == 24


def test_invalid_status_team_and_acl_are_rejected(client):
    epic_id = "epic-checkout-retry"
    assert client.patch(f"/admin/epics/{epic_id}", json={"status": "done"}).status_code == 422
    assert client.patch(f"/admin/epics/{epic_id}", json={"team": "finance"}).status_code == 422
    assert client.patch(f"/admin/epics/{epic_id}", json={"product_key": "nope"}).status_code == 422
    assert (
        client.put(f"/admin/epics/{epic_id}/acl", json={"native_acl": ["hr_employee"]}).status_code
        == 422
    )
    assert client.put(f"/admin/epics/{epic_id}/acl", json={"native_acl": []}).status_code == 422
    created = client.post(
        "/admin/epics",
        json={
            "epic_id": "epic-extra-one",
            "title": "Extra",
            "status": "planned",
            "product_key": "checkout",
            "team": "payments",
        },
    )
    assert created.status_code == 201
    assert (
        client.post(
            "/admin/epics",
            json={
                "epic_id": "epic-extra-one",
                "title": "Extra",
                "status": "planned",
                "product_key": "checkout",
                "team": "payments",
            },
        ).status_code
        == 409
    )


def test_bad_timestamp_is_rejected(client):
    assert client.get("/changes", params={"updated_since": "yesterday"}).status_code == 422


def test_reset_restores_the_seed(client):
    client.delete("/admin/epics/epic-platform-cost")
    body = client.post("/admin/reset").json()
    assert body["counts"] == {"epic": 25}
    assert client.get("/epics/epic-platform-cost").status_code == 200
