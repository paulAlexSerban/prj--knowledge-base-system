# Design
- four department sources, connectors that snapshot and enqueue, one worker that writes Postgres first then Neo4j, and one retrieval service behind REST and MCP
- authorization is in the data path (SQL/Cypher pre-filter, post-check, RLS, audit)
- deletes are hard deletes with verification
- models are not allowed to change permissions or invent Cypher

## Components
- Connectors fetch, hash, put S3, then enqueue a pointer; they never embed.
- Ingestion skips embed on a matching content hash, still merges the graph, and leaves the message unacked if Neo4j fails.
- Deletes tombstone, verify both stores, and block a replayed older create.
- Gateway routes by rules, searches authorized chunks, re-checks HR policy ACL at read time, and wraps snippets as untrusted data.
- Graph is a small typed index (deal → epic → team). Specs stay prose.