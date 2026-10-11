-- Idempotent. Applied on every start by postgres-migrate, including an existing volume.
-- The connector connects as ingestion_rw with search_path=kbs.

CREATE TABLE IF NOT EXISTS kbs.sync_state (
    source_id text PRIMARY KEY,
    watermark text,
    last_success_at timestamptz,
    last_error text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kbs.source_objects (
    source_id text NOT NULL,
    object_id text NOT NULL,
    object_type text NOT NULL,
    version text NOT NULL,
    content_hash text NOT NULL,
    native_acl text NOT NULL,
    s3_key text NOT NULL,
    s3_version_id text,
    source_updated_at text NOT NULL,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, object_id)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON kbs.sync_state TO ingestion_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON kbs.source_objects TO ingestion_rw;
