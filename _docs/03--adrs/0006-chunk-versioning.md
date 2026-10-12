# ADR 0006: Chunk and embedding versions, rebuilt in place

Status: accepted for the POC.

## Context

A chunk cut by an old splitter, or embedded by an old model, and then queried as if it were current, is a silent regression. The POC still has to keep serving while a rebuild runs.

## Decision

Every chunk stores `chunker_version`, `embedding_model`, `section_path`, and `content_hash`. The unique key is `(source_id, object_id, chunk_index, chunker_version, embedding_model)`. Queries filter to the active embedding model. An unchanged content hash skips the embedding call and still merges the graph. Changing the chunker or the model is a rebuild: `make reindex` re-embeds objects that have no chunk under the active model, then deletes the other models' chunks, and only when nothing is left missing. A failure halfway leaves the previous index serving. A second run is a no-op. The worker runs the same backfill every `RECONCILE_INDEX_SECONDS` (60 in Compose). Lexical search does not depend on the embedding model and stays valid across the rebuild. The POC ships one active version.

## Alternatives rejected

- An unversioned index, dropped and recreated on any change. Fine for a notebook. It cannot keep serving during a re-embed, and a citation no longer says what produced it.
- Embedding ACL attributes into the vector. Permissions change more often than body text. An ACL event would re-embed. They stay in columns ([ADR 0018](0018-authorization.md)).

## Consequences

- The golden set is the gate for a chunker change. It is 20 cases written for this demo ([ADR 0008](0008-hybrid-retrieval.md)).
- One object with a stale snapshot is skipped and logged. It does not fail the rebuild.

## Production path

Dual-write the old and new versions, compare recall on the golden set, publish a retirement date, then drop the old version. Put both version fields in the `/v1` changelog.
