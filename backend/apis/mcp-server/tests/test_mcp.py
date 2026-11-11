"""MCP tools use the same service as REST. A missing token never reaches a tool."""

import json
from datetime import UTC, datetime

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import text

from gateway_core.auth import issue_token, principal_from_token
from gateway_core.graph_read import GraphReader
from gateway_core.metrics import AUTH_FAILURES
from gateway_core.service import GatewayService
from gateway_core.tools import ASK, FIND_RELATED, GET_DOCUMENT, LIST_SOURCES, SEARCH
from ingest_contract.events import RAW_BUCKET, ChangeEvent
from knowledge_store.embedder import HashEmbedder
from knowledge_store.graph import GraphWriter
from knowledge_store.postgres import ChunkTable
from knowledge_store.store import KnowledgeStore
from mcp_server.server import JwtVerifier, build_server, http_app
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
            (
                "pol-redundancy",
                "raw/hr/policy/pol-redundancy.md",
                ADMIN,
                "hr_policy_admin",
                "hash-admin",
            ),
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


def _call(server, persona: str, tool: str, **arguments) -> dict:
    """Call a tool over the real transport as a persona. Auth only runs there."""
    import asyncio

    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    app = http_app(server)
    headers = {"Authorization": f"Bearer {issue_token(persona, SECRET)}"}

    async def run() -> dict:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(  # noqa: SIM117 - the client is an argument of the next one
            transport=transport, base_url="http://testserver", headers=headers
        ) as http:
            async with streamable_http_client("http://testserver/mcp", http_client=http) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments)
                    assert result.is_error is False
                    return result.structured_content

    with TestClient(app):
        return asyncio.run(run())


def test_bad_mcp_token_is_counted(monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("GATEWAY_JWT_SECRET", SECRET)
    before = AUTH_FAILURES.labels(reason="invalid")._value.get()
    assert asyncio.run(JwtVerifier().verify_token("not-a-token")) is None
    assert AUTH_FAILURES.labels(reason="invalid")._value.get() == before + 1


def test_mcp_route_rejects_a_missing_token(mcp) -> None:
    from prometheus_client import REGISTRY

    server, _service = mcp
    app = http_app(server)
    with TestClient(app) as http:
        refused = http.post("/mcp", headers={"accept": "application/json, text/event-stream"})
        assert refused.status_code == 401
        assert http.get("/health").status_code == 200
        raw = "/not-a-tool"
        assert http.get(raw).status_code == 404
    routes: set[str] = set()
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == "kbs_http_requests_total":
                route = sample.labels.get("route")
                if route is not None:
                    routes.add(route)
    assert "unmatched" in routes
    assert "/health" in routes
    assert raw not in routes


def test_tools_match_the_shared_descriptions(mcp) -> None:
    server, _service = mcp
    import asyncio

    tools = asyncio.run(server.list_tools())
    descriptions = {tool.name: tool.description for tool in tools}
    assert descriptions == {
        "ask_knowledge": ASK,
        "search_knowledge": SEARCH,
        "get_document": GET_DOCUMENT,
        "find_related": FIND_RELATED,
        "list_sources": LIST_SOURCES,
    }


def test_search_over_the_http_transport(mcp) -> None:
    server, _service = mcp
    found = _call(server, "employee_eng", "search_knowledge", query="Germany", limit=5)
    hidden = _call(server, "outsider", "search_knowledge", query="Germany", limit=5)
    assert {item["object_id"] for item in found["results"]} == {"pol-de"}
    assert hidden["results"] == []


def test_two_personas_match_the_service(mcp) -> None:
    server, service = mcp
    via_mcp = _call(server, "employee_eng", "search_knowledge", query="Germany", limit=5)
    via_rest = service.search(
        principal_from_token(issue_token("employee_eng", SECRET), SECRET),
        "Germany",
        5,
        "rest",
    )
    assert {item["object_id"] for item in via_mcp["results"]} == {"pol-de"}
    assert {item["object_id"] for item in via_rest["results"]} == {"pol-de"}

    chunk_id = via_mcp["results"][0]["chunk_id"]
    missing = _call(server, "outsider", "get_document", chunk_id=chunk_id)
    assert missing["error"] == "not found"
    own = _call(server, "employee_eng", "get_document", chunk_id=chunk_id)
    assert "error" not in own


def test_roadmap_deals_matches_the_service(mcp) -> None:
    server, service = mcp
    service.graph.driver.execute_query(
        """
        CREATE (t:Team {id: 'payments'})
        CREATE (e:Epic {
            id: 'epic-checkout-retry', title: 'Retry', status: 'in_progress',
            version: '1', source_id: 'engineering',
            acl_groups: ['roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $rules
        })-[:OWNED_BY]->(t)
        CREATE (:Deal {
            id: 'deal-northwind-q3', account_name: 'Northwind Traders', arr: 480000,
            quarter: '2026-Q3', version: '1', source_id: 'sales',
            acl_groups: ['roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $rules
        })-[:DEPENDS_ON]->(e)
        """,
        rules=json.dumps([{"group": "roadmap", "department": "*", "clearance": "internal"}]),
    )
    found = _call(server, "product_lead", "find_related", kind="roadmap_deals")
    assert found["kind"] == "roadmap_deals"
    assert found["paths"][0]["steps"][0]["id"] == "deal-northwind-q3"
    hidden = _call(server, "employee_eng", "find_related", kind="roadmap_deals")
    rejected = _call(server, "employee_eng", "find_related", kind="roadmap_deals", quarter="Q3")
    assert hidden["paths"] == []
    assert rejected["error"] == "quarter"
    question = "Which high-ARR Sales deals in Q3 depend on unreleased Engineering epics?"
    via_ask = _call(server, "product_lead", "ask_knowledge", query=question)
    hidden_ask = _call(server, "outsider", "ask_knowledge", query=question)
    assert via_ask["route"]["class"] == "relational"
    assert via_ask["paths"][0]["steps"][0]["id"] == "deal-northwind-q3"
    assert hidden_ask["route"]["class"] == "relational"
    assert hidden_ask["paths"] == []
