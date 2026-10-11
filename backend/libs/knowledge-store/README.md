# knowledge-store

Library. No image.

Turns one change event into chunks in Postgres and nodes in Neo4j. The ingestion worker calls it. `EMBEDDER=hash` is the default: 384 deterministic dimensions, no model download. `EMBEDDER=fastembed` uses bge-small when the `fastembed` package is installed. Switching models is a rebuild (ADR 0003), not a mixed index.
