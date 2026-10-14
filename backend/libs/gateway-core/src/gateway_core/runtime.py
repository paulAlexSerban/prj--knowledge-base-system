"""Open a reader-only store. The gateway has no ingestion password and no graph writes."""

import os

from neo4j import GraphDatabase
from sqlalchemy import create_engine

from gateway_core.graph_read import GraphReader
from gateway_core.recheck import HttpAclRecheck
from gateway_core.service import GatewayService
from ingest_contract.events import RAW_BUCKET
from knowledge_store.embedder import build_embedder
from knowledge_store.graph import GraphWriter
from knowledge_store.postgres import ChunkTable
from knowledge_store.store import KnowledgeStore
from shared.registry import load_registry


def open_gateway() -> GatewayService:
    reader = _engine("gateway_ro", os.environ["GATEWAY_DB_PASSWORD"])
    driver = GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
        auth=("neo4j", os.environ["NEO4J_PASSWORD"]),
    )
    # The chunk writer is None, so this process cannot insert chunks.
    # GraphWriter is required by KnowledgeStore and is never called.
    store = KnowledgeStore(
        ChunkTable(None, reader),
        GraphWriter(driver),
        None,
        os.environ.get("RAW_BUCKET", RAW_BUCKET),
        build_embedder(os.environ.get("EMBEDDER", "hash")),
        {contract.source_id: contract for contract in load_registry()},
    )
    recheck = None
    hr = store.contracts.get("hr")
    if hr is not None and hr.acl_recheck_at_read:
        recheck = HttpAclRecheck(os.environ.get("HARNESS_HR_URL", ""))
    return GatewayService(store, GraphReader(driver), recheck)


def _engine(user: str, password: str):
    host = os.environ.get("POSTGRES_HOST", "postgres")
    database = os.environ.get("POSTGRES_DB", "kbs")
    return create_engine(
        f"postgresql+psycopg://{user}:{password}@{host}:5432/{database}",
        connect_args={"options": "-csearch_path=kbs"},
    )
