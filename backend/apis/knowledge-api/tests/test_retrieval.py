"""Authorized retrieval through the API. Two principals, one pool."""

from datetime import UTC, datetime

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import text

from gateway_core.auth import issue_token
from gateway_core.graph_read import GraphReader
from gateway_core.service import GatewayService
from ingest_contract.events import RAW_BUCKET, ChangeEvent
from knowledge_api.app import create_app
from knowledge_store.embedder import HashEmbedder
from knowledge_store.graph import GraphWriter
from knowledge_store.postgres import ChunkTable
from knowledge_store.store import KnowledgeStore
from shared.contracts.validate import load_contracts

SECRET = "test-secret-test-secret-test-secret"
DE = b"# Leave\n\n## Eligibility\n\nParental leave in Germany is sixteen weeks.\n"
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
    missing = http.post(
        "/v1/graph/related",
        json={"kind": "teammates"},
        headers=_auth("employee_eng"),
    )
    assert missing.status_code == 400


def test_dev_token_issues_a_persona_and_stays_hidden(api, monkeypatch) -> None:
    http, _admin = api
    issued = http.post("/dev/token", json={"persona": "employee_eng"})
    assert issued.status_code == 200
    assert issued.json()["principal"]["subject"] == "employee-eng"
    monkeypatch.delenv("DEV_TOKEN_ENDPOINT")
    hidden = http.post("/dev/token", json={"persona": "employee_eng"})
    assert hidden.status_code == 404
