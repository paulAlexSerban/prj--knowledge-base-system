"""MCP tools use the same service as REST. A missing token never reaches a tool."""

from datetime import UTC, datetime

import boto3
import pytest
from fastapi.testclient import TestClient
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.transport_security import TransportSecuritySettings
from moto import mock_aws
from sqlalchemy import text

from gateway_core.auth import issue_token, principal_from_token
from gateway_core.graph_read import GraphReader
from gateway_core.metrics import AUTH_FAILURES
from gateway_core.service import GatewayService
from gateway_core.tools import FIND_RELATED, GET_DOCUMENT, LIST_SOURCES, SEARCH
from ingest_contract.events import RAW_BUCKET, ChangeEvent
from knowledge_store.embedder import HashEmbedder
from knowledge_store.graph import GraphWriter
from knowledge_store.postgres import ChunkTable
from knowledge_store.store import KnowledgeStore
from mcp_server.server import JwtVerifier, build_server
from shared.contracts.validate import load_contracts

SECRET = "test-secret-test-secret-test-secret"
DE = b"# Leave\n\n## Eligibility\n\nParental leave in Germany is sixteen weeks.\n"
ADMIN = b"# Redundancy\n\n## Process\n\nThe redundancy procedure is confidential.\n"


def _root():
    from pathlib import Path

    return Path(__file__).resolve().parents[4]


def _contracts():
    found = load_contracts(_root() / "infrastructure" / "config" / "sources")
    return {item.source_id: item for item in found}


@pytest.fixture
def mcp(database, neo4j_driver, monkeypatch):
    monkeypatch.setenv("GATEWAY_JWT_SECRET", SECRET)
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
        rows = [
            ("pol-de", "raw/hr/policy/pol-de.md", DE, "hr_employee", "hash-de"),
            ("pol-redundancy", "raw/hr/policy/pol-redundancy.md", ADMIN, "hr_policy_admin", "hash-admin"),
        ]
        for object_id, key, body, acl, digest in rows:
            client.put_object(
                Bucket=RAW_BUCKET,
                Key=key,
                Body=body,
                Metadata={"kbs-content-hash": digest, "kbs-version": "1", "kbs-native-acl": acl},
            )
            store.upsert_object(
                ChangeEvent(
                    source_id="hr",
                    object_id=object_id,
                    event_type="create",
                    version="1",
                    content_hash=digest,
                    occurred_at=datetime.now(UTC),
                    s3_pointer=key,
                )
            )
        service = GatewayService(store, GraphReader(neo4j_driver))
        yield build_server(service), service


def _use(persona: str):
    import asyncio

    access = asyncio.run(JwtVerifier().verify_token(issue_token(persona, SECRET)))
    assert access is not None
    return auth_context_var.set(AuthenticatedUser(access))


def test_bad_mcp_token_is_counted(monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("GATEWAY_JWT_SECRET", SECRET)
    before = AUTH_FAILURES.labels(reason="invalid")._value.get()
    assert asyncio.run(JwtVerifier().verify_token("not-a-token")) is None
    assert AUTH_FAILURES.labels(reason="invalid")._value.get() == before + 1


def test_mcp_route_rejects_a_missing_token(mcp) -> None:
    server, _service = mcp
    app = server.streamable_http_app(
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    with TestClient(app) as http:
        refused = http.post("/mcp", headers={"accept": "application/json, text/event-stream"})
        assert refused.status_code == 401
        assert http.get("/health").status_code == 200


def test_tools_match_the_shared_descriptions(mcp) -> None:
    server, _service = mcp
    descriptions = {tool.name: tool.description for tool in server._tool_manager.list_tools()}
    assert descriptions == {
        "search_knowledge": SEARCH,
        "get_document": GET_DOCUMENT,
        "find_related": FIND_RELATED,
        "list_sources": LIST_SOURCES,
    }


def test_two_personas_match_the_service(mcp) -> None:
    server, service = mcp
    search = server._tool_manager.get_tool("search_knowledge")
    assert search is not None
    engineer = _use("employee_eng")
    try:
        via_mcp = search.fn(query="Germany", limit=5)
    finally:
        auth_context_var.reset(engineer)
    via_rest = service.search(
        principal_from_token(issue_token("employee_eng", SECRET), SECRET),
        "Germany",
        5,
        "rest",
    )
    assert {item["object_id"] for item in via_mcp["results"]} == {"pol-de"}
    assert {item["object_id"] for item in via_rest["results"]} == {"pol-de"}

    outsider = _use("outsider")
    try:
        hidden = search.fn(query="Germany", limit=5)
        document = server._tool_manager.get_tool("get_document")
        assert document is not None
        missing = document.fn(chunk_id=via_mcp["results"][0]["chunk_id"])
    finally:
        auth_context_var.reset(outsider)
    assert hidden["results"] == []
    assert missing["error"] == "not found"

    try:
        search.fn(query="Germany")
    except PermissionError:
        pass
    else:
        raise AssertionError("expected PermissionError")
