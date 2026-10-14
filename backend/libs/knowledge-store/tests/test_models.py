"""ORM models stay aligned with the connector tables, and search stays an expression."""

from pathlib import Path

from sqlalchemy.dialects import postgresql

from connector_core.state import SourceObject as ConnectorObject
from connector_core.state import SyncState as ConnectorState
from knowledge_store.acl import Principal
from knowledge_store.models import SourceObject, SyncState
from knowledge_store.postgres import _lexical_select, _vector_select

ROOT = Path(__file__).resolve().parents[1]
PRINCIPAL = Principal("ada", ("employees",), "internal", "engineering")


def test_sync_columns_match_the_connector_models() -> None:
    assert set(SyncState.__table__.columns.keys()) == set(ConnectorState.__table__.columns.keys())
    assert set(SourceObject.__table__.columns.keys()) == set(
        ConnectorObject.__table__.columns.keys()
    )


def test_chunk_access_does_not_use_text_sql() -> None:
    source = (ROOT / "src" / "knowledge_store" / "postgres.py").read_text(encoding="utf-8")
    graph = (ROOT / "src" / "knowledge_store" / "graph.py").read_text(encoding="utf-8")
    assert "text(" not in source
    assert "MERGE " not in graph
    assert "MATCH " not in graph
    assert "cypher_query" not in graph
    assert "install_labels" not in graph


def test_search_compiles_the_acl_predicate() -> None:
    dialect = postgresql.dialect()
    vector_sql = str(
        _vector_select(PRINCIPAL, "hash-v1", [0.0, 1.0], 3, None).compile(dialect=dialect)
    )
    lexical_sql = str(
        _lexical_select(PRINCIPAL, "hash-v1", "leave", 3, ["pol-1"]).compile(dialect=dialect)
    )
    empty_sql = str(
        _vector_select(PRINCIPAL, "hash-v1", [0.0], 1, []).compile(dialect=dialect)
    )
    assert "&&" in vector_sql
    assert "<=>" in vector_sql
    assert "clearance_rank" in vector_sql
    assert "@@" in lexical_sql
    assert "plainto_tsquery" in lexical_sql
    assert "pol-1" in lexical_sql or "object_id" in lexical_sql
    assert "false" in empty_sql.lower()
