# ADR 0008: Hybrid search with reciprocal rank fusion, and nothing else

Status: accepted for the POC.

## Context

Dense search misses exact identifiers. Lexical search misses paraphrases. The two scores are not on one scale. Spike 02 also describes a query router. The golden set is how a reranker would be justified.

## Decision

Search runs pgvector cosine distance and Postgres full-text `ts_rank`, then merges the lists in application code with reciprocal rank fusion at `k = 60`. A vector-only neighbour is kept only when the active model is semantic and the distance is within `VECTOR_MAX_DISTANCE`. `hash-v1` neighbours are always dropped. There is no cross-encoder and no community-summary GraphRAG. Which of search or the graph runs is [ADR 0026](0026-query-router.md). `/v1/search` and `/v1/graph/related` stay available when the caller already knows.

One seeded bge-small run, recorded in the README, scored recall at 5 of 1.00, paraphrase recall of 1.00, and MRR of 0.95, at 216 ms, on about 500 chunks. The e5-large run through `POST /v1/ask` scored the same recall, MRR of 0.90, and 372 ms p95, which misses the 250 ms target. The committed gate is lower: recall at 5 of 0.90, paraphrase recall of 0.80, MRR of 0.70, and a 500 ms regression guard. The set is 23 cases written for this demo.

## Alternatives rejected

- A cross-encoder over the top results. Extra latency against the 250 ms budget, and this golden set shows no precision gap. The candidate if that changes is named in spike 02. It is not in the images.
- Community summaries and global GraphRAG. The demo questions are a policy lookup and one path. A summary job would not serve them.
- Vector search only. It fails exact identifiers and it cannot answer "depends on".
- A model as the only classifier. Built later as a tie-break, not as the authority ([ADR 0026](0026-query-router.md)).

## Consequences

- A clean score on this self-authored set is weak evidence for another corpus, and it says nothing about 10 million chunks. The floor is 0.20 for multilingual-e5-large. The earlier bge-small floor was 0.40. It has to be re-measured if the corpus or the model changes.
- `ts_rank` is not BM25. That was not a measured gap ([ADR 0001](0001-postgres-hybrid-index.md)).
