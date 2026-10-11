#!/bin/bash
# Runs once, on an empty data directory. Passwords come from the compose env.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE ingestion_rw LOGIN PASSWORD '${INGESTION_DB_PASSWORD}';
CREATE ROLE gateway_ro LOGIN PASSWORD '${GATEWAY_DB_PASSWORD}';

CREATE SCHEMA IF NOT EXISTS kbs;
-- The app sets search_path=kbs and does not see public, so the type lives here.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA kbs;

-- 384 matches the local embedders (hash-v1 and bge-small). ADR 0003.
CREATE TABLE kbs.chunks (
    id bigserial PRIMARY KEY,
    source_id text NOT NULL,
    object_id text NOT NULL,
    content text,
    embedding vector(384)
);

CREATE TABLE kbs.audit_log (
    id bigserial PRIMARY KEY,
    principal text,
    chunk_id bigint,
    attributes jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE kbs.chunks ENABLE ROW LEVEL SECURITY;

-- The read policy is migrations/002-knowledge-store.sql. With no policy yet,
-- a non-owner sees nothing. The table owner bypasses RLS, so the API must
-- connect as gateway_ro.

GRANT USAGE ON SCHEMA kbs TO ingestion_rw, gateway_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON kbs.chunks TO ingestion_rw;
GRANT USAGE, SELECT ON SEQUENCE kbs.chunks_id_seq TO ingestion_rw;
GRANT SELECT ON kbs.chunks, kbs.audit_log TO gateway_ro;
SQL
