-- Idempotent. Applied on every start after 004.
-- multilingual-e5-large and hash-v1 are 1024 dimensions (ADR 0005).
-- A column that is still vector(384) cannot be cast, so those rows are
-- deleted and the index is rebuilt empty. `make reindex` (or `make seed`)
-- fills it again. A column that is already vector(1024) is left untouched,
-- including its rows. 1024 is under the pgvector HNSW limit of 2000.

DO $$
DECLARE
    column_type text;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod)
    INTO column_type
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'kbs'
      AND c.relname = 'chunks'
      AND a.attname = 'embedding'
      AND NOT a.attisdropped;

    IF column_type IS NULL OR column_type NOT LIKE '%vector(1024)%' THEN
        DROP INDEX IF EXISTS chunks_embedding_hnsw;
        DELETE FROM kbs.chunks;
        ALTER TABLE kbs.chunks ALTER COLUMN embedding TYPE vector(1024);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON kbs.chunks USING hnsw (embedding vector_cosine_ops);
