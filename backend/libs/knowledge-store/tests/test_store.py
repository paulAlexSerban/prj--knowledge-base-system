"""Chunks, ACL, search, and the graph against Postgres and Neo4j."""

import logging
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import text

from ingest_contract.events import RAW_BUCKET, ChangeEvent
from knowledge_store.acl import AclView, Principal
from knowledge_store.chunkers import ChunkDraft
from knowledge_store.embedder import HashEmbedder
from knowledge_store.graph import GraphWriter
from knowledge_store.postgres import ChunkTable
from knowledge_store.store import KnowledgeStore
from shared.contracts.validate import load_contracts
from shared.logging import JsonFormatter

BUCKET = RAW_BUCKET
POLICY = b"# Leave\n\n## Eligibility\n\nEveryone in Germany is eligible.\n\n## Pay\n\nSixteen weeks.\n"
PERSON = (
    b'{"person_id":"emp-1","full_name":"Ada Lovelace","team":"People",'
    b'"email":"ada@example.com","role":"Head","salary":90000,'
    b'"national_id":"NID-SECRET","version":"1"}'
)


class Spy(HashEmbedder):
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += len(texts)
        return super().embed(texts)


def _attach_worker_logs():
    from io import StringIO

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("ingestion-worker", "ingestion"))
    logger = logging.getLogger("ingestion-worker")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return stream


def _root():
    from pathlib import Path

    return Path(__file__).resolve().parents[4]


def _contracts():
    found = load_contracts(_root() / "infrastructure" / "config" / "sources")
    return {item.source_id: item for item in found}


@pytest.fixture
def world(database, neo4j_driver):
    with database["admin"].begin() as connection:
        connection.execute(text("TRUNCATE chunks, tombstones, source_objects"))
        connection.execute(text("TRUNCATE audit_log"))
    neo4j_driver.execute_query("MATCH (n) DETACH DELETE n")
    spy = Spy()
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        store = KnowledgeStore(
            ChunkTable(database["writer"], database["reader"]),
            GraphWriter(neo4j_driver),
            client,
            BUCKET,
            spy,
            _contracts(),
        )
        yield store, spy, client, database["admin"], neo4j_driver


def _put(client, key: str, body: bytes, acl: str, version: str, digest: str) -> None:
    client.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=body,
        Metadata={
            "kbs-content-hash": digest,
            "kbs-version": version,
            "kbs-native-acl": acl,
        },
    )


def _event(object_id: str, key: str, version: str, digest: str, event_type: str = "create") -> ChangeEvent:
    return ChangeEvent(
        source_id="hr",
        object_id=object_id,
        event_type=event_type,
        version=version,
        content_hash=digest,
        occurred_at=datetime.now(UTC),
        s3_pointer=key,
    )


def test_upsert_is_idempotent_and_acl_does_not_embed(world) -> None:
    store, spy, client, admin, driver = world
    key = "raw/hr/policy/pol-1.md"
    _put(client, key, POLICY, "hr_employee", "1", "hash-1")
    store.upsert_object(_event("pol-1", key, "1", "hash-1"))
    assert spy.calls == 2
    store.upsert_object(_event("pol-1", key, "1", "hash-1", "update"))
    assert spy.calls == 2
    with admin.connect() as connection:
        rows = connection.execute(text("SELECT count(*) AS n FROM chunks")).one().n
    assert rows == 2
    _put(client, key, POLICY, "hr_policy_admin", "1", "hash-acl")
    store.apply_acl(_event("pol-1", key, "1", "hash-acl", "acl_change"))
    assert spy.calls == 2
    with admin.connect() as connection:
        groups = connection.execute(text("SELECT acl_groups FROM chunks LIMIT 1")).one().acl_groups
    assert groups == ["hr_admins"]
    assert driver.execute_query(
        "MATCH (p:Policy {id: $id}) RETURN p.acl_groups AS groups", id="pol-1"
    )[0][0]["groups"] == ["hr_admins"]


def test_version_bump_replaces_chunks(world) -> None:
    store, spy, client, admin, _driver = world
    key = "raw/hr/policy/pol-1.md"
    _put(client, key, POLICY, "hr_employee", "1", "hash-1")
    store.upsert_object(_event("pol-1", key, "1", "hash-1"))
    changed = b"# Leave\n\n## Eligibility\n\nChanged body.\n"
    _put(client, key, changed, "hr_employee", "2", "hash-2")
    store.upsert_object(_event("pol-1", key, "2", "hash-2", "update"))
    assert spy.calls == 3
    with admin.connect() as connection:
        bodies = [row.content for row in connection.execute(text("SELECT content FROM chunks"))]
    assert len(bodies) == 1
    assert any("Changed body." in body for body in bodies)


def test_delete_is_verified_and_replay_does_not_resurrect(world) -> None:
    store, _spy, client, admin, driver = world
    key = "raw/hr/person/emp-1.json"
    _put(client, key, PERSON, "hr_employee", "1", "hash-p")
    logs = _attach_worker_logs()
    store.upsert_object(_event("emp-1", key, "1", "hash-p"))
    written = logs.getvalue()
    for forbidden in ("salary", "compensation", "ssn", "national_id", "90000", "NID-SECRET", "Ada Lovelace"):
        assert forbidden not in written
    assert "record-v1" in written
    team = driver.execute_query(
        "MATCH (:Person {id: 'emp-1'})-[:BELONGS_TO]->(t:Team) RETURN t.id AS id"
    )[0]
    assert team[0]["id"] == "People"
    props = driver.execute_query("MATCH (p:Person {id: 'emp-1'}) RETURN properties(p) AS props")[0][0]["props"]
    assert "90000" not in str(props)
    assert "NID-SECRET" not in str(props)
    with admin.connect() as connection:
        chunk_text = " ".join(row.content for row in connection.execute(text("SELECT content FROM chunks")))
    assert "90000" not in chunk_text
    assert "NID-SECRET" not in chunk_text
    store.delete_object(
        ChangeEvent(
            source_id="hr",
            object_id="emp-1",
            event_type="delete",
            version="1",
            content_hash="hash-p",
            occurred_at=datetime.now(UTC),
        )
    )
    assert store.chunks.count_chunks("hr", "emp-1") == 0
    assert store.graph.count_node("hr", "emp-1") == 0
    with admin.connect() as connection:
        tomb = connection.execute(text("SELECT count(*) AS n FROM tombstones")).one().n
    assert tomb == 1
    store.upsert_object(_event("emp-1", key, "1", "hash-p"))
    assert store.chunks.count_chunks("hr", "emp-1") == 0
    _put(client, key, PERSON, "hr_employee", "2", "hash-p2")
    store.upsert_object(_event("emp-1", key, "2", "hash-p2", "update"))
    assert store.chunks.count_chunks("hr", "emp-1") == 1


def test_two_principals_and_rls(world) -> None:
    store, _spy, client, admin, _driver = world
    key = "raw/hr/policy/pol-1.md"
    _put(client, key, POLICY, "hr_employee,hr_policy_admin", "1", "hash-1")
    store.upsert_object(_event("pol-1", key, "1", "hash-1"))
    employee = Principal("ada", ("employees",), "internal", "engineering")
    outsider = Principal("bob", ("sales_team",), "internal", "sales")
    admin_low = Principal("cam", ("hr_admins",), "internal", "people")
    blank = Principal("ada", ("employees",), "internal", "")
    assert store.chunks.count_visible(None) == 0
    assert store.chunks.count_visible(blank) == 0
    assert store.chunks.count_visible(employee) == 2
    assert store.chunks.count_visible(outsider) == 0
    found = store.search("Germany", employee, limit=5, request_id="req-1")
    assert found
    assert "Germany" in found[0].text
    assert store.search("Germany", outsider, request_id="req-2") == []
    # Coarse columns include hr_admins at internal. The admin rule requires confidential.
    assert store.search("Germany", admin_low, request_id="req-3") == []
    with admin.connect() as connection:
        decisions = {
            (row.principal, row.decision, row.chunk_id is not None)
            for row in connection.execute(text("SELECT principal, decision, chunk_id FROM audit_log"))
        }
    assert ("ada", "allow", True) in decisions
    assert ("bob", "deny", False) in decisions
    assert ("cam", "deny", True) in decisions


def test_admin_only_policy_is_hidden_from_an_engineer(world) -> None:
    store, _spy, client, _admin, _driver = world
    key = "raw/hr/policy/pol-admin.md"
    _put(client, key, POLICY, "hr_policy_admin", "1", "hash-admin")
    store.upsert_object(_event("pol-admin", key, "1", "hash-admin"))
    engineer = Principal("eve", ("employees",), "internal", "engineering")
    admin = Principal("ira", ("hr_admins",), "confidential", "people")
    assert store.chunks.count_visible(engineer) == 0
    assert store.search("Germany", engineer, request_id="eng-admin") == []
    found = store.search("Germany", admin, request_id="admin-ok")
    assert found
    assert "Germany" in found[0].text


def test_named_department_still_excludes_other_departments(world) -> None:
    store, _spy, _client, _admin, _driver = world
    embedder = HashEmbedder()
    draft = ChunkDraft(0, "body", "Quarterly forecast for the account.", "record-v1")
    vector = embedder.embed([draft.text])[0]
    acl = AclView(
        ("sales_team",),
        ("sales",),
        0,
        ({"group": "sales_team", "department": "sales", "clearance": "public"},),
        (),
    )
    store.chunks.replace_chunks(
        "sales", "deal-1", "deal", "1", "hash-deal", embedder.name, [draft], [vector], acl
    )
    sales = Principal("sam", ("sales_team",), "internal", "sales")
    engineer = Principal("eve", ("sales_team",), "internal", "engineering")
    assert store.chunks.count_visible(sales) == 1
    assert store.chunks.count_visible(engineer) == 0
    assert store.search("forecast", sales, request_id="sales-ok")
    assert store.search("forecast", engineer, request_id="sales-no") == []


def test_stored_employee_department_is_opened_by_the_migration(world) -> None:
    store, _spy, _client, admin, _driver = world
    embedder = HashEmbedder()
    draft = ChunkDraft(0, "Eligibility", "Parental leave in Germany.", "markdown-headings-v1")
    vector = embedder.embed([draft.text])[0]
    old = AclView(
        ("employees",),
        ("people",),
        1,
        ({"group": "employees", "department": "people", "clearance": "internal"},),
        (),
    )
    store.chunks.replace_chunks(
        "hr", "pol-old", "policy", "1", "hash-old", embedder.name, [draft], [vector], old
    )
    engineer = Principal("eve", ("employees",), "internal", "engineering")
    assert store.chunks.count_visible(engineer) == 0
    sql = (_root() / "database" / "postgres" / "migrations" / "003-abac-department.sql").read_text()
    with admin.begin() as connection:
        for statement in sql.split(";"):
            if statement.strip():
                connection.execute(text(statement))
    assert store.chunks.count_visible(engineer) == 1
    found = store.search("Germany", engineer, request_id="rewritten")
    assert found
    assert found[0].object_id == "pol-old"


def test_unmapped_acl_is_visible_to_nobody(world) -> None:
    store, _spy, client, _admin, _driver = world
    key = "raw/hr/policy/pol-1.md"
    _put(client, key, POLICY, "not-a-role", "1", "hash-1")
    store.upsert_object(_event("pol-1", key, "1", "hash-1"))
    assert store.chunks.count_chunks("hr", "pol-1") == 2
    employee = Principal("ada", ("employees",), "restricted", "people")
    assert store.chunks.count_visible(employee) == 0
    assert store.search("Germany", employee, request_id="req-4") == []


def test_reconcile_removes_orphans_and_backfills(world) -> None:
    store, _spy, client, admin, _driver = world
    key = "raw/hr/policy/pol-1.md"
    _put(client, key, POLICY, "hr_employee", "1", "hash-1")
    with admin.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO source_objects (
                    source_id, object_id, object_type, version, content_hash,
                    native_acl, s3_key, source_updated_at
                ) VALUES (
                    'hr', 'pol-1', 'policy', '1', 'hash-1', 'hr_employee', :key, '2026-01-15T09:00:00Z'
                )
                """
            ),
            {"key": key},
        )
    store.reconcile_index()
    assert store.chunks.count_chunks("hr", "pol-1") == 2
    with admin.begin() as connection:
        connection.execute(text("DELETE FROM source_objects WHERE object_id = 'pol-1'"))
    store.reconcile_index()
    assert store.chunks.count_chunks("hr", "pol-1") == 0


def test_deal_edge_survives_either_order_and_dies_with_the_epic(neo4j_driver) -> None:
    graph = GraphWriter(neo4j_driver)
    neo4j_driver.execute_query("MATCH (n) DETACH DELETE n")
    acl = AclView(("sales_team",), ("sales",), 0, ({"group": "sales_team", "department": "sales"},), ())
    graph.write("engineering", "epic", "epic-9", {"title": "Billing", "status": "unreleased"}, "Billing", acl, "1")
    graph.write("sales", "deal", "deal-1", {"account_id": "acme", "epic_id": "epic-9"}, "Acme", acl, "1")
    assert graph.depends_on("deal-1", "epic-9")
    graph.delete("engineering", "epic-9")
    assert not graph.depends_on("deal-1", "epic-9")
    neo4j_driver.execute_query("MATCH (n) DETACH DELETE n")
    graph.write("sales", "deal", "deal-1", {"account_id": "acme", "epic_id": "epic-9"}, "Acme", acl, "1")
    graph.write("engineering", "epic", "epic-9", {"title": "Billing", "status": "unreleased"}, "Billing", acl, "1")
    assert graph.depends_on("deal-1", "epic-9")


def test_hash_embedder_abstains_without_a_lexical_hit(world) -> None:
    store, _spy, client, admin, _driver = world
    key = "raw/hr/policy/pol-1.md"
    _put(client, key, POLICY, "hr_employee", "1", "hash-1")
    store.upsert_object(_event("pol-1", key, "1", "hash-1"))
    employee = Principal("ada", ("employees",), "internal", "engineering")
    assert store.search("xylophone quantum payroll", employee, request_id="no-answer") == []
    with admin.connect() as connection:
        decision = connection.execute(
            text("SELECT decision, chunk_id FROM audit_log WHERE request_id = 'no-answer'")
        ).one()
    assert decision.decision == "deny"
    assert decision.chunk_id is None


def test_hr_contract_loads() -> None:
    assert _contracts()["hr"].source_id == "hr"
