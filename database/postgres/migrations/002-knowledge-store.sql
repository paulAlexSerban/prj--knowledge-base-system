-- Idempotent. Applied on every start by postgres-migrate.
-- Chunks gain the ADR 0003 columns, ACL columns, full-text search, and an HNSW
-- index. Row-level security for gateway_ro is the coarse ACL check. ingestion_rw
-- writes every row. The table owner bypasses RLS, so the API must not use it.

CREATE OR REPLACE FUNCTION kbs.clearance_rank(label text) RETURNS smallint
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE label
        WHEN 'public' THEN 0
        WHEN 'internal' THEN 1
        WHEN 'confidential' THEN 2
        WHEN 'restricted' THEN 3
        ELSE -1
    END::smallint;
$$;

GRANT EXECUTE ON FUNCTION kbs.clearance_rank(text) TO gateway_ro, ingestion_rw;

ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS object_type text NOT NULL DEFAULT '';
ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS chunk_index integer NOT NULL DEFAULT 0;
ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS version text NOT NULL DEFAULT '';
ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS section_path text NOT NULL DEFAULT '';
ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS content_hash text NOT NULL DEFAULT '';
ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS chunker_version text NOT NULL DEFAULT '';
ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS embedding_model text NOT NULL DEFAULT '';
ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS acl_groups text[] NOT NULL DEFAULT '{}';
ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS acl_departments text[] NOT NULL DEFAULT '{}';
ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS min_clearance smallint NOT NULL DEFAULT 99;
ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS acl_rules jsonb NOT NULL DEFAULT '[]';
ALTER TABLE kbs.chunks ADD COLUMN IF NOT EXISTS synced_at timestamptz NOT NULL DEFAULT now();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'kbs' AND table_name = 'chunks' AND column_name = 'fts'
    ) THEN
        ALTER TABLE kbs.chunks ADD COLUMN fts tsvector
            GENERATED ALWAYS AS (
                to_tsvector(
                    'english',
                    coalesce(section_path, '') || ' ' || coalesce(content, '')
                )
            ) STORED;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS chunks_identity
    ON kbs.chunks (source_id, object_id, chunk_index, chunker_version, embedding_model);
CREATE INDEX IF NOT EXISTS chunks_fts ON kbs.chunks USING gin (fts);
CREATE INDEX IF NOT EXISTS chunks_acl_groups ON kbs.chunks USING gin (acl_groups);
CREATE INDEX IF NOT EXISTS chunks_source_object ON kbs.chunks (source_id, object_id);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON kbs.chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS kbs.tombstones (
    source_id text NOT NULL,
    object_id text NOT NULL,
    version text NOT NULL DEFAULT '',
    content_hash text NOT NULL DEFAULT '',
    deleted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, object_id)
);

ALTER TABLE kbs.audit_log ADD COLUMN IF NOT EXISTS request_id text;
ALTER TABLE kbs.audit_log ADD COLUMN IF NOT EXISTS decision text;
ALTER TABLE kbs.audit_log ADD COLUMN IF NOT EXISTS source_id text;

CREATE OR REPLACE FUNCTION kbs.audit_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$;

DROP TRIGGER IF EXISTS audit_log_no_update ON kbs.audit_log;
CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE OR DELETE ON kbs.audit_log
    FOR EACH ROW EXECUTE FUNCTION kbs.audit_append_only();

DROP POLICY IF EXISTS chunks_deny_without_principal ON kbs.chunks;
DROP POLICY IF EXISTS chunks_read_by_acl ON kbs.chunks;
CREATE POLICY chunks_read_by_acl ON kbs.chunks
    FOR SELECT
    TO gateway_ro
    USING (
        coalesce(current_setting('kbs.groups', true), '') <> ''
        AND coalesce(current_setting('kbs.clearance', true), '') <> ''
        AND coalesce(current_setting('kbs.department', true), '') <> ''
        AND acl_groups && string_to_array(current_setting('kbs.groups', true), ',')
        AND current_setting('kbs.department', true) = ANY (acl_departments)
        AND min_clearance <= kbs.clearance_rank(current_setting('kbs.clearance', true))
    );

DROP POLICY IF EXISTS chunks_ingestion_all ON kbs.chunks;
CREATE POLICY chunks_ingestion_all ON kbs.chunks
    FOR ALL
    TO ingestion_rw
    USING (true)
    WITH CHECK (true);

GRANT USAGE ON SCHEMA kbs TO ingestion_rw, gateway_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON kbs.chunks TO ingestion_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON kbs.tombstones TO ingestion_rw;
GRANT USAGE, SELECT ON SEQUENCE kbs.chunks_id_seq TO ingestion_rw;
GRANT SELECT ON kbs.chunks TO gateway_ro;
GRANT INSERT ON kbs.audit_log TO gateway_ro;
GRANT USAGE, SELECT ON SEQUENCE kbs.audit_log_id_seq TO gateway_ro;
REVOKE UPDATE, DELETE ON kbs.audit_log FROM gateway_ro, ingestion_rw;
