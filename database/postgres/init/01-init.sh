#!/bin/bash
# Runs once, on an empty data directory. Passwords come from the compose env.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE EXTENSION IF NOT EXISTS vector;

CREATE ROLE ingestion_rw LOGIN PASSWORD '${INGESTION_DB_PASSWORD}';
CREATE ROLE gateway_ro LOGIN PASSWORD '${GATEWAY_DB_PASSWORD}';

CREATE SCHEMA IF NOT EXISTS kbs;

-- 384 is a stub dimension. ADR 0003 picks the real model before any embed is stored.
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

-- PLACEHOLDER. Not ABAC. Any non-empty kbs.principal session setting sees every row.
-- The table owner bypasses RLS. The API must connect as gateway_ro, not as the owner.
CREATE POLICY chunks_deny_without_principal ON kbs.chunks
    FOR ALL
    TO gateway_ro
    USING (
        current_setting('kbs.principal', true) IS NOT NULL
        AND current_setting('kbs.principal', true) <> ''
    )
    WITH CHECK (
        current_setting('kbs.principal', true) IS NOT NULL
        AND current_setting('kbs.principal', true) <> ''
    );

GRANT USAGE ON SCHEMA kbs TO ingestion_rw, gateway_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON kbs.chunks TO ingestion_rw;
GRANT USAGE, SELECT ON SEQUENCE kbs.chunks_id_seq TO ingestion_rw;
GRANT SELECT ON kbs.chunks, kbs.audit_log TO gateway_ro;
SQL
