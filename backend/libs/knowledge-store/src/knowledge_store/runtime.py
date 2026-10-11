"""Build the store from the process environment and wait until it answers."""

import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from neo4j import GraphDatabase
from sqlalchemy import create_engine

from ingest_contract.events import RAW_BUCKET
from knowledge_store import metrics
from knowledge_store.embedder import build_embedder
from knowledge_store.graph import GraphWriter
from knowledge_store.postgres import ChunkTable
from knowledge_store.store import KnowledgeStore
from shared.registry import load_registry


def open_store() -> KnowledgeStore:
    writer = _engine(os.environ.get("POSTGRES_USER", "ingestion_rw"), os.environ["INGESTION_DB_PASSWORD"])
    reader = _engine("gateway_ro", os.environ["GATEWAY_DB_PASSWORD"])
    driver = GraphDatabase.driver(
        os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
        auth=("neo4j", os.environ["NEO4J_PASSWORD"]),
    )
    return KnowledgeStore(
        ChunkTable(writer, reader),
        GraphWriter(driver),
        _s3(),
        os.environ.get("RAW_BUCKET", RAW_BUCKET),
        build_embedder(os.environ.get("EMBEDDER", "hash")),
        {contract.source_id: contract for contract in load_registry()},
    )


def ready(store: KnowledgeStore) -> bool:
    postgres_up = store.chunks.ping()
    metrics.DEPENDENCY.labels(dependency="postgres").set(1 if postgres_up else 0)
    neo4j_up = store.graph.ping()
    metrics.DEPENDENCY.labels(dependency="neo4j").set(1 if neo4j_up else 0)
    try:
        store.snapshots.head_bucket(Bucket=store.bucket)
    except (BotoCoreError, ClientError, OSError):
        metrics.DEPENDENCY.labels(dependency="s3").set(0)
        s3_up = False
    else:
        metrics.DEPENDENCY.labels(dependency="s3").set(1)
        s3_up = True
    return postgres_up and neo4j_up and s3_up


def _engine(user: str, password: str):
    host = os.environ.get("POSTGRES_HOST", "postgres")
    database = os.environ.get("POSTGRES_DB", "kbs")
    return create_engine(
        f"postgresql+psycopg://{user}:{password}@{host}:5432/{database}",
        connect_args={"options": "-csearch_path=kbs"},
    )


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        config=Config(s3={"addressing_style": "path"}),
    )
