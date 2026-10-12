# ADRs

One decision per file. Spikes hold the longer reasoning. These records say what was chosen, what was rejected, and what that costs.

| ADR                                   | Decision                                                                |
| ------------------------------------- | ----------------------------------------------------------------------- |
| [0001](0001-postgres-hybrid-index.md) | Postgres, pgvector, and full-text. Not a separate vector database.      |
| [0002](0002-neo4j-graph.md)           | Neo4j for a small typed graph. Account and Spec constraints are unused. |
| [0003](0003-postgres-then-neo4j.md)   | Postgres commits first. A failed graph write retries.                   |
| [0004](0004-s3-snapshots.md)          | S3 holds the raw snapshot. Postgres holds the pointer.                  |
| [0005](0005-embedding-model.md)       | Local bge-small. `hash-v1` for tests. English only.                     |
| [0006](0006-chunk-versioning.md)      | Chunk and embedding versions. A rebuild, not a mixed index.             |
| [0007](0007-chunker-registry.md)      | One chunker per format. PDF sections are not recovered.                 |
| [0008](0008-hybrid-retrieval.md)      | Reciprocal rank fusion only. No reranker and no query router.           |
| [0009](0009-observability-stack.md)   | Alloy, Prometheus, Loki, Tempo, Grafana.                                |
| [0010](0010-dashboards-as-code.md)    | Dashboards in git. Red panels, no Alertmanager.                         |
| [0011](0011-alloy-docker-socket.md)   | Alloy's read-only Docker socket.                                        |
| [0012](0012-sqs-fifo.md)              | SQS FIFO, one queue per tier, a dead-letter queue per queue.            |
| [0013](0013-claim-check-messages.md)  | The queue carries a pointer, not the document.                          |
| [0014](0014-hard-deletes.md)          | Hard deletes, verified, with a reconciliation sweep.                    |
| [0015](0015-scheduling.md)            | In-process polls. Scheduler is not wired.                               |
| [0016](0016-connector-processes.md)   | One process per source. Not a plugin loader.                            |
| [0017](0017-source-contracts.md)      | YAML contracts. An invalid file refuses startup.                        |
| [0018](0018-authorization.md)         | ABAC in SQL, Cypher, and row-level security.                            |
| [0019](0019-identity.md)              | Mock HS256 JWTs. OIDC is the production path.                           |
| [0020](0020-fastapi.md)               | FastAPI for the HTTP services. Not compared in a spike.                 |
| [0021](0021-mcp.md)                   | The official MCP SDK. The FastMCP name is not what runs.                |
| [0022](0022-local-platform.md)        | floci, Terraform, and one Compose file per trust area.                  |
| [0023](0023-uv-workspace.md)          | A uv workspace. One image per deployable.                               |
| [0024](0024-demo-harness.md)          | FastAPI and SQLite harnesses. HTTP only.                                |
| [0025](0025-chat-answer.md)           | LangChain writes the answer. The gateway does not.                      |
