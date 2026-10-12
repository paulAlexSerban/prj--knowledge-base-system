# ADR 0008: Hybrid search with reciprocal rank fusion, and nothing else

Status: accepted for the POC.

## Context

Dense search misses exact identifiers. Lexical search misses paraphrases. The two scores are not on one scale. Spike 02 also describes a query router. The golden set is how a reranker would be justified.

## Decision

Search runs pgvector cosine distance and Postgres full-text `ts_rank`, then merges the lists in application code with reciprocal rank fusion at `k = 60`. A vector-only neighbour is kept only when the active model is semantic and the distance is within `VECTOR_MAX_DISTANCE` (default 0.40). `hash-v1` neighbours are always dropped. There is no cross-encoder, no community-summary GraphRAG, and no query router. The caller chooses search or `find_related`. The chat page sends the suggested roadmap question to `roadmap_deals` and a typed question to search.

One seeded run, recorded in the README, scored recall at 5 of 1.00, paraphrase recall of 1.00, and MRR of 0.95, at 216 ms, on about 500 chunks. The committed gate is lower: recall at 5 of 0.90, paraphrase recall of 0.80, MRR of 0.70. The set is 20 cases written for this demo.

## Alternatives rejected

- A cross-encoder over the top results. Extra latency against the 250 ms budget, and this golden set shows no precision gap. The candidate if that changes is named in spike 02. It is not in the images.
- Community summaries and global GraphRAG. The demo questions are a policy lookup and one path. A summary job would not serve them.
- Vector search only. It fails exact identifiers and it cannot answer "depends on".
- A classifier that picks search or the graph. Not built. Routing mistakes are visible because the caller picks the tool.

## Consequences

- A clean score on 20 self-authored cases is weak evidence for another corpus, and it says nothing about 10 million chunks. The 0.40 floor was set from distances on this corpus and has to be re-measured if the corpus changes.
- `ts_rank` is not BM25. That was not a measured gap ([ADR 0001](0001-postgres-hybrid-index.md)).
