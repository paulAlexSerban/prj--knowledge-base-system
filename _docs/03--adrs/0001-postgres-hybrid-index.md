# ADR 0001: Postgres for metadata, hybrid search, and authorization

Status: accepted for the POC.

## Context

Retrieval has to filter by permission, return chunk text, and search both by meaning and by exact identifier. Spike 02 depends on that. The assumed production rate is about 1.2 change events per second. That figure is an assumption, not a measurement. The retrieval target is 250 ms p95, excluding answer generation.

## Decision

Postgres 18 with pgvector (`pgvector/pgvector:pg18`) is the system of record for chunks. A row holds the text, the ACL attributes, the content hash, and a `vector(1024)` embedding. Full-text search is a generated English `tsvector` with a GIN index. The vector index is HNSW using cosine distance. `gateway_ro` reads under row-level security. `ingestion_rw` writes every row. The table owner bypasses row-level security, so the API must not connect as that user. Application code uses SQLAlchemy models for these tables ([ADR 0028](0028-orm-and-ogm.md)). Reciprocal rank fusion of the two result lists is application code ([ADR 0008](0008-hybrid-retrieval.md)).

## Alternatives rejected

- A dedicated vector database (Qdrant, Pinecone, Weaviate). ACL, lineage, and row-level security would still need Postgres, so this adds a store and removes none.
- OpenSearch beside Postgres. A real split at a much larger corpus. It is a second query language and a second authorization implementation for a volume that does not need them.
- BM25 in a separate engine. Postgres full-text is enough for identifiers and policy names at this corpus size, under the same row-level security. `ts_rank` is not BM25. That difference was not measured as a quality gap.

## Consequences

- The permission pre-filter is a normal SQL predicate next to the vectors. One authorization implementation, not two.
- Moving vectors to another engine later is possible because chunk identity and ACL stay in Postgres. That move is not free and is not scheduled.
- Nothing here was measured at 10 million chunks.

## Production path

Aurora PostgreSQL or RDS with the `vector` extension. A connection pool must set the principal for the transaction, not for the session.
