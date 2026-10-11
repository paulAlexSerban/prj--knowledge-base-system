from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from harness_hr.app import create_app
from harness_hr.store import Store

FORBIDDEN = ("salary", "compensation", "ssn", "national_id")
KNOWN_REGIONS = {"DE", "US", "UK", "FR", "NL", "ES", "IE", "CA", "GLOBAL"}
NEW_COUNTRIES = ("fr", "nl", "es", "ie", "ca")
ADMIN_ONLY = {
    "pol-disciplinary-procedure",
    "pol-compensation-review",
    "pol-redundancy-procedure",
}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(tmp_path, clock) -> TestClient:
    store = Store(tmp_path / "hr.db", tmp_path / "policies", clock=clock)
    return TestClient(create_app(store))


def all_changes(client: TestClient, since: str | None = None, limit: int = 100) -> list[dict]:
    items, token = [], None
    while True:
        params = {"limit": limit}
        if since:
            params["updated_since"] = since
        if token:
            params["page_token"] = token
        body = client.get("/changes", params=params).json()
        items += body["items"]
        token = body["next_page_token"]
        if not token:
            return items


def test_seed_loads_people_and_policies(client):
    body = client.get("/discover").json()
    assert body["counts"] == {"person": 48, "policy": 38}
    assert body["capabilities"] == ["updated_since", "snapshot_diff"]


def test_germany_parental_leave_is_present_and_readable(client):
    record = client.get("/policies/pol-parental-leave-de").json()
    assert record["region"] == "DE" and record["native_acl"] == ["hr_employee"]
    text = client.get("/policies/pol-parental-leave-de/content").text
    assert "Elternzeit" in text and "16 weeks" in text


def test_pdf_policy_is_a_pdf_with_text(client):
    response = client.get("/policies/pol-expenses/content")
    assert response.headers["content-type"] == "application/pdf"
    assert (
        response.content.startswith(b"%PDF-1.4")
        and b"Travel and Expense Policy" in response.content
    )


def test_restricted_policies_carry_admin_acl(client):
    assert client.get("/policies/pol-disciplinary-procedure/acl").json()["native_acl"] == [
        "hr_policy_admin"
    ]


def test_curated_regions_leave_and_admin_acl(client):
    items = client.get("/policies").json()["items"]
    by_id = {item["policy_id"]: item for item in items}
    assert {item["region"] for item in items} <= KNOWN_REGIONS
    for code in NEW_COUNTRIES:
        parental = by_id[f"pol-parental-leave-{code}"]
        annual = by_id[f"pol-annual-leave-{code}"]
        assert parental["region"] == annual["region"] == code.upper()
        assert parental["native_acl"] == annual["native_acl"] == ["hr_employee"]
    admin = {item["policy_id"] for item in items if "hr_policy_admin" in item["native_acl"]}
    assert admin == ADMIN_ONLY
    assert all("hr_employee" not in by_id[policy_id]["native_acl"] for policy_id in ADMIN_ONLY)
    assert by_id["pol-grievance-procedure"]["format"] == "pdf"
    assert by_id["pol-performance-review"]["format"] == "pdf"
    people_regions = {item["region"] for item in client.get("/people").json()["items"]}
    assert {"FR", "NL", "ES", "IE", "CA"} <= people_regions


def test_no_endpoint_returns_forbidden_fields(client):
    def keys(node: object) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for value in node.values() for k in keys(value)}
        if isinstance(node, list):
            return {k for value in node for k in keys(value)}
        return set()

    for path in (
        "/people",
        "/people/emp-000001",
        "/changes",
        "/people/emp-000001/acl",
        "/policies",
    ):
        assert not keys(client.get(path).json()) & set(FORBIDDEN), path


def test_changes_paginates_without_loss_or_duplicates(client):
    small = all_changes(client, limit=7)
    big = all_changes(client, limit=500)
    assert [i["id"] for i in small] == [i["id"] for i in big]
    assert len({i["id"] for i in big}) == 86


def test_updated_since_returns_only_the_edit(client):
    before = client.get("/changes").json()["server_time"]
    client.put("/admin/policies/pol-remote-work", json={"body": "# Remote Work Policy\n\nNew."})
    items = all_changes(client, since=before)
    assert [i["id"] for i in items] == ["pol-remote-work"]
    assert items[0]["version"] == 2


def test_edit_with_identical_body_does_not_bump_version(client):
    body = client.get("/policies/pol-remote-work/content").text
    response = client.put("/admin/policies/pol-remote-work", json={"body": body})
    assert response.json()["version"] == 1


def test_edit_changes_content_hash(client):
    old = client.get("/policies/pol-remote-work").json()["content_hash"]
    client.put("/admin/policies/pol-remote-work", json={"body": "# Remote Work Policy\n\nNew."})
    assert client.get("/policies/pol-remote-work").json()["content_hash"] != old


def test_acl_change_moves_updated_at_but_not_version_or_hash(client):
    before = client.get("/policies/pol-benefits-overview").json()
    after = client.put(
        "/admin/policies/pol-benefits-overview/acl", json={"native_acl": ["hr_policy_admin"]}
    ).json()
    assert after["native_acl"] == ["hr_policy_admin"]
    assert after["version"] == before["version"]
    assert after["content_hash"] == before["content_hash"]
    assert after["updated_at"] > before["updated_at"]


def test_delete_is_visible_only_in_ids(client):
    marker = client.get("/ids").json()["server_time"]
    assert client.delete("/admin/policies/pol-expenses").status_code == 204
    assert client.get("/policies/pol-expenses").status_code == 404
    assert client.get("/policies/pol-expenses/content").status_code == 404
    assert "pol-expenses" not in {i["id"] for i in client.get("/ids").json()["ids"]["policy"]}
    assert all_changes(client, since=marker) == []


def test_person_edit_and_delete(client):
    changed = client.patch("/admin/people/emp-000010", json={"team": "payments"}).json()
    assert changed["team"] == "payments" and changed["version"] == 2
    assert client.delete("/admin/people/emp-000010").status_code == 204
    assert client.get("/people/emp-000010").status_code == 404


def test_reset_restores_seed(client):
    client.delete("/admin/policies/pol-expenses")
    client.post("/admin/reset")
    assert client.get("/discover").json()["counts"] == {"person": 48, "policy": 38}


# Negative cases


def test_unknown_object_is_404(client):
    assert client.get("/policies/pol-nope").status_code == 404
    assert client.get("/people/emp-999/acl").status_code == 404
    assert client.delete("/admin/policies/pol-nope").status_code == 404


def test_bad_timestamp_is_422(client):
    assert client.get("/changes", params={"updated_since": "yesterday"}).status_code == 422


def test_page_limit_is_bounded(client):
    assert client.get("/changes", params={"limit": 0}).status_code == 422
    assert client.get("/changes", params={"limit": 501}).status_code == 422


def test_unknown_native_acl_is_rejected(client):
    response = client.put("/admin/policies/pol-remote-work/acl", json={"native_acl": ["root"]})
    assert response.status_code == 422
    assert (
        client.put("/admin/policies/pol-remote-work/acl", json={"native_acl": []}).status_code
        == 422
    )


def test_duplicate_policy_and_person_conflict(client):
    body = {
        "policy_id": "pol-remote-work",
        "title": "x",
        "category": "c",
        "region": "DE",
        "body": "x",
    }
    assert client.post("/admin/policies", json=body).status_code == 409
    person = client.get("/people/emp-000001").json()
    person = {k: person[k] for k in ("person_id", "email", "full_name", "team", "role", "region")}
    assert client.post("/admin/people", json=person).status_code == 409


def test_duplicate_email_conflicts(client):
    person = client.get("/people/emp-000001").json()
    person = {k: person[k] for k in ("email", "full_name", "team", "role", "region")}
    assert client.post("/admin/people", json={**person, "person_id": "emp-900"}).status_code == 409


def test_invalid_policy_id_and_format_rejected(client):
    base = {"title": "t", "category": "c", "region": "DE", "body": "b"}
    assert client.post("/admin/policies", json={**base, "policy_id": "BAD"}).status_code == 422
    response = client.post(
        "/admin/policies", json={**base, "policy_id": "pol-new-one", "format": "docx"}
    )
    assert response.status_code == 422


def test_generator_is_deterministic_and_unique():
    from harness_hr.generate import generate_people, generate_policies

    assert generate_people(20, seed=7) == generate_people(20, seed=7)
    assert generate_policies(20, seed=7) == generate_policies(20, seed=7)
    people = generate_people(10_000, seed=3)
    assert len({row["person_id"] for row in people}) == 10_000
    assert len({row["email"] for row in people}) == 10_000
    policies = generate_policies(10_000, seed=3)
    assert len({row["policy_id"] for row in policies}) == 10_000
    assert sum(row["format"] == "pdf" for row in policies) == 2_000
    assert sum(row["native_acl"] == ["hr_policy_admin"] for row in policies) == 500


def test_admin_seed_pages_ten_thousand_people(client):
    response = client.post("/admin/seed", json={"kind": "person", "count": 10_000, "seed": 11})
    assert response.status_code == 200
    body = response.json()
    assert body["inserted"] == 10_000
    assert body["counts"]["person"] == 10_048
    people = [item for item in all_changes(client, limit=500) if item["object_type"] == "person"]
    assert len(people) == len({item["id"] for item in people}) == 10_048
    sample = client.get("/changes", params={"limit": 1}).json()["items"][0]["record"]
    assert not set(sample) & set(FORBIDDEN)


def test_second_seed_continues_ids(client):
    first = client.post("/admin/seed", json={"kind": "person", "count": 3, "seed": 2})
    second = client.post("/admin/seed", json={"kind": "person", "count": 2, "seed": 2})
    assert first.status_code == second.status_code == 200
    ids = {row["id"] for row in client.get("/ids").json()["ids"]["person"]}
    assert "emp-000049" in ids and "emp-000053" in ids
    assert len(ids) == 53


def test_seed_count_and_kind_are_rejected(client):
    assert client.post("/admin/seed", json={"kind": "person", "count": 50_001}).status_code == 422
    assert client.post("/admin/seed", json={"kind": "deal", "count": 1}).status_code == 422
    assert client.post("/admin/seed", json={"kind": "policy", "count": 0}).status_code == 422


def test_seed_replace_drops_previous_people(client):
    client.post("/admin/seed", json={"kind": "person", "count": 2, "seed": 1})
    replaced = client.post(
        "/admin/seed", json={"kind": "person", "count": 4, "seed": 9, "replace": True}
    )
    assert replaced.json()["counts"]["person"] == 4
    assert client.get("/people/emp-000001").status_code == 200
    assert client.get("/people/emp-000048").status_code == 404
