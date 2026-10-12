# Spike 02: Turning source data into knowledge agents can use

Status: decision for the POC. Production scale is called out and is not built.

## Problem

Agents fail in four different ways, and each one is a different subsystem:

| Failure | Where it is fixed |
| ------- | ----------------- |
| The fact was never ingested, or was ingested too late | Ingestion and sync (spikes 01 and 04) |
| The fact is indexed, and retrieval misses it because the query and the passage use different words, or the fact is buried in a long document | Indexing and query understanding (this spike) |
| The right passage is retrieved and misread, because it says "the new policy" without naming which policy or when it took effect | Context: metadata, version, and relationships (this spike) |
| The answer uses information the requester may not see | Authorization at retrieval time (spike 03). This is a security failure, not a quality failure. |

Questions also split in two. Semantic questions ("What have we said about data residency in enterprise contracts?") fit similarity search. Relational questions ("Which high-ARR Q3 deals depend on unreleased engineering epics?") need joins and filters. Similarity search answers the second kind badly, and a graph answers the first kind badly, because most organizational prose does not survive being reduced to triples.

## Decision (MVP)

Keep the source document as the unit of truth. Everything else is a derived index. Use two derived indexes, and route each question to the representation that is authoritative for it.

### Hybrid RAG, in Postgres

- Structure-aware chunks. Markdown splits on headings. PDF text is split into paragraphs, because the HR renderer drops heading marks. Records render as one chunk per row. Each chunk stores source id, section path, a short contextual header (document title plus heading path), version, timestamp, ACL attributes, `embedding_model`, and `chunker_version`. See [ADR 0003](../03--adrs/0003-embedding-and-chunk-versioning.md).
- Dense retrieval with pgvector, plus lexical retrieval with Postgres full-text search. Dense retrieval misses exact identifiers (deal ids, epic keys, policy names). Lexical retrieval catches them.
- Reciprocal rank fusion merges the two ranked lists in application code. Fusion does not require calibrating the two score scales against each other.
- Raw blobs live in S3. Postgres holds the chunk text, metadata, and both indexes. See [ADR 0001](../03--adrs/0001-storage.md).

### Graph, in Neo4j, small on purpose

Ontology for the demo question and the policy question only:

| Nodes | Edges |
| ----- | ----- |
| Person, Team, Deal, Epic, Policy | `BELONGS_TO`, `DEPENDS_ON`, `OWNED_BY` |

Population rules:

- Structured sources (Sales deals, Engineering epics, HR directory) emit edges in code. These edges are deterministic and are the ones the roadmap answer trusts. There is no account node. Account fields sit on the deal.
- Product specs stay prose. They are not graph nodes. A later extractor could emit `REFERENCES` edges, each with a confidence and a source span, as supporting evidence only. That extractor is not built, and those edges would not decide the headline answer.
- Entity identity is the shared source key or email. Fuzzy entity resolution is out of scope, and the demo data uses one product key across Sales, Product, and Engineering so the join is real.

The graph writer is built. HR records write `Person -BELONGS_TO-> Team` and a `Policy` node. A deal writes `DEPENDS_ON` to its epic and stores the account name. An epic writes `OWNED_BY` to the HR team named on the record. `OWNS`, `REFERENCES`, the spec extractor, and the query router are not built. The gateway reads four templates: teammates of a person, people on a team, policies the caller may see, and `roadmap_deals` (`Deal -DEPENDS_ON-> Epic -OWNED_BY-> Team`, quarter and minimum ARR, epic not released). That last template is the built answer for question 2. It is one fixed question. The router is the production path. `hash-v1` is lexical, so the path does not depend on shared words.

### Query router

The service layer classifies the request:

| Class | Authority | What runs |
| ----- | --------- | --------- |
| Semantic | Chunk text | Hybrid search, fused, then authorized |
| Relational | Graph | Parameterized Cypher over the typed ontology, then the linked chunks for wording |
| Hybrid | Graph for the path, chunks for the prose | Graph traversal to find the entities, hybrid search restricted to those entities' chunks |

Authority is code, not a prompt. Factual lookups on structured attributes trust the graph and the relational rows. Explanatory questions trust the document text, and the graph only locates it.

### Evaluation, before tuning

The golden set is built: 20 cases in `tools/eval/golden.yaml`, run by `make eval` on every change to chunking, the embedding model, prompts, or retrieval parameters. It adds a paraphrase category (queries that share no word with the policy) and three graph cases to the four below:

- Questions with a known supporting chunk (recall@k, MRR on the retrieved set).
- Questions whose answer is not in the corpus (the system must abstain).
- Questions with two conflicting sources (the answer must surface both versions).
- Access-boundary questions (a user without the claim must receive an empty authorized set). These are security tests.

Retrieval metrics and answer faithfulness are tracked separately. Faithfulness for the POC is a citation check: every claim in the answer maps to a returned chunk id. A model-graded judge is a production-path addition.

## Why

- The two demo questions need different representations. Hybrid search answers the policy question. The graph answers the dependency question and can show the path, which is the explanation.
- Putting both indexes in one design, and writing down which one is authoritative per question class, avoids a chatbot that improvises joins from retrieved prose.
- Reciprocal rank fusion is enough. A reranker is justified only after the golden set shows recall is fine and precision is not. Measured on 20 cases and about 500 chunks: `bge-small-en-v1.5` plus full-text, fused, scores recall at 5 of 1.00 and MRR of 0.95. There is no gap to close, so the reranker stays out. The candidate if that changes is a small cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2` through fastembed) over the top 20, measured against the 250 ms budget. The same run with `hash-v1` scores 0.50 recall and 0.20 on paraphrases, which is the evidence that the dense side does the work.
- Deterministic edges from SQL and JSON avoid the failure mode where plausible extracted triples compound across hops.

## Alternatives rejected

| Alternative | Why it was rejected for the POC |
| ----------- | ------------------------------- |
| Vector search only | Fails the roadmap question. "Depends on" is an edge, not a paraphrase. |
| Graph only, including LLM extraction over all prose | Policy nuance does not survive as triples, and extracted triples that are plausible and wrong get worse when traversed. |
| Stuff the corpus into the prompt | Reasonable for a handful of static policies that fit a context window. It does not meet authorization, citation, or the 10-million-object assumption, and it hides freshness. |
| Fine-tuning | Changes style and format. It does not keep knowledge current, and it cannot enforce per-user authorization. |
| Cross-encoder reranker in the POC | Extra latency against the 250 ms budget, and no measured precision gap yet. Added later if `eval` shows one. |
| Community summaries and global GraphRAG | Useful for corpus-level themes. The demo questions are entity lookups and one multi-hop path. Summaries would add a batch job without serving the script. |
| Fuzzy entity resolution | The hardest part of a real graph, and dishonest to fake with a demo that already shares keys. Called out as a production-path risk in the brief. |

## Trade-offs and risks

- Two indexes must stay consistent with the document. A bug in one produces an answer that looks authoritative and is assembled from mismatched pieces. The worker commits the chunk, then writes the graph. A failed graph write leaves the message unacknowledged so the event retries (spike 04). Until that retry, the chunk can exist without the node.
- Chunks that are too small lose the context needed to interpret them. Chunks that are too large dilute the embedding and waste the context budget. The contextual header (title plus section path) is the mitigation that stays inside the chunk, so a retrieved passage still names the policy and the version.
- Dense retrieval returns topically close and factually irrelevant passages. Lexical search and fusion reduce that. They do not remove it. The golden set is how regressions are caught. The corpus is small, so a clean score here is weak evidence for 10 million objects. The similarity floor (`VECTOR_MAX_DISTANCE`, 0.40) was set from observed distances (related 0.29 to 0.44, unrelated 0.57 or more) and must be re-measured on another corpus.
- Routing mistakes send a relational question to vector search. The router is a small classifier with an explicit fallback: when the question names a known entity type (deal, epic, policy), prefer the graph.

## Production path

- Add a reranker only when recall@k is healthy and precision is not.
- Add entity resolution when a real source pair does not share keys, with a human-review queue for low-confidence merges.
- Dual-write during embedding-model migrations, and retire the old index on a published date ([ADR 0003](../03--adrs/0003-embedding-and-chunk-versioning.md)).
- Query rewriting for conversational follow-ups, once a chat client exists. The POC questions are standalone.

## Open TODOs

- Built: the golden-set file format and the `make eval` report (recall@k, MRR, abstention, access-boundary pass/fail, p95 latency). Open: a model-graded faithfulness check, and a multilingual embedder if policies stop being English.
- A query router that chooses search or `roadmap_deals`. The template itself is built.
