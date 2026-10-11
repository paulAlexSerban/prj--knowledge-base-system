import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from harness_engineering.catalog import QUESTION_EPICS
from harness_sales.app import create_app
from harness_sales.catalog import HIGH_ARR, QUESTION_DEALS
from harness_sales.store import Store

FORBIDDEN = {"salary", "compensation", "ssn", "national_id", "commission_rate"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def harness(tmp_path):
    path = tmp_path / "sales.db"
    store = Store(path, clock=Clock())
    return TestClient(create_app(store)), path


def drain(client: TestClient, cursor: str = "0", limit: int = 100) -> tuple[list[dict], str]:
    items: list[dict] = []
    while True:
        body = client.get("/changes", params={"cursor": cursor, "limit": limit}).json()
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if not body["more"]:
            return items, cursor


def _keys(node: object) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {key for value in node.values() for key in _keys(value)}
    if isinstance(node, list):
        return {key for value in node for key in _keys(value)}
    return set()


def test_seed_is_forty_deals(harness):
    client, _path = harness
    body = client.get("/discover").json()
    assert body["counts"] == {"deal": 40}
    assert body["capabilities"] == ["cursor_feed"]


def test_question_deals_are_high_arr_q3_on_unreleased_epics(harness):
    client, _path = harness
    by_id = {item["deal_id"]: item for item in client.get("/deals").json()["items"]}
    for deal_id in QUESTION_DEALS:
        deal = by_id[deal_id]
        assert deal["arr"] >= HIGH_ARR
        assert deal["quarter"] == "2026-Q3"
        assert deal["epic_id"] in QUESTION_EPICS
    # Released epic, no epic, wrong quarter, and low ARR must not join that set.
    assert by_id["deal-woodgrove-q3"]["epic_id"] == "epic-checkout-wallets"
    assert by_id["deal-proseware-q3"]["epic_id"] is None
    assert by_id["deal-coho-q2"]["quarter"] == "2026-Q2"
    assert by_id["deal-lucerne-q4"]["quarter"] == "2026-Q4"
    assert by_id["deal-graphic-q3"]["arr"] < HIGH_ARR
    assert by_id["deal-contoso-q3"]["native_acl"] == ["sales_leadership"]
    assert by_id["deal-tailspin-q3"]["native_acl"] == ["sales_leadership"]


def test_cursor_resumes_without_gaps_or_duplicates(harness):
    client, _path = harness
    full, tip = drain(client, limit=100)
    paged, paged_tip = drain(client, limit=3)
    assert [item["id"] for item in paged] == [item["id"] for item in full]
    assert len(full) == 40
    assert len({item["id"] for item in full}) == 40
    assert all(item["op"] == "upsert" for item in full)
    assert tip == paged_tip
    again, same = drain(client, cursor=tip)
    assert again == []
    assert same == tip


def test_delete_is_an_event_and_commission_is_never_returned(harness):
    client, path = harness
    _items, tip = drain(client)
    deal_id = "deal-proseware-q3"
    assert client.delete(f"/admin/deals/{deal_id}").status_code == 204
    page = client.get("/changes", params={"cursor": tip}).json()
    assert len(page["items"]) == 1
    assert page["items"][0]["op"] == "delete"
    assert page["items"][0]["id"] == deal_id
    assert "record" not in page["items"][0]
    ids = {row["id"] for row in client.get("/ids").json()["ids"]["deal"]}
    assert deal_id not in ids
    assert client.get(f"/deals/{deal_id}").status_code == 404
    for route in ("/discover", "/changes", "/ids", "/deals", "/deals/deal-northwind-q3/acl"):
        assert not _keys(client.get(route).json()) & FORBIDDEN, route
    with sqlite3.connect(path) as connection:
        rate = connection.execute(
            "SELECT commission_rate FROM deals WHERE deal_id = 'deal-northwind-q3'"
        ).fetchone()
    assert rate is not None and rate[0] > 0


def test_edit_and_acl_change_are_upserts(harness):
    client, _path = harness
    deal_id = "deal-northwind-q3"
    before = client.get(f"/deals/{deal_id}").json()
    _items, tip = drain(client)
    edited = client.patch(f"/admin/deals/{deal_id}", json={"arr": 500_000}).json()
    assert edited["version"] == before["version"] + 1
    page = client.get("/changes", params={"cursor": tip}).json()
    assert page["items"][0]["op"] == "upsert"
    assert page["items"][0]["record"]["arr"] == 500_000
    tip = page["next_cursor"]
    closed = client.put(
        f"/admin/deals/{deal_id}/acl", json={"native_acl": ["sales_leadership"]}
    ).json()
    assert closed["version"] == edited["version"]
    acl_page = client.get("/changes", params={"cursor": tip}).json()
    assert acl_page["items"][0]["record"]["native_acl"] == ["sales_leadership"]
    assert acl_page["items"][0]["version"] == edited["version"]


def test_invalid_cursor_stage_epic_and_acl_are_rejected(harness):
    client, _path = harness
    assert client.get("/changes", params={"cursor": "abc"}).status_code == 422
    assert client.get("/changes", params={"cursor": "-1"}).status_code == 422
    deal_id = "deal-northwind-q3"
    assert client.patch(f"/admin/deals/{deal_id}", json={"stage": "closed"}).status_code == 422
    assert client.patch(f"/admin/deals/{deal_id}", json={"quarter": "Q3"}).status_code == 422
    assert client.patch(f"/admin/deals/{deal_id}", json={"epic_id": "EPIC-1"}).status_code == 422
    assert client.patch(f"/admin/deals/{deal_id}", json={"arr": -1}).status_code == 422
    assert client.put(f"/admin/deals/{deal_id}/acl", json={"native_acl": []}).status_code == 422
    created = {
        "deal_id": "deal-extra-one",
        "account_id": "acc-extra",
        "account_name": "Extra",
        "arr": 10,
        "quarter": "2026-Q3",
        "stage": "prospect",
        "owner_email": "ada@sales.example",
        "product_key": "checkout",
    }
    assert client.post("/admin/deals", json=created).status_code == 201
    assert client.post("/admin/deals", json=created).status_code == 409


def test_reset_appends_a_fresh_seed_after_the_old_cursor(harness):
    client, _path = harness
    _items, tip = drain(client)
    body = client.post("/admin/reset").json()
    assert body["counts"] == {"deal": 40}
    again, _cursor = drain(client, cursor=tip)
    assert len(again) == 40
    assert all(item["op"] == "upsert" for item in again)
    assert client.get("/deals/deal-northwind-q3").status_code == 200
