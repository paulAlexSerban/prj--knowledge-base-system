"""Postgres and Neo4j for store tests. Skips when Docker cannot start them."""

import subprocess
import time
from pathlib import Path

import pytest
from neo4j import GraphDatabase
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS = ROOT / "database" / "postgres" / "migrations"

BOOTSTRAP = """
CREATE SCHEMA IF NOT EXISTS kbs;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA kbs;
DO $$ BEGIN
    CREATE ROLE ingestion_rw LOGIN PASSWORD 'test-ingestion';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
    CREATE ROLE gateway_ro LOGIN PASSWORD 'test-gateway';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
CREATE TABLE IF NOT EXISTS kbs.chunks (
    id bigserial PRIMARY KEY,
    source_id text NOT NULL,
    object_id text NOT NULL,
    content text,
    embedding vector(384)
);
CREATE TABLE IF NOT EXISTS kbs.audit_log (
    id bigserial PRIMARY KEY,
    principal text,
    chunk_id bigint,
    attributes jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE kbs.chunks ENABLE ROW LEVEL SECURITY;
GRANT USAGE ON SCHEMA kbs TO ingestion_rw, gateway_ro;
"""


def _published_port(name: str, container_port: str) -> str:
    output = subprocess.check_output(["docker", "port", name, container_port], text=True)
    return output.splitlines()[0].rsplit(":", 1)[-1]


def _docker_ok() -> bool:
    return subprocess.call(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def _psql(name: str, sql: str) -> None:
    subprocess.run(
        ["docker", "exec", "-i", name, "psql", "-U", "kbs", "-d", "kbs", "-v", "ON_ERROR_STOP=1"],
        input=sql,
        text=True,
        check=True,
    )


def _wait_postgres(url: str) -> None:
    engine = create_engine(url)
    deadline = time.time() + 90
    last = ""
    while time.time() < deadline:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
            return
        except Exception as exc:  # noqa: BLE001 - the container is still starting
            last = str(exc)
            time.sleep(1)
    raise RuntimeError(last)


@pytest.fixture(scope="session")
def database():
    if not _docker_ok():
        pytest.skip("docker is not available")
    name = "kbs-test-pg"
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "-e", "POSTGRES_PASSWORD=test",
            "-e", "POSTGRES_USER=kbs",
            "-e", "POSTGRES_DB=kbs",
            "-p", "127.0.0.1::5432",
            "pgvector/pgvector:pg18",
        ],
        check=True,
        capture_output=True,
    )
    port = _published_port(name, "5432")
    admin_url = f"postgresql+psycopg://kbs:test@127.0.0.1:{port}/kbs"
    try:
        _wait_postgres(admin_url)
        _psql(name, BOOTSTRAP)
        _psql(name, (MIGRATIONS / "001-sync-state.sql").read_text())
        _psql(name, (MIGRATIONS / "002-knowledge-store.sql").read_text())
    except Exception as exc:
        logs = subprocess.run(
            ["docker", "logs", name], check=False, capture_output=True, text=True
        )
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
        raise RuntimeError(logs.stdout[-2000:] + logs.stderr[-2000:]) from exc
    options = {"options": "-csearch_path=kbs"}
    yield {
        "admin": create_engine(admin_url, connect_args=options),
        "writer": create_engine(
            f"postgresql+psycopg://ingestion_rw:test-ingestion@127.0.0.1:{port}/kbs",
            connect_args=options,
        ),
        "reader": create_engine(
            f"postgresql+psycopg://gateway_ro:test-gateway@127.0.0.1:{port}/kbs",
            connect_args=options,
        ),
    }
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


@pytest.fixture(scope="session")
def neo4j_driver():
    if not _docker_ok():
        pytest.skip("docker is not available")
    name = "kbs-test-neo4j"
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "-e", "NEO4J_AUTH=neo4j/test-neo4j",
            "-e", "NEO4J_server_memory_heap_initial__size=128m",
            "-e", "NEO4J_server_memory_heap_max__size=256m",
            "-e", "NEO4J_server_memory_pagecache_size=64m",
            "-p", "127.0.0.1::7687",
            "neo4j:5-community",
        ],
        check=True,
        capture_output=True,
    )
    port = _published_port(name, "7687")
    driver = GraphDatabase.driver(f"bolt://127.0.0.1:{port}", auth=("neo4j", "test-neo4j"))
    deadline = time.time() + 150
    last = ""
    while time.time() < deadline:
        try:
            driver.verify_connectivity()
            break
        except Exception as exc:  # noqa: BLE001 - neo4j takes a while
            last = str(exc)
            time.sleep(2)
    else:
        logs = subprocess.run(
            ["docker", "logs", name], check=False, capture_output=True, text=True
        )
        driver.close()
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
        raise RuntimeError(last + "\n" + logs.stdout[-1500:] + logs.stderr[-1500:])
    cypher = (ROOT / "database" / "neo4j" / "init.cypher").read_text()
    for statement in cypher.split(";"):
        if statement.strip():
            driver.execute_query(statement)
    yield driver
    driver.close()
    subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
