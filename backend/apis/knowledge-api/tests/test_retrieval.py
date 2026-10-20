"""Authorized retrieval through the API. Two principals, one pool."""

import json
import logging
from datetime import UTC, datetime
from io import StringIO

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import text

from gateway_core.auth import issue_token
from gateway_core.graph_read import GraphReader
from gateway_core.metrics import AUTH_FAILURES, EMPTY, RECHECK_FAILURES, REQUESTS, TOTAL
from gateway_core.recheck import RecheckFailed
from gateway_core.service import GatewayService
from ingest_contract.events import RAW_BUCKET, ChangeEvent
from knowledge_api.app import create_app
from knowledge_store.acl import AclView, Principal, allows, view_from_rules
from knowledge_store.embedder import HashEmbedder
from knowledge_store.graph import GraphWriter
from knowledge_store.postgres import ChunkTable
from knowledge_store.store import KnowledgeStore
from shared.contracts.validate import load_contracts
from shared.logging import JsonFormatter

SECRET = "test-secret-test-secret-test-secret"
DE = (
    b"# Leave\n\n## Eligibility\n\nParental leave in Germany is sixteen weeks.\n\n"
    b"## Notice\n\nGermany requires notice to the manager.\n"
)
FR = b"# Leave\n\n## Eligibility\n\nParental leave in France is twenty eight weeks.\n"
ADMIN = b"# Redundancy\n\n## Process\n\nThe redundancy procedure is confidential.\n"


def _root():
    from pathlib import Path

    return Path(__file__).resolve().parents[4]


def _contracts():
    found = load_contracts(_root() / "infrastructure" / "config" / "sources")
    return {item.source_id: item for item in found}


def _person(person_id: str) -> bytes:
    return (
        f'{{"person_id":"{person_id}","full_name":"Ada {person_id}","team":"People",'
        f'"email":"{person_id}@example.com","role":"Engineer","version":"1"}}'
    ).encode()


def _put(client, key: str, body: bytes, acl: str, digest: str) -> None:
    client.put_object(
        Bucket=RAW_BUCKET,
        Key=key,
        Body=body,
        Metadata={"kbs-content-hash": digest, "kbs-version": "1", "kbs-native-acl": acl},
    )


def _event(object_id: str, key: str, digest: str) -> ChangeEvent:
    return ChangeEvent(
        source_id="hr",
        object_id=object_id,
        event_type="create",
        version="1",
        content_hash=digest,
        occurred_at=datetime.now(UTC),
        s3_pointer=key,
    )


@pytest.fixture
def api(database, neo4j_driver, monkeypatch):
    monkeypatch.setenv("GATEWAY_JWT_SECRET", SECRET)
    monkeypatch.setenv("DEV_TOKEN_ENDPOINT", "1")
    with database["admin"].begin() as connection:
        connection.execute(text("TRUNCATE chunks, tombstones, source_objects"))
        connection.execute(text("TRUNCATE audit_log"))
    neo4j_driver.execute_query("MATCH (n) DETACH DELETE n")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=RAW_BUCKET)
        store = KnowledgeStore(
            ChunkTable(database["writer"], database["reader"]),
            GraphWriter(neo4j_driver),
            client,
            RAW_BUCKET,
            HashEmbedder(),
            _contracts(),
        )
        seeded = [
            ("pol-de", "raw/hr/policy/pol-de.md", DE, "hr_employee", "hash-de"),
            ("pol-fr", "raw/hr/policy/pol-fr.md", FR, "hr_employee", "hash-fr"),
            ("pol-redundancy", "raw/hr/policy/pol-redundancy.md", ADMIN, "hr_policy_admin", "hash-admin"),
            ("emp-1", "raw/hr/person/emp-1.json", _person("emp-1"), "hr_employee", "hash-e1"),
            ("emp-2", "raw/hr/person/emp-2.json", _person("emp-2"), "hr_employee", "hash-e2"),
        ]
        for object_id, key, body, acl, digest in seeded:
            _put(client, key, body, acl, digest)
            store.upsert_object(_event(object_id, key, digest))
        service = GatewayService(store, GraphReader(neo4j_driver))
        with TestClient(create_app(service)) as http:
            yield http, database["admin"]


def _auth(persona: str) -> dict[str, str]:
    return {"authorization": f"Bearer {issue_token(persona, SECRET)}"}


def test_search_requires_a_token(api) -> None:
    http, _admin = api
    response = http.post("/v1/search", json={"query": "Germany"})
    assert response.status_code == 401


def test_expired_token_is_rejected(api) -> None:
    http, _admin = api
    token = issue_token("employee_eng", SECRET, ttl_seconds=-10)
    response = http.post(
        "/v1/search",
        json={"query": "Germany"},
        headers={"authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_engineer_finds_the_germany_policy_and_outsider_does_not(api) -> None:
    http, admin = api
    found = http.post(
        "/v1/search",
        json={"query": "Germany", "limit": 5},
        headers={**_auth("employee_eng"), "x-request-id": "req-allow"},
    )
    assert found.status_code == 200
    body = found.json()
    assert body["request_id"] == "req-allow"
    assert found.headers["x-request-id"] == "req-allow"
    assert body["results"]
    hit = body["results"][0]
    assert hit["object_id"] == "pol-de"
    assert [item["object_id"] for item in body["results"]].count("pol-de") == 1
    assert hit["snippet"].startswith("<<<UNTRUSTED_CONTENT")
    assert "Germany" in hit["snippet"]
    assert hit["synced_at"]
    denied = http.post(
        "/v1/search",
        json={"query": "Germany"},
        headers={**_auth("outsider"), "x-request-id": "req-deny"},
    )
    assert denied.status_code == 200
    assert denied.json()["results"] == []
    with admin.connect() as connection:
        decisions = {
            (row.request_id, row.decision)
            for row in connection.execute(text("SELECT request_id, decision FROM audit_log"))
        }
    assert ("req-allow", "allow") in decisions
    assert ("req-deny", "deny") in decisions


def test_no_answer_and_conflicting_countries(api) -> None:
    http, _admin = api
    empty = http.post(
        "/v1/search",
        json={"query": "xylophone quantum payroll"},
        headers=_auth("employee_eng"),
    )
    assert empty.status_code == 200
    assert empty.json()["results"] == []
    both = http.post(
        "/v1/search",
        json={"query": "parental leave", "limit": 10},
        headers=_auth("employee_eng"),
    )
    ids = {item["object_id"] for item in both.json()["results"]}
    assert {"pol-de", "pol-fr"} <= ids


def test_admin_policy_is_hidden_from_an_engineer(api) -> None:
    http, _admin = api
    employee = http.post(
        "/v1/search",
        json={"query": "redundancy"},
        headers=_auth("employee_eng"),
    )
    assert employee.json()["results"] == []
    admin = http.post(
        "/v1/search",
        json={"query": "redundancy"},
        headers=_auth("hr_admin"),
    )
    assert {item["object_id"] for item in admin.json()["results"]} == {"pol-redundancy"}


def test_get_document_does_not_reveal_a_hidden_chunk(api) -> None:
    http, _admin = api
    found = http.post("/v1/search", json={"query": "Germany"}, headers=_auth("employee_eng"))
    chunk_id = found.json()["results"][0]["chunk_id"]
    allowed = http.get(f"/v1/documents/{chunk_id}", headers=_auth("employee_eng"))
    assert allowed.status_code == 200
    assert "Germany" in allowed.json()["snippet"]
    hidden = http.get(f"/v1/documents/{chunk_id}", headers=_auth("outsider"))
    assert hidden.status_code == 404
    missing = http.get("/v1/documents/999999", headers=_auth("employee_eng"))
    assert missing.status_code == 404


def test_graph_paths_follow_the_caller(api) -> None:
    http, _admin = api
    team = http.post(
        "/v1/graph/related",
        json={"kind": "team", "entity_id": "People"},
        headers=_auth("employee_eng"),
    )
    assert team.status_code == 200
    people = {
        step["id"]
        for path in team.json()["paths"]
        for step in path["steps"]
        if step["type"] == "Person"
    }
    assert people == {"emp-1", "emp-2"}
    outsider = http.post(
        "/v1/graph/related",
        json={"kind": "team", "entity_id": "People"},
        headers=_auth("outsider"),
    )
    assert outsider.json()["paths"] == []
    policies = http.post(
        "/v1/graph/related",
        json={"kind": "policies"},
        headers=_auth("employee_eng"),
    )
    policy_ids = {
        step["id"]
        for path in policies.json()["paths"]
        for step in path["steps"]
        if step["type"] == "Policy"
    }
    assert "pol-de" in policy_ids
    assert "pol-redundancy" not in policy_ids
    one = http.post(
        "/v1/graph/related",
        json={"kind": "policies", "entity_id": "pol-de"},
        headers=_auth("employee_eng"),
    )
    one_ids = {
        step["id"]
        for path in one.json()["paths"]
        for step in path["steps"]
        if step["type"] == "Policy"
    }
    assert one_ids == {"pol-de"}
    missing = http.post(
        "/v1/graph/related",
        json={"kind": "teammates"},
        headers=_auth("employee_eng"),
    )
    assert missing.status_code == 400


def test_graph_hides_a_sales_deal_and_a_mixed_rule(api) -> None:
    http, admin = api
    service = http.app.state.service
    sales = AclView(
        ("sales_team",),
        ("sales",),
        1,
        ({"group": "sales_team", "department": "sales", "clearance": "internal"},),
        (),
    )
    service.store.graph.write(
        "sales",
        "deal",
        "deal-9",
        {"account_id": "acme", "epic_id": ""},
        "Acme",
        sales,
        "1",
    )
    service.store.graph.write("sales", "policy", "pol-sales", {}, "Forecast", sales, "1")
    mixed = AclView(
        ("employees", "hr_admins"),
        (),
        1,
        (
            {"group": "employees", "department": "*", "clearance": "internal"},
            {"group": "hr_admins", "department": "people", "clearance": "confidential"},
        ),
        (),
    )
    service.store.graph.write("hr", "policy", "pol-mixed", {}, "Mixed", mixed, "1")

    stored = service.graph.driver.execute_query(
        """
        MATCH (d:Deal {id: $id})
        RETURN d.acl_departments AS departments, d.acl_rules AS rules
        """,
        id="deal-9",
    )[0][0]
    assert list(stored["departments"]) == ["sales"]
    deal = view_from_rules(stored["rules"])
    engineer = Principal("employee-eng", ("employees",), "internal", "engineering")
    seller = Principal("sam", ("sales_team",), "internal", "sales")
    assert allows(deal, engineer) == (False, None)
    assert allows(deal, seller)[0] is True

    engineer_ids = _policy_ids(service.graph.related("policies", engineer, None, 25))
    assert "pol-sales" not in engineer_ids
    assert "pol-de" in engineer_ids
    assert "pol-mixed" in engineer_ids
    seller_ids = _policy_ids(service.graph.related("policies", seller, None, 25))
    assert seller_ids == {"pol-sales"}

    low_admin = Principal("cam", ("hr_admins",), "internal", "people")
    denied = service.find_related(low_admin, "policies", "pol-mixed", 25, "graph-mixed")
    assert denied["paths"] == []
    outsider = service.graph.related(
        "policies", Principal("outsider", (), "internal", "engineering"), None, 25
    )
    assert outsider == []
    with admin.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT decision, attributes->>'object_id' AS object_id
                FROM audit_log WHERE request_id = 'graph-mixed'
                """
            )
        ).one()
    assert row.decision == "deny"
    assert row.object_id == "pol-mixed"


def test_find_related_audits_allow_and_deny(api) -> None:
    http, admin = api
    allowed = http.post(
        "/v1/graph/related",
        json={"kind": "policies", "entity_id": "pol-de"},
        headers={**_auth("employee_eng"), "x-request-id": "graph-allow"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["paths"]
    denied = http.post(
        "/v1/graph/related",
        json={"kind": "team", "entity_id": "People"},
        headers={**_auth("outsider"), "x-request-id": "graph-deny"},
    )
    assert denied.json()["paths"] == []
    with admin.connect() as connection:
        rows = {
            (row.request_id, row.decision, row.chunk_id)
            for row in connection.execute(
                text(
                    """
                    SELECT request_id, decision, chunk_id FROM audit_log
                    WHERE request_id IN ('graph-allow', 'graph-deny')
                    """
                )
            )
        }
    assert ("graph-allow", "allow", None) in rows
    assert ("graph-deny", "deny", None) in rows


def _policy_ids(accesses) -> set[str]:
    found: set[str] = set()
    for item in accesses:
        if not item.allowed:
            continue
        for step in item.path["steps"]:
            if step["type"] == "Policy":
                found.add(step["id"])
    return found


def test_empty_search_is_a_deny_line_and_a_metric(api) -> None:
    http, _admin = api
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("gateway-api", "access"))
    logger = logging.getLogger("gateway")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    empty_before = EMPTY.labels(source_id="none", permission_group="none")._value.get()
    requests_before = REQUESTS.labels(tool="search", surface="rest", result="empty")._value.get()
    missing_before = AUTH_FAILURES.labels(reason="missing")._value.get()
    try:
        denied = http.post(
            "/v1/search",
            json={"query": "Germany"},
            headers={**_auth("outsider"), "x-request-id": "req-metric"},
        )
        refused = http.post("/v1/search", json={"query": "Germany"})
    finally:
        logger.removeHandler(handler)
    assert denied.status_code == 200
    assert denied.json()["results"] == []
    assert refused.status_code == 401
    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line["decision"] == "deny"
    assert line["request_id"] == "req-metric"
    assert line["tool"] == "search"
    assert "text" not in line
    assert "snippet" not in line
    assert EMPTY.labels(source_id="none", permission_group="none")._value.get() == empty_before + 1
    assert REQUESTS.labels(tool="search", surface="rest", result="empty")._value.get() == requests_before + 1
    assert AUTH_FAILURES.labels(reason="missing")._value.get() == missing_before + 1


class _Acl:
    def __init__(self, native: list[str] | None) -> None:
        self.native = native

    def current_acl(self, source_id: str, object_type: str, object_id: str) -> list[str] | None:
        return self.native


class _Down:
    def current_acl(self, source_id: str, object_type: str, object_id: str) -> list[str] | None:
        raise RecheckFailed("timeout")


def test_read_time_acl_drops_a_closed_policy_and_keeps_people(api) -> None:
    http, _admin = api
    http.app.state.service.recheck = _Acl(["hr_policy_admin"])
    employee = http.post(
        "/v1/search",
        json={"query": "Germany", "limit": 5},
        headers=_auth("employee_eng"),
    )
    assert employee.json()["results"] == []
    admin = http.post(
        "/v1/search",
        json={"query": "Germany", "limit": 5},
        headers=_auth("hr_admin"),
    )
    assert {item["object_id"] for item in admin.json()["results"]} == {"pol-de"}
    people = http.post(
        "/v1/search",
        json={"query": "Ada", "limit": 5},
        headers=_auth("employee_eng"),
    )
    assert {item["object_type"] for item in people.json()["results"]} == {"person"}
    found = http.post("/v1/search", json={"query": "Germany"}, headers=_auth("hr_admin"))
    chunk_id = found.json()["results"][0]["chunk_id"]
    hidden = http.get(f"/v1/documents/{chunk_id}", headers=_auth("employee_eng"))
    assert hidden.status_code == 404


def test_read_time_acl_failure_returns_nothing(api) -> None:
    http, _admin = api
    http.app.state.service.recheck = _Down()
    before = RECHECK_FAILURES.labels(source_id="hr")._value.get()
    found = http.post(
        "/v1/search",
        json={"query": "Germany"},
        headers=_auth("employee_eng"),
    )
    assert found.json()["results"] == []
    assert RECHECK_FAILURES.labels(source_id="hr")._value.get() == before + 1


def _seed_roadmap(driver) -> None:
    driver.execute_query(
        """
        CREATE (t:Team {id: 'payments'})
        CREATE (openEpic:Epic {
            id: 'epic-checkout-retry', title: 'Retry', status: 'in_progress',
            version: '1', source_id: 'engineering',
            acl_groups: ['eng_project', 'roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $epic
        })-[:OWNED_BY]->(t)
        CREATE (done:Epic {
            id: 'epic-checkout-wallets', title: 'Wallets', status: 'released',
            version: '1', source_id: 'engineering',
            acl_groups: ['roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $epic
        })-[:OWNED_BY]->(t)
        CREATE (:Deal {
            id: 'deal-northwind-q3', account_name: 'Northwind Traders', arr: 480000,
            quarter: '2026-Q3', version: '1', source_id: 'sales',
            acl_groups: ['roadmap', 'sales_team'], acl_departments: [],
            min_clearance: 1, acl_rules: $open
        })-[:DEPENDS_ON]->(openEpic)
        CREATE (:Deal {
            id: 'deal-contoso-q3', account_name: 'Contoso', arr: 920000,
            quarter: '2026-Q3', version: '1', source_id: 'sales',
            acl_groups: ['sales_leadership'], acl_departments: ['sales'],
            min_clearance: 2, acl_rules: $lead
        })-[:DEPENDS_ON]->(openEpic)
        CREATE (:Deal {
            id: 'deal-woodgrove-q3', account_name: 'Woodgrove', arr: 700000,
            quarter: '2026-Q3', version: '1', source_id: 'sales',
            acl_groups: ['roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $open
        })-[:DEPENDS_ON]->(done)
        CREATE (:Deal {
            id: 'deal-graphic-q3', account_name: 'Graphic', arr: 45000,
            quarter: '2026-Q3', version: '1', source_id: 'sales',
            acl_groups: ['roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $open
        })-[:DEPENDS_ON]->(openEpic)
        CREATE (:Deal {
            id: 'deal-coho-q2', account_name: 'Coho', arr: 800000,
            quarter: '2026-Q2', version: '1', source_id: 'sales',
            acl_groups: ['roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $open
        })-[:DEPENDS_ON]->(openEpic)
        """,
        open=json.dumps(
            [
                {"group": "sales_team", "department": "sales", "clearance": "internal"},
                {"group": "roadmap", "department": "*", "clearance": "internal"},
            ]
        ),
        epic=json.dumps(
            [
                {"group": "eng_project", "department": "engineering", "clearance": "internal"},
                {"group": "roadmap", "department": "*", "clearance": "internal"},
            ]
        ),
        lead=json.dumps(
            [{"group": "sales_leadership", "department": "sales", "clearance": "confidential"}]
        ),
    )


def _deal_ids(body: dict) -> set[str]:
    found = set()
    for path in body["paths"]:
        for step in path["steps"]:
            if step["type"] == "Deal":
                found.add(step["id"])
    return found


def test_roadmap_deals_is_an_access_boundary(api) -> None:
    http, admin = api
    _seed_roadmap(http.app.state.service.graph.driver)
    sales_before = TOTAL.labels(source_id="sales", permission_group="roadmap")._value.get()
    sales_empty_before = EMPTY.labels(source_id="sales", permission_group="roadmap")._value.get()
    none_before = TOTAL.labels(source_id="none", permission_group="none")._value.get()
    none_empty_before = EMPTY.labels(source_id="none", permission_group="none")._value.get()
    eng_empty_before = EMPTY.labels(source_id="none", permission_group="employees")._value.get()
    allowed = http.post(
        "/v1/graph/related",
        json={"kind": "roadmap_deals"},
        headers={**_auth("product_lead"), "x-request-id": "roadmap-allow"},
    )
    assert allowed.status_code == 200
    assert _deal_ids(allowed.json()) == {"deal-northwind-q3"}
    hidden = {"deal-contoso-q3", "deal-woodgrove-q3", "deal-graphic-q3", "deal-coho-q2"}
    assert _deal_ids(allowed.json()).isdisjoint(hidden)
    engineer = http.post(
        "/v1/graph/related",
        json={"kind": "roadmap_deals"},
        headers={**_auth("employee_eng"), "x-request-id": "roadmap-eng"},
    )
    outsider = http.post(
        "/v1/graph/related",
        json={"kind": "roadmap_deals"},
        headers={**_auth("outsider"), "x-request-id": "roadmap-out"},
    )
    assert engineer.json()["paths"] == []
    assert outsider.json()["paths"] == []
    assert TOTAL.labels(source_id="sales", permission_group="roadmap")._value.get() == sales_before + 1
    assert EMPTY.labels(source_id="sales", permission_group="roadmap")._value.get() == sales_empty_before
    assert TOTAL.labels(source_id="none", permission_group="none")._value.get() == none_before + 1
    assert EMPTY.labels(source_id="none", permission_group="none")._value.get() == none_empty_before + 1
    assert (
        EMPTY.labels(source_id="none", permission_group="employees")._value.get()
        == eng_empty_before + 1
    )
    assert http.post(
        "/v1/graph/related",
        json={"kind": "roadmap_deals", "quarter": "Q3"},
        headers=_auth("product_lead"),
    ).status_code == 422
    assert http.post(
        "/v1/graph/related",
        json={"kind": "roadmap_deals", "min_arr": -1},
        headers=_auth("product_lead"),
    ).status_code == 422
    with admin.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT decision, attributes->'rule'->>'group' AS grp
                FROM audit_log WHERE request_id = 'roadmap-allow'
                """
            )
        ).all()
    assert rows
    assert {row.decision for row in rows} == {"allow"}
    assert {row.grp for row in rows} == {"roadmap"}


def test_ask_routes_the_roadmap_question_and_hides_it_from_an_outsider(api) -> None:
    http, _admin = api
    _seed_roadmap(http.app.state.service.graph.driver)
    question = "Which high-ARR Sales deals in Q3 depend on unreleased Engineering epics?"
    allowed = http.post(
        "/v1/ask",
        json={"query": question},
        headers={**_auth("product_lead"), "x-request-id": "ask-roadmap"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["route"] == {
        "class": "relational",
        "source": "rules",
        "graph": "roadmap_deals",
        "quarter": "2026-Q3",
        "min_arr": None,
    }
    assert _deal_ids(allowed.json()) == {"deal-northwind-q3"}
    outsider = http.post("/v1/ask", json={"query": question}, headers=_auth("outsider"))
    assert outsider.status_code == 200
    assert outsider.json()["paths"] == []
    assert outsider.json()["route"]["class"] == "relational"
    policy = "parental leave in Germany"
    engineer = http.post("/v1/ask", json={"query": policy}, headers=_auth("employee_eng"))
    assert engineer.json()["route"]["class"] == "semantic"
    assert any(item["object_id"] == "pol-de" for item in engineer.json()["results"])
    hidden = http.post("/v1/ask", json={"query": policy}, headers=_auth("outsider"))
    assert hidden.json()["results"] == []
    assert http.post("/v1/ask", json={"query": question}).status_code == 401


def test_dev_token_issues_a_persona_and_stays_hidden(api, monkeypatch) -> None:
    http, _admin = api
    issued = http.post("/dev/token", json={"persona": "employee_eng"})
    assert issued.status_code == 200
    assert issued.json()["principal"]["subject"] == "employee-eng"
    monkeypatch.delenv("DEV_TOKEN_ENDPOINT")
    hidden = http.post("/dev/token", json={"persona": "employee_eng"})
    assert hidden.status_code == 404
