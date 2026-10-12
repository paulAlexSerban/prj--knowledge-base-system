# Ingestion worker

Compose service `ingestion-worker`, metrics port 9101. It loads the contract registry, waits until Postgres, Neo4j, and the snapshot bucket answer, then drains `kbs-p0.fifo` before `kbs-p1.fifo` before `kbs-p2.fifo`.

Each message is written by `knowledge-store`: chunks and ACL columns in Postgres, then nodes and edges in Neo4j. A deal or an epic is one chunk. A spec splits on headings. A deal node stores arr, quarter, stage, and product_key and depends on an epic. An epic stores team and product_key and is owned by an HR team. A spec is prose and has no graph node. A failed graph write is not acknowledged. Every `RECONCILE_INDEX_SECONDS` (default 60) the worker deletes orphan chunks and backfills objects that have no chunk at the current version.

A message the handler rejects stays in flight until the visibility timeout (60 seconds) and is received again. After five receives the worker copies it to that tier's dead-letter queue. Terraform sets the same `maxReceiveCount`. Whether floci applies the redrive was not observed. `make dlq-replay` sends those messages back with a new deduplication id, `{original}:replay:{message_id}`, so FIFO does not drop the replay. `FAIL_OBJECT_ID` rejects one object id on purpose, for the dead-letter check.

`EMBEDDER=hash` selects `hash-v1` (384 dimensions, deterministic, not semantic). Queue names and the change-event model live in `ingest-contract`.
