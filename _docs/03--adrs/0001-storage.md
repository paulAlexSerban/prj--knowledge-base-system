# ADR 0001: Storage for metadata, hybrid search, and the graph

Status: accepted for the POC.

## Context

The gateway needs a system of record for document lineage, ACL attributes, and sync state; a hybrid index (dense vectors and lexical search); and a graph for a small set of typed relationships. Spike 01 and spike 02 depend on this choice. Production volume is about 1.2 change events per second on average, with a retrieval target of 250 ms p95 excluding model generation.

## Decision

- Postgres holds metadata, source registry, ACL attributes, sync state, content hashes, chunk text, pgvector embeddings, and full-text search. Row-level security enforces the principal on every query (spike 03). Reciprocal rank fusion runs in application code over the two Postgres result lists.
- Neo4j holds the knowledge graph (Person, Team, Account, Deal, Epic, Spec, Policy, and the four edge types in spike 02).
- S3 on floci holds raw snapshots and document blobs. Postgres stores the pointer. The HR connector writes one key per object, `raw/<source_id>/<object_type>/<object_id>.<ext>`, and overwrites it on change. Bucket versioning keeps the previous bytes. A delete removes every version. Object metadata carries `kbs-version`, `kbs-content-hash`, `kbs-native-acl`, `kbs-source-updated-at`, and `kbs-snapshot-at`. `kbs.sync_state` holds the watermark. `kbs.source_objects` holds the hash, version, ACL, and S3 key. The connector connects as `ingestion_rw` with `search_path=kbs`. floci accepted bucket versioning and the public access block. The apply container does not keep Terraform state, and floci is in memory, so a second apply while the bucket still exists stops at `BucketAlreadyExists` until floci is restarted.

The document row in Postgres is authoritative for prose. The graph is a derived index and is written in the same ingestion unit of work.

## Why

One relational database covers the parts that must be correct and filterable: permissions, versions, hashes, and lexical match on identifiers. pgvector keeps the dense index next to the ACL columns, so the pre-filter is a normal SQL predicate and does not require a second authorization implementation. Neo4j is there because the roadmap question is a short typed traversal that recursive SQL would obscure, and because showing the path is part of the demo. The corpus is small enough that operating both locally in Compose is cheap.

## Alternatives rejected

| Alternative | Why it was rejected for the POC |
| ----------- | ------------------------------- |
| Postgres only, graph as edge tables and recursive CTEs | Fewer containers, and a reasonable production simplification if the ontology stays this small. Rejected for the POC because the graph traversal and path explanation are a primary demo, and Cypher states them more clearly than a recursive query. Revisit if Neo4j becomes the thing that fails in setup. |
| OpenSearch plus Neo4j plus Postgres | Closest to a large-scale split of lexical/vector, graph, and metadata. Three query languages and three authorization implementations for a volume that does not need them. |
| A dedicated vector database (Qdrant, Pinecone, Weaviate) | Strong vector engines. ACL pre-filtering, relational lineage, and row-level security would still need Postgres, so this adds a store without removing one. |
| Graph stored only as triples inside the vector payload | No real traversal, no path to show, and authorization on edges becomes an application loop. |

## Consequences

- Two stores can diverge. Ingestion retries the pair; upserts are idempotent (spike 04).
- Operators run Postgres and Neo4j in Compose. Schema lives under `database/`. The API connects as `gateway_ro`. Ingestion connects as `ingestion_rw`. The table owner bypasses row-level security. The policy on `kbs.chunks` is a placeholder: any non-empty `kbs.principal` session setting can see every row. Replace it before demo data is loaded.
- A later move of vectors to a dedicated engine is possible because chunk identity and ACL attributes stay in Postgres. That move is not free and is not scheduled.

## Production path

Stay on Postgres plus Neo4j until a measured gap appears (vector recall at 10 million chunks, or graph query latency). If vectors move, Postgres remains the authorization and lineage authority.
