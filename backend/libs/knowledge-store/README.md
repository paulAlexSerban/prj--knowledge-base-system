# knowledge-store

Library. No image.

Turns one change event into chunks in Postgres and nodes in Neo4j. A deal or an epic is one chunk. A spec splits on headings and has no graph node. A deal depends on an epic. An epic is owned by an HR team. The ingestion worker calls it. `EMBEDDER=hash` selects the offline and test embedder: model name `hash-v1`, 1024 deterministic dimensions, not a semantic model. Lexical search still matches exact terms. `EMBEDDER=fastembed` uses multilingual-e5-large (`intfloat/multilingual-e5-large`, 1024 dimensions, `passage: ` on stored text and `query: ` on questions). `fastembed` is a dependency of this package, and the worker and gateway images each bake the weights in at build time (about 2.2 GB per image). Switching models is a rebuild ([ADR 0006](../../../_docs/03--adrs/0006-chunk-versioning.md)), not a mixed index.
