# knowledge-store

Library. No image.

Turns one change event into chunks in Postgres and nodes in Neo4j. The ingestion worker calls it. `EMBEDDER=hash` is the default and the Compose setting: model name `hash-v1`, 384 deterministic dimensions, not a semantic model. Lexical search still matches exact terms. `EMBEDDER=fastembed` uses bge-small when the `fastembed` package is installed. That package is not locked. Switching models is a rebuild (ADR 0003), not a mixed index.
