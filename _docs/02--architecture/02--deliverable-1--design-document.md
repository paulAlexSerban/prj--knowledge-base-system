# Knowledge Base Gateway: Design Document

It answers the eight questions in the assignment, states the assumptions, and says plainly what the POC proves and what it does not. It is written to be read alone. Longer reasoning is in the [project brief](../00--project-brief/00-project-brief.md), the [spikes](../01--spikes/), and the [ADRs](../03--adrs/README.md). The POC and the AI-SDLC are Deliverable 2 (see the [README](../../README.md)).

Every statement below is tagged by where it lives. "Design" means the production proposal. "Built" means it runs in the POC. "Not built" means it is proposed only.

## 1. Summary

Four departments own sources that change independently and share no schema. Agents need knowledge that is accurate, current, and authorized at the moment they act.

The proposal is a knowledge gateway with four parts:

1. Connectors, one per source, detect changes and emit small events onto priority queues.
2. Workers normalize, chunk, embed, and write to two stores. Postgres is authoritative for prose, lineage, permissions, and hybrid search. Neo4j is a derived graph with a small typed ontology.
3. One service layer, behind a REST API and an MCP server, retrieves under the caller's identity. Authorization runs in the data path. Agents hold no store credentials and have no write tools.
4. Deletes are hard deletes, verified, with a reconciliation sweep as the backstop.

The POC covers four mocked sources and two questions:

1. "What is our parental leave policy in Germany?" Answered from authorized chunks with a citation.
2. "Which high-ARR Sales deals in Q3 depend on unreleased Engineering epics?" Answered from a graph path.

## 2. Assumptions

The discussion should start here. The first column is what I assume for the real company. The second is what the POC actually exercises.

| Topic          | Production assumption                                                                                                    | POC                                                                                      |
| -------------- | ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| Scale          | About 2,000 employees and about 10 million objects across Sales, Product, Engineering, and HR.                           | A few hundred documents and records, about 500 chunks.                                   |
| Change volume  | About 100,000 changes a day, about 1.2 a second on average, bursts of 10 to 20 times.                                    | Same pipeline at demo volume. Not load-tested.                                           |
| Freshness      | HR policy and security under 30 seconds. Jira and Confluence under 5 minutes. Archives daily.                            | Three queue tiers and a lag metric. Shown locally.                                       |
| Security       | Zero trust. Every query carries the caller's identity and attributes. Agents have no elevated privilege.                 | A mock identity provider signs HS256 JWTs with group, department, clearance, and region. |
| Queries        | Semantic search and multi-hop traversal.                                                                                 | The two demo questions plus a 20-case golden set.                                        |
| Latency        | Under 250 ms p95 for retrieval, excluding model generation.                                                              | A target with a metric. Measured once on a laptop, which is not a load test.             |
| Language       | English policies. Germany appears in the demo but the policy text is English.                                            | English only.                                                                            |
| Infrastructure | Not specified. I assume AWS because the assignment mentions SQS-style services and the local stack emulates AWS.         | Docker Compose, floci for SQS and S3, Terraform against floci.                           |
| Models         | Not specified.                                                                                                           | Local multilingual-e5-large embeddings, 1024 dimensions. OpenRouter for chat if a key is set. Optional router model for low-confidence questions. Offline hash embedder. |
| Source shapes  | Each department exposes at least one of: a pollable listing, a change feed, or a webhook. Native permissions are mappable. | Four cooperative mock sources built for this.                                            |
| Shared keys    | Cross-department joins rely on a key or email that exists in more than one system.                                        | The demo data has one product key across three sources, on purpose.                      |

Requirements I narrowed, and why:

- The 30-second target only holds for a source that can push or be polled that fast. Freshness is a per-source contract, not a platform promise.
- The 250 ms budget covers hybrid search plus an authorization filter. A reranker or open-ended graph expansion is a separate decision, made only if metrics show a gap.
- "Without manual intervention" means no human re-indexes the corpus. It does not remove the need for a named owner per connector, a dead-letter queue, and a reconciliation job.
- Compensation figures and national identifiers are not ingested (`salary`, `compensation`, `ssn`, `national_id`). Leaving them out is safer than protecting them with attribute rules on a retrieval index.
- The graph serves the multi-hop question. Policy prose stays in chunks.

## 3. Architecture

![End-to-end data flow](../99--dagrams/02-end-to-end-data-flow.png)

Source drawio: [02-end-to-end-data-flow.drawio](../99--dagrams/02-end-to-end-data-flow.drawio). Supporting views: [system context](../99--dagrams/00-c4-l1--system-context.png), [containers](../99--dagrams/00-c4-l2--container-diagram--knowledge-base-system.png), [connectors](../99--dagrams/01-c4-l2--container-connectors.png), [components](../99--dagrams/00-c4-l3--component-diagram--knowledge-base-system.png), the [ingestion sequence](../99--dagrams/03-sequence--ingestion-pipeline.png), and the [read-path sequence](../99--dagrams/04-sequence--knowledge-store-flow.png).

### Components

| Component         | Role                                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------------------ |
| Source contracts  | One YAML file per source: owner, freshness tier, sensitivity, and rules mapping native permissions to claims. |
| Connectors        | One process per source. Detect change, fetch, map ACL, normalize, write the raw payload to S3, emit an event. |
| Queues            | SQS FIFO, one per freshness tier, each with a dead-letter queue. Group id is source id plus object id.        |
| Object storage    | S3. Raw payloads and document blobs. Events carry a pointer, not the body.                                    |
| Ingestion workers | Chunk, embed, upsert Postgres, merge Neo4j. Or apply a delete.                                                |
| Postgres          | Prose, lineage, ACL attributes, sync state, content hashes, pgvector, full-text, row-level security.          |
| Neo4j             | Derived graph. Rebuildable from Postgres and the structured sources.                                          |
| Service layer     | Retrieval, graph templates, principal handling, authorization, audit. Shared by both adapters.                |
| REST API, MCP     | Thin adapters. Validate the token, call the service layer, return cited results.                              |
| Observability     | Alloy, Prometheus, Loki, Tempo, Grafana.                                                                      |

### Data flow, source to agent

| Hop | Where            | Transformation                                                                                                                              |
| --- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Source           | A record is created, edited, or deleted in its own schema.                                                                                  |
| 2   | Connector        | Detect the change. Fetch the body. Map native permissions to `group`, `clearance`, `department`. Normalize to a platform document with a content hash. Raw payload to S3. |
| 3   | Queue            | Event with source id, object id, type, version or hash, `occurred_at`, S3 pointer. Ordered per object.                                      |
| 4   | Ingestion worker | Chunk with a format-aware splitter. Add a contextual header (title plus section path). Embed only if the content hash changed.             |
| 5   | Ingestion worker | Upsert chunks, vectors, full-text, and ACL columns in Postgres. Then merge graph nodes and edges. A failed graph write leaves the message unacknowledged. |
| 6   | Service layer    | Pre-filter in SQL or Cypher, over-fetch, fine-grained check, row-level security, audit. HR policy search also re-checks the source ACL.     |
| 7   | Agent            | Receives cited snippets or a graph path. Retrieved text is untrusted data.                                                                  |

Two rules shape the whole design. Postgres is authoritative and Neo4j is derived, so the worker commits Postgres first and retries Neo4j. Conventional code owns permissions, sync, deletion, and state transitions. A model is used only for language understanding and constrained extraction.

## 4. Data connection

How does the system connect to heterogeneous sources?

Source quirks (auth, pagination, formats, rate limits) stay inside a connector that implements one interface: `discover`, `list_changes`, `fetch`, `get_acl`, `normalize`. Nothing downstream knows what a source looks like.

A source cannot join the platform without a valid contract. `load_contracts` is the registration gate and a connector refuses to start if it raises. The contract requires an owner, a freshness tier, and at least one `acl_mapping` rule. A high-sensitivity source must also declare an ACL re-check at read time.

Each connector uses the cheapest change signal its source really offers. The four demo sources cover four of them:

| Source      | Shape                                          | Change signal            | Sensitivity                        |
| ----------- | ---------------------------------------------- | ------------------------ | ---------------------------------- |
| HR          | Markdown and PDF policies, SQLite directory    | Snapshot poll            | High. ACL re-checked at read time. |
| Sales       | SQLite CRM                                     | Cursor change feed       | Medium                             |
| Product     | Markdown specs                                 | Webhook                  | Low                                |
| Engineering | Jira-like epics in SQLite                      | `updated_since` listing  | Medium                             |

Status: built for all four. The HR connector does snapshot diffing against S3. Product enqueues a pointer from a webhook receiver.

Rejected:

- Airbyte or Fivetran. They load a warehouse. The hard parts here are permission normalization, tombstones, and freshness contracts, and they own none of those.
- Federated query at read time. It puts source latency and partial failure inside the user's request and gives no lineage.
- CDC. The platform does not own the source databases and cannot ask for log access.

Honest limit: the mock sources are cooperative. Real Jira, Drive, or HubSpot connectors will spend most of their effort on pagination, token refresh, rate limits, and permission models that do not map cleanly. The POC does not prove that.

## 5. Knowledge representation

How is knowledge from different departments modeled and related?

Similarity search answers semantic questions and fails relational ones. A graph answers relational questions and fails on prose. So the source document stays the unit of truth and each question goes to the store that is authoritative for it.

Prose (Postgres):

- Structure-aware chunks: markdown by heading, PDF by paragraph, records as one chunk per row.
- Each chunk keeps a contextual header, the chunker version, the embedding model, the content hash, the source object and version, the sync time, and the ACL attributes.
- Retrieval is dense (pgvector, 1024-dimension multilingual-e5-large) plus English full-text for exact identifiers, merged with reciprocal rank fusion. A vector-only hit is kept only inside a distance floor, so a question with no answer returns nothing. The dense side is multilingual. The lexical side is not.

Relations (Neo4j):

| Nodes                            | Edges                                  |
| -------------------------------- | -------------------------------------- |
| Person, Team, Policy, Deal, Epic | `BELONGS_TO`, `DEPENDS_ON`, `OWNED_BY` |

- Edges from structured records are extracted in code and are deterministic. The headline roadmap answer trusts only those: deal depends on epic, epic is owned by a team.
- Entities join on a shared source key or email. There is no fuzzy entity resolution.
- Edges extracted by a model from prose would carry a confidence and a source span and would be supporting evidence only. That extractor is design only. It is not built.

Rejected:

- Vector-only. It fails exact identifiers and the dependency question.
- A graph for everything. It adds a second copy of prose to keep in sync for no retrieval gain.
- Community-summary GraphRAG. No measured gap justifies the cost.
- Fine-tuning. It neither keeps knowledge current nor enforces access.

Honest limits:

- The router is built. It chooses search, one of four graph templates, or both for `roadmap_deals`. Question 2 is still that one template. A relational question with no template stays unanswered as a path.
- The join works because the demo data was written with a shared key. Whether real Sales, Product, and Engineering systems share one is an open question for the stakeholders.

## 6. Agent access

How do agents query the system safely?

MCP gives every client the same tools. It does not authenticate users or enforce access, so the server and the REST API both do it on every call, through one shared service layer.

| MCP tool           | REST                     | Behavior                                                                    |
| ------------------ | ------------------------ | --------------------------------------------------------------------------- |
| `ask_knowledge`    | `POST /v1/ask`           | Rules route the question, then search or a template. Optional model tie-break. |
| `search_knowledge` | `POST /v1/search`        | Hybrid retrieval for the caller. One chunk per object, with provenance.     |
| `get_document`     | `GET /v1/documents/{id}` | One authorized chunk with provenance.                                       |
| `find_related`     | `POST /v1/graph/related` | Bounded graph templates. Returns the path.                                  |
| `list_sources`     | `GET /v1/sources`        | Owner, tier, last sync. No bodies.                                          |

There is no write tool and no open SQL or Cypher. The token's claims become the principal. Authorization is layered:

1. A coarse pre-filter in SQL and Cypher on group, clearance, and department.
2. Over-fetch, then a fine-grained post-check where one stored rule must match group, department, and clearance together.
3. Postgres row-level security reads the principal from session settings. A query that forgets the predicate still cannot read another principal's rows.
4. An audit row names the attributes that justified each allow. Denies are logged.

Retrieved text is data. Snippets are marked as untrusted, no tool can write, and the gateway does not generate an answer. The calling agent does.

Staleness is explicit. HR policy search calls the source ACL endpoint before returning a chunk and fails closed on timeout. Other sources trust the indexed ACL inside the freshness window stated in their contract.

Status: built for REST and MCP, including `/v1/ask`, with two principals tested against one pool. The router is rules first. A model is used only for a low-confidence question when a key and `OPENROUTER_ROUTER_MODEL` are set, and a bad label is discarded. Not built: OIDC (the POC uses HS256 and a dev token endpoint), a principal holding several departments (a `roadmap_readers` rule with `department: "*"` stands in), and per-service Neo4j roles. Neo4j Community has no roles, so the gateway and worker share one user. That is an accepted POC risk.

Rejected:

- MCP as the authorization boundary. The protocol does not carry user identity.
- Per-agent database credentials. They multiply the permission surface and bypass the audit path.
- Filtering after the top-k only. It leaks through counts and empties the result set when the top hits are unauthorized.

## 7. Self-maintenance

How do additions, changes, and deletions propagate?

Ingestion is a stream of create, update, delete, and ACL-change events. A periodic job exists only for reconciliation.

| Change     | Path                                                                                                                                              |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Create     | Detected, normalized, chunked, embedded, upserted. Visible to a principal whose claims match the ACL.                                              |
| Update     | Content hash changed: re-chunk and re-embed. Hash unchanged: update sync metadata, skip the embedding call, still merge the graph.                 |
| ACL change | Attributes updated in place, no re-embed, sent on the highest-priority queue.                                                                      |
| Delete     | Connector deletes the S3 object and verifies it is gone, then emits `delete`. Worker tombstones, hard-deletes chunks and graph nodes, verifies both. |

Mechanics:

- The queue is at-least-once. The upsert key is source id, object id, chunk index, chunker version, and embedding model, so a replay changes nothing.
- Three queues match three tiers. One worker drains the highest priority first.
- After 5 receives a message moves to the dead-letter queue. `make dlq-replay` sends it back. A poison message blocks only its own object, because groups are per object.
- Reconciliation deletes chunks whose source object row is missing and backfills objects with no chunk at the current version. It is the backstop for a missed delete.
- A failed Neo4j write leaves the message unacknowledged and it retries, and a retry with a matching hash still writes the graph.

Why hard deletes: soft-deleted rows resurface through one missed filter, and graph edges stay traversable. Retention is a source decision, not an index decision.

Status: built, including the delete verification and the DLQ replay. Not built: automatic intake pause on a lag breach. The alert is a dashboard threshold. Scheduling is an in-process timer, not EventBridge Scheduler, because Scheduler cannot fire faster than once a minute.

## 8. Technology choices

| Component                  | Choice                                                         | Why                                                                                                                | Excluded, and why                                                                                                                                        |
| -------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Prose, vectors, lexical, ACL | Postgres with pgvector, full-text, row-level security        | The ACL pre-filter is an ordinary SQL predicate next to the vectors. One authorization implementation.             | OpenSearch plus a vector DB plus Neo4j: three query languages and three permission models for a volume that does not need them. A dedicated vector DB still needs Postgres for ACL and lineage. |
| Graph                      | Neo4j                                                          | The roadmap question is a short typed traversal and the path is the explanation.                                   | Recursive SQL over edge tables is viable at this ontology size. It is the fallback if Neo4j becomes the weak link. Neptune only if openCypher is enough.  |
| Messaging                  | SQS FIFO, one queue per tier, DLQ per queue                    | Per-object ordering, at-least-once delivery, visible poison messages.                                              | Kafka or Kinesis are justified by a second consumer needing replay, not by about 1.2 events a second.                                                    |
| Objects                    | S3                                                             | Raw payloads and blobs. Postgres holds the pointer. Keeps events small.                                            | Shared volume or Postgres large objects. Not compared in a spike.                                                                                        |
| Connectors                 | Custom, one interface                                          | The hard problems are ACL normalization, tombstones, and freshness.                                                | Airbyte, Fivetran, federation, CDC (see section 4).                                                                                                      |
| Service                    | Python, FastAPI, Pydantic v2                                   | Typed contracts, one shared service layer for two adapters, same language as the workers.                          | Flask, Django, Litestar. Not compared in a spike.                                                                                                        |
| Agent protocol             | MCP plus REST                                                  | MCP for agents, REST for the chat page, scripts, and eval. Same service layer.                                     | MCP only: no non-agent clients. REST only: every agent framework writes its own tool wrapper.                                                            |
| Embeddings                 | `intfloat/multilingual-e5-large` via fastembed, 1024 dimensions, CPU | No key at run time. Multilingual dense search. About 2.2 GB in each of three images. Tests use a hash embedder. | bge-small (the previous English-only model), e5-small (weaker), bge-base and bge-large (English only), mxbai-embed-large (English only), hosted embeddings (a network dependency inside ingestion). |
| Chat answer                | OpenRouter through LangChain when a key is set                 | Model names are config. The gateway itself never calls a model.                                                    | LangGraph for a single prompt.                                                                                                                           |
| Identity                   | Mock IdP issuing JWTs                                          | The claim contract is what the service enforces. Only `gateway_core.auth` knows the signer.                        | MCP as the boundary. Per-agent store access.                                                                                                             |
| Local AWS and IaC          | floci and Terraform with endpoint overrides, one-shot container | Real Terraform resources against an emulator. Same files move to AWS with a provider block and a state backend.    | LocalStack (heavier). CloudWatch on floci: metrics and logs stay on Prometheus and Loki so they behave the same in Compose.                              |
| Retrieval extras           | Reciprocal rank fusion only                                    | No score calibration needed. The measured recall leaves no gap to close.                                           | Cross-encoder reranker, community-summary GraphRAG: no measured gap.                                                                                     |
| Observability              | Alloy, Prometheus, Loki, Tempo, Grafana, dashboards as code    | Freshness and authorization are distributions, a deny is one log line, one change is one trace across the queue.   | Logs only. Scraping without a collector. Jaeger beside Tempo.                                                                                            |
| Packaging                  | uv workspace, one image per deployable, Compose per trust area | Reproducible installs, separate blast radius for the gateway, workers, and connectors.                             | pip and venv. One image for everything.                                                                                                                  |

### Production path on AWS (not built)

| Local                                | Production                                                                              |
| ------------------------------------ | --------------------------------------------------------------------------------------- |
| floci SQS and S3                     | SQS and S3. Terraform stays, endpoint overrides go, add remote state and KMS.           |
| Postgres with pgvector               | Aurora PostgreSQL or RDS with `vector`. Pooling must set the principal per transaction. |
| Neo4j Community                      | Neo4j Aura or self-managed. Per-service roles replace the shared user.                  |
| Compose services                     | ECS on Fargate, one service per image, an ALB in front. Workers scale on queue depth.   |
| Webhook receiver container           | API Gateway plus Lambda that enqueues the pointer.                                      |
| In-process polls                     | EventBridge Scheduler for the slow tiers. The fastest tier stays in the connector.      |
| HS256 and dev token                  | Company OIDC. The gateway still builds the principal.                                   |
| Grafana stack                        | Same Alloy with OTLP to the company backend.                                            |

## 9. Trade-offs

| Trade-off                       | Accepted cost                                                                    | Where it shows                                          |
| ------------------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Freshness versus embedding cost | Real time only for the top tier. Unchanged content skips embedding.              | Skipped-embed counter, tier-0 lag.                      |
| Two stores versus one           | Postgres and Neo4j can diverge until a retry succeeds.                           | Unacknowledged messages, drift panel.                   |
| SQS versus a log                | No cross-object order and no independent replay.                                 | Dead-letter depth.                                      |
| Indexed ACL versus read-time ACL | Only HR pays a live call. Other sources are stale inside their window.          | Retrieval latency split, window on the contract.        |
| Deterministic versus extracted edges | Recall on spec-to-epic links is lower, but the headline answer is trustworthy. | Path versus supporting edges.                           |
| 250 ms versus the HR re-check   | The live call can eat the budget. It fails closed rather than skip.              | Retrieval and ACL re-check histograms.                  |
| Hard delete versus audit trail  | No undelete from the index. Recovery means re-ingesting from the source.         | Delete verification metric.                             |
| One Postgres for everything     | Vector scale ceiling. At 10 million objects, about 20 to 40 million chunks, the index needs tuning or partitioning. | Not measured. See risks.                                |

### Requirements I would question

- "Authorized at the moment they act" is a staleness budget, not a guarantee. Only HR re-checks live. Someone has to accept that for Sales and Engineering.
- "100,000 changes a day" is an average. The number that sizes the system is the burst after a bulk import or a reorganization, and the embedding bill for it.
- "Without manual intervention" cannot cover a source that changes its API. Each connector needs a named owner who is paged by the lag alert.
- The two example agents want different things. The HR chatbot needs abstention and citations. The roadmap agent needs correct cross-department joins, which depend on shared keys nobody has promised exist.

## 10. Quality evaluation

### Before release and on every change

A golden set of 23 cases (`tools/eval/golden.yaml`) runs with `make eval` against `POST /v1/ask` as each persona. It exits non-zero below the thresholds in the file. It runs on every change to chunking, the embedding model, prompts, or retrieval parameters.

| Category                | Pass condition                                                                  |
| ----------------------- | ------------------------------------------------------------------------------- |
| Known supporting chunk  | In the retrieved set. Measured as recall at 5 and MRR.                          |
| Paraphrase              | Same, for queries that share no word with the policy.                           |
| No answer in the corpus | The system abstains.                                                            |
| Conflicting sources     | Both versions are surfaced.                                                     |
| Access boundary         | Two principals on one pool. The unauthorized one gets an empty result.          |
| Graph                   | The expected path is returned.                                                  |

Measured on the seeded stack:

| Run                             | recall at 5 | paraphrase recall at 5 | MRR  | no answer | access | graph | p95    |
| ------------------------------- | ----------- | ---------------------- | ---- | --------- | ------ | ----- | ------ |
| `hash-v1` plus full-text, fused (20 cases, previous) | 0.50        | 0.20                   | 0.50 | 1.00      | 1.00   | 1.00  | 167 ms |
| bge-small plus full-text, fused (20 cases, previous) | 1.00        | 1.00                   | 0.95 | 1.00      | 1.00   | 1.00  | 216 ms |
| e5-large plus full-text, fused, routed (23 cases)    | 1.00        | 1.00                   | 0.90 | 1.00      | 1.00   | 1.00  | 372 ms |

Read these numbers carefully. The corpus is about 500 generated chunks. The cases were written by the person who wrote the corpus. Latency is a laptop running everything through HTTP including a live HR ACL call. The e5-large p95 is 372 ms, which misses the 250 ms design target. A query embed on this CPU is about 150 to 300 ms. The regression gate stays 500 ms, and the 250 ms target is not raised. Recall at 5 is still 1.00, so no reranker was added. MRR is 0.90 because a neighbour sometimes ranks above the expected policy. This is a regression gate. It is not evidence that retrieval works on 10 million real objects.

Faithfulness is a citation check: every claim must map to a returned chunk id. A model-graded judge is a production addition and is not built.

### In operation

Each service exports metrics, structured logs with `request_id` and `trace_id`, and traces, so one object change or one search is one trace across the queue.

| Signal                              | Why it matters                                                 |
| ----------------------------------- | -------------------------------------------------------------- |
| Per-source snapshot and event lag   | Freshness against the contract.                                |
| Queue and dead-letter depth         | Stuck or poisoned objects.                                     |
| Embed calls versus skipped by hash  | Cost and whether change detection works.                       |
| Index drift (indexed versus source ids) | Missed deletes and missed creates.                         |
| Retrieval latency and ACL re-check latency | The 250 ms budget.                                      |
| Empty-result rate per permission group | The earliest visible sign of an authorization defect.       |
| Auth failures, deny log lines       | Misuse and mapping errors.                                     |

POC alerts are dashboard thresholds: tier-0 lag over 30 seconds, any dead-letter message, any delete verification failure. There is no Alertmanager.

### When problems arise

| Symptom                                      | Action                                                                                          |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Dead-letter messages                         | Fix the cause, then replay. The upsert key makes replay safe.                                   |
| Lag breach or stalled connector              | Page the contract owner. Proposed: pause that connector's intake. Not built.                    |
| Indexed ids exceed source ids                | Run reconciliation.                                                                             |
| Recall drops after a chunker or model change | Roll back `chunker_version` or `embedding_model`, then `make reindex`.                          |
| Empty-result rate rises for one group        | Treat as an authorization defect first. Check ACL mapping, the pre-filter, and RLS settings.    |
| HR ACL re-check errors                       | Fail closed. The search returns nothing and the failure counter increments.                     |
| A user reports a wrong answer                | Pull the trace, check which chunk version and sync time was cited, and add the case to the golden set. |

## 11. Risks

- floci fidelity is unproven. The API shape works. Ordering under load and behavior on real SQS are untested.
- The two stores can diverge until a retry completes. Postgres is authoritative.
- Permissions are stale inside the sync window for medium and low sensitivity sources.
- Row-level security keyed on session settings is easy to get wrong under connection pooling. The table owner bypasses it.
- Neo4j Community has no roles, so there is no per-principal boundary in the graph store beyond the query templates.
- Model-extracted edges will sometimes be wrong. They never decide the headline answer.
- Entity resolution beyond shared keys is deferred.
- The 250 ms and 30-second targets are design targets, not load-test results.
- Embedding model choice was tuned on a small English corpus. A multilingual company will need a re-measure.
- Data residency and multi-region operation are not designed.
- The vector index at the production assumption has not been sized.

## 12. What is built

Built:

- Design documents, ADRs, and diagrams.
- Source-contract gate with tests and CI, and four YAML contracts.
- Compose per trust area, Terraform on floci (three FIFO queues, three dead-letter queues, one bucket).
- Postgres and Neo4j schemas, including row-level security and an audit table.
- Four demo harnesses and four connectors.
- Ingestion worker with chunking, embedding, hash skip, tombstones, verified deletes, and graph writes.
- REST API and MCP server with `ask` (the router), search, `get_document`, `find_related` (four templates including `roadmap_deals`), and `list_sources`.
- HR ACL re-check, audit rows, and dead-letter replay.
- Observability stack with dashboards and alert thresholds.
- Golden set and `make eval`.
- Chat page and two read-only notebook agents over MCP.

Not built: OIDC, a deployed agent runtime, a model-graded faithfulness check, automatic intake pause, Alertmanager, the Lambda for the webhook path, and the model-based edge extractor. The router selects templates. It does not plan open traversals.
