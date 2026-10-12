# Project Brief

## Context

A global company has its operational knowledge spread across many systems, owned by different departments and stored in different shapes. Each department (Sales, Product, Engineering, Finance, People, and others) owns its own sources. Those sources are updated continuously and independently: records are created, edited, or deleted every day, with no central coordination and no common schema.

The company wants to build AI agents that automate process workflows across departments. Two examples:

- A chatbot that lets employees query HR information (policies, procedures, benefits) in natural language.
- An agent that connects information from Sales, Engineering, and Product to produce data-driven roadmaps.

None of these agents exist yet. To build them reliably, agents need knowledge that is accurate, current, and authorized at the moment they act. Today there is no system that provides this.

![System Context](00-c4-l1--system-context.png)

The POC implements one slice of that system: a knowledge gateway that ingests four department sources, keeps a hybrid index (vector plus knowledge graph) current from change events, and exposes the result to agents through one API and one MCP server. Production scale, additional departments, and a full agent product are documented as a production path and are not built.

## Explicit requirements

- Handle data from distributed sources that belong to different departments, with different formats, access methods, and ownership.
- Transform that data into knowledge that AI agents can interpret and use.
- Expose the knowledge to AI agents through a consistent access layer.
- Maintain itself over time, reflecting additions, modifications, and deletions in the source systems without manual re-indexing.

## Implicit requirements

- Make the knowledge available to multiple future agents so that every team does not implement its own connectors, document processing, permissions, retrieval, and synchronization.
- Reduce the effort required to discover and use organizational knowledge, and make it safer and cheaper to introduce new AI-enabled workflows.

## Product principles

1. Authorization before relevance. A highly relevant document is never a valid result if the requester is not allowed to see it.
2. Source systems remain authoritative. The platform indexes and organizes knowledge. It does not replace source ownership or become the system of record.
3. Freshness is measurable. Every indexed item carries synchronization and source-version metadata, and freshness objectives can be monitored.
4. One access contract, multiple integrations. Agents consume a stable interface and do not connect directly to every department's systems.
5. Deterministic controls, probabilistic intelligence. Conventional code owns permissions, synchronization, deletion, validation, and state transitions. Language models are used where language understanding or semantic extraction adds value.

## Primary users and jobs to be done

| User / consumer               | Need                                                           | Expected outcome                                   |
| ----------------------------- | -------------------------------------------------------------- | -------------------------------------------------- |
| Employees                     | Ask questions about company policies and procedures            | Accurate answers with traceable sources            |
| Department owners             | Publish and maintain operational knowledge in existing systems | No duplicate manual knowledge management           |
| AI agents                     | Retrieve authorized facts from multiple departments            | Consistent, machine-readable context               |
| Platform engineers            | Add and maintain integrations                                  | Reusable connectors and observable synchronization |
| Security and governance teams | Enforce access restrictions and trace usage                    | Auditable, policy-compliant access                 |

## Assumptions

Two sets of assumptions are stated separately. The interview discussion starts from the production set. The POC is sized to the second set and does not claim to have proven the first.

### Stated production assumptions

| Topic | Assumption |
| ----- | ---------- |
| Scale | About 2,000 active employees and about 10 million objects (documents, records, emails, spreadsheets) across Sales, Product, Engineering, and People/HR. |
| Change volume | About 100,000 creations, modifications, or deletions per day. |
| Freshness | High-priority security and HR policy: propagation under 30 seconds. Operational content (Jira, Confluence): under 5 minutes. Bulk historical archives: batch sync every 24 hours. |
| Security | Zero-trust, user-delegated access. Downstream agents hold no elevated privilege. Every query inherits the initiating user's identity, entitlement scopes, and access-control attributes. HR data that contains compensation or national identifiers is subject to attribute-based access control. |
| Query types | Unstructured semantic search ("What is our parental leave policy in Germany?") and structured multi-hop traversal ("Which high-ARR Sales deals in Q3 depend on unreleased Engineering epics?"). |
| Heterogeneity | Unstructured: HR policies (PDF/DOCX), product specs (Notion/Confluence), sales transcripts. Semi-structured: financial JSON APIs, engineering work items. Structured: sales CRM records, people directory. |
| Latency | Unified retrieval under 250 ms p95, excluding language-model generation time. |

Sanity check on volume. 100,000 events per day is about 1.2 events per second on average. Peak bursts of 10 to 20 times the average are still well inside a single queue and a small worker pool. The production constraint is embedding cost, rate limits on source APIs, and delete correctness, not raw throughput. A design that starts with Kafka, a dedicated vector cluster, and multi-region replication is solving a problem this volume does not have.

### POC scaling assumptions

| Topic | POC assumption |
| ----- | -------------- |
| Sources | Four mocked department services: HR, Sales, Product, Engineering. |
| Corpus | A few hundred documents and records, enough to show citations, permission boundaries, and a multi-hop graph question. |
| Freshness | The same three tiers exist as queue priority and a measured sync-lag metric. The 30-second and 5-minute targets are demonstrated on the local stack. They are not load-tested. |
| Identity | A dev endpoint signs HS256 JWTs. Claims are subject, groups, clearance, and department. Region is not a claim. This stands in for OIDC. |
| Language models | OpenRouter via `OPENROUTER_API_KEY` for chat, when a key is set. The running embedder is local `hash-v1` (deterministic, not semantic). A semantic local model is optional and not locked. The constrained extractor is not built. |
| Infrastructure | Docker Compose plus [floci](https://floci.io) for AWS emulation (SQS, S3, EventBridge Scheduler). Terraform talks to floci endpoint overrides. |

## Questioned requirements

These are places where the stated requirements need a narrower reading before implementation.

- The 30-second freshness SLA holds only for sources that can push (webhook or change feed). A source that only supports polling cannot meet 30 seconds without polling faster than its rate limits allow. Freshness is a per-source contract, not a platform-wide promise.
- The 250 ms p95 retrieval budget covers hybrid search (vector plus full-text) with an authorization filter. Adding a cross-encoder reranker or an unbounded graph expansion inside that budget is a separate decision, made only after retrieval metrics show a gap.
- "Without manual intervention" means no human re-indexes the corpus. It still requires an owner per connector, a dead-letter queue, and a reconciliation job. Silent failure is the failure mode this system has to make visible.
- Compensation figures and national identifiers are a poor fit for a retrieval index even with attribute-based access control. The safer decision is data minimization: do not ingest those fields. The POC directory contains role, team, and region, and omits compensation and identifiers.
- A knowledge graph pays for itself on the multi-hop question. It does not pay for itself on policy prose. The graph ontology stays small and typed. Open-ended prose stays in chunks.

## POC scope

### Use cases that the demo must answer

1. HR policy chatbot. "What is our parental leave policy in Germany?" returns an answer with a citation to the policy chunk, its source version, and its sync time.
2. Cross-department roadmap question. "Which high-ARR Sales deals in Q3 depend on unreleased Engineering epics?" returns deals, epics, and the path that connects them (deal, epic, owning team). High ARR is 200,000 or more. Account fields sit on the deal. Specs stay prose and are not on this path.

### In scope (built)

- Four connectors behind the shared interface, each with a written data contract. Sales is deals only: account fields sit on the deal, and there is no account object.
- Change detection: HR and Engineering poll an updated-since listing. Sales reads a cursor feed that includes deletes. The Product harness posts a pointer to the webhook receiver, which enqueues it on `kbs-product-hooks`. The product connector snapshots the spec and emits the change event. A lost hook is recovered from `/ids`.
- An event pipeline: the four connectors, SQS with a dead-letter queue per tier plus the hook queue, and one ingestion worker.
- Hybrid retrieval: Postgres with pgvector and full-text search, merged with reciprocal rank fusion. `hash-v1` only keeps a chunk that also matched the words. Search covers HR. Deals, epics, and specs are stored and are not routed by the gateway yet.
- A Neo4j knowledge graph with a small ontology. HR writes people, teams, and policies. A deal depends on an epic, and an epic is owned by an HR team. The constrained extractor for product specs is not built. Specs stay prose.
- One service layer, `gateway-core`, exposed twice: a REST API and an MCP server (`search_knowledge`, `get_document`, `find_related`, `list_sources`). Search returns one chunk per object. Neither adapter writes an answer.
- User-delegated authorization: an HS256 JWT, a coarse pre-filter, a fine-grained post-check, Postgres row-level security, and an audit record of which attributes justified a result. Graph checks are the Cypher template and a read-only session. Neo4j Community has no roles.
- Delete propagation, content-hash skip, idempotent upserts, and a reconciliation sweep.
- A demo harness: FastAPI services with SQLite for HR, Sales, Product, and Engineering. HR stores rows with SQLAlchemy and can bulk-load through `POST /admin/seed`. The other three sources are a curated set and have no bulk seed. Each connector writes a raw snapshot to S3 and enqueues a change event.
- Docker Compose, floci, and Terraform for the FIFO queues, dead-letter queues, and snapshot bucket. Alloy collects metrics, container logs, and OTLP traces. Prometheus stores metrics, Loki stores logs, Tempo stores traces, and Grafana shows all four boards plus the trace list, including retrieval latency and the deny line. The API key stays in the environment.

Not built: the spec extractor, the query router, answer synthesis, OIDC, and a golden set of about 20 questions. Question 2 is built as one graph template, `roadmap_deals`. The router is the production path. A `roadmap_readers` rule with `department: "*"` lets one persona read a sales deal and an engineering epic. A principal still has one department, so that star rule is a deliberate widening. The real fix is a principal that can hold more than one department. Leadership deals and leadership epics do not carry the extra grant. `hash-v1` is lexical, so question 2 uses the graph and does not depend on shared words. API tests cover one known chunk, a query with no shared terms, two countries, an access boundary, and the roadmap path.

### Out of scope (production path, documented only)

Finance and other departments beyond the four. Real OAuth against Google, Notion, Jira, HubSpot, or Workday. Gong or Zoom transcripts. Multi-region deployment. A cross-encoder reranker. Community-summary GraphRAG. Kafka or Kinesis. A product UI beyond a small chat page or CLI. Compensation and national-identifier fields.

## Target stack

| Concern | POC choice |
| ------- | ---------- |
| Language and HTTP | Python, FastAPI. The Knowledge API and the MCP server are thin adapters over one service layer. |
| Metadata, vectors, lexical search, authorization | Postgres: source registry, ACL attributes, sync state, content hashes, pgvector, full-text search, row-level security. |
| Knowledge graph | Neo4j. |
| Messaging | SQS on floci, FIFO per source key so updates for one object stay ordered, plus a dead-letter queue. |
| Object storage | S3 on floci for raw snapshots and document blobs. |
| Scheduling | P0 poll loop inside the connector worker. EventBridge Scheduler, one-minute minimum, for P1/P2 polls and reconciliation. |
| Workers | Connector workers (extract, detect change, emit). Ingestion workers (normalize, chunk, embed, upsert vectors and graph, tombstone deletes). |
| Models | OpenRouter for chat when a key is set. The running embedder is local `hash-v1`. `fastembed` is optional and not locked. The constrained extractor is not built. |
| Identity | Dev-only HS256 JWTs. Claims are subject, groups, clearance, and department. |
| Infra | Docker Compose. Terraform with floci endpoint overrides for queues and the snapshot bucket. |
| Observability | Prometheus, Alloy, Loki, Tempo, and Grafana. One change or one search is one trace. |


![Container Diagram](00-c4-l2--container-diagram--knowledge-base-system.png)

![Component Diagram](00-c4-l3--component-diagram--kowledge-base-system.png)

## Demo script

The demo is the acceptance test. Each step is observable in the API response, the graph path, or the dashboard.

1. Demonstrable. Ask the Germany parental-leave question as `employee_eng`. The gateway returns the policy chunk, its source version, and its sync time. It does not write the answer. Notebook `41`.
2. Demonstrable. The same question as `outsider` returns nothing. The audit row and a JSON log line record the deny (`decision` of `deny`, `request_id` in the line, not in a Loki label). Open the trace on KBS / Traces, then jump to that deny line by `trace_id`. Notebook `41`.
3. Demonstrable through the gateway. `find_related` kind `roadmap_deals`, as `product_lead`, returns a Q3 deal whose ARR is 200,000 or more and whose epic is not released, with the path to that epic and the owning team. Leadership-only deals stay hidden. `employee_eng` and `outsider` get no paths. Notebook `44`. The chat page sends that suggested question to the graph tool and sends a typed question to search.
4. Demonstrable for an HR policy. Notebook `43` creates a policy, closes its ACL, and deletes it. Search and `find_related` drop it. Releasing the epic, or deleting the deal, drops that path from `roadmap_deals`. Notebook `14` deletes a deal. `hash-v1` is lexical, so question 2 does not use search.
5. Demonstrable at the worker. Stop an ingestion worker mid-batch, then start it again. In-flight messages return to the queue or land in the dead-letter queue. Replaying them does not duplicate chunks or edges. `make dlq-replay` sends dead-letter messages back, with a new deduplication id so FIFO does not drop them.

`make up` and `make test` exist. `make up` starts the stack, including the four connectors, the ingestion worker, and both gateways. `seed`, `demo`, and `eval` are still to be written. HR bulk load is notebook `00`. Notebook `02` resets the other three sources. Notebooks `40`–`44` call the gateway.

## Risks and open questions

- floci's documentation lists SQS, S3, Scheduler, IAM, and Bedrock Runtime, among others. A local apply created the FIFO queues and one send/receive worked. That is not a fidelity or load test. Model calls go to OpenRouter or the local fallback because the design should not depend on one cloud model API.
- Two stores (Postgres and Neo4j) can diverge. The document in Postgres is the authority for prose. The graph is a derived index. The worker commits Postgres, then writes Neo4j. A failed graph write leaves the message unacknowledged, so the event retries. Until that retry the chunk can exist without the node. A retry whose content hash already matches still writes the graph.
- LLM extraction of edges from product specs will be wrong some of the time. Every extracted edge carries confidence and a source span. The demo question is answered from deterministic edges extracted from structured records. Extracted edges are visible and are not trusted for the headline answer.
- Permission propagation has a window. HR policy search re-checks the harness ACL and fails closed when that call does not answer. A failed check drops the whole page, not only the policy that timed out. People records are not re-checked. Sales, Product, and Engineering contracts say the indexed ACL is authoritative inside the freshness window. That re-check is not wired for them. Search can return their chunks when the rule matches. Question 2 uses the graph.
- Entity resolution beyond shared ids and email addresses is deferred. If Sales and Engineering use different identifiers for the same product, the demo data uses one shared product key on purpose. Production will need a resolution step; pretending the demo key is that step would be dishonest.
- The 250 ms budget and the 30-second SLA are design targets with a metric, not a load-test result.

## Repo layout

| Path | Role |
| ---- | ---- |
| `_docs/00--project-brief/` | This brief, the C4 system-context diagram, and the [C4 container diagram](01-c4-l2--container-connectors.png) for connector workers. |
| `_docs/01--spikes/` | Decision records for ingestion, knowledge representation, access, and synchronization. |
| `_docs/02--architecture/` | Architecture document. Deliverable 1, written from this brief, the spikes, and the ADRs. |
| `_docs/03--adrs/` | Short ADRs for storage, observability, and embedding/chunk versioning. |
| `backend/apis/knowledge-api` | REST gateway. Its own image. |
| `backend/apis/mcp-server` | MCP gateway. Its own image. |
| `backend/apis/webhook-receiver` | Product webhook receiver. Its own image. It enqueues a pointer. The production path is API Gateway plus Lambda, and that function is not built. |
| `backend/connectors/` | One connector process per source. Each has its own image. |
| `backend/workers/ingestion-worker` | Queue consumer. Its own image. Writes chunks and the graph. |
| `backend/libs/connector-core` | Connector interface and snapshot sync. A library, not a service. |
| `backend/libs/ingest-contract` | Change events, queue names, and the snapshot hash. A library, not a service. |
| `backend/libs/knowledge-store` | Chunking, embeddings, Postgres chunks, and the Neo4j writer. A library, not a service. |
| `backend/libs/gateway-core` | Retrieval, JWT checks, Neo4j read templates, and the tool text. A library, not a service. |
| `database` | Postgres and Neo4j schema. |
| `demo-harness/` | HR, Sales, Product, and Engineering mock services. |
| `notebooks/` | Jupyter notebooks for the HR harness (`00`–`30`), the other three sources (`02`, `14`–`16`), and the gateway (`40`–`44`). |
| `infrastructure/` | Compose orchestration, Terraform provisioning, floci config, and `config/sources` YAML contracts. |
| `observability` | Alloy, Loki, Prometheus, Tempo, five Grafana dashboards, and metric definitions. |
| `shared` | Contracts, the registry gate, and logging. Source contracts in `shared/src/shared/contracts` are checked by `make contracts-check`. |
| `tools` | `scripts/dlq_replay.py`. Evaluation scripts are not written. |

## Related decisions

- [Spike 01: connecting heterogeneous sources](../01--spikes/01--handling-data-from-distributed-sources-w-diff-formats-access-methods-n-ownership.md)
- [Spike 02: knowledge representation](../01--spikes/02--transforming-data-into-knowledge-that-ai-agents-can-easily-interpret-and-use.md)
- [Spike 03: agent access layer](../01--spikes/03--exposing-knowledge-to-ai-agents-though-consistent-access-layer.md)
- [Spike 04: self-maintenance](../01--spikes/04--self-maintaing-over-time-refelcting-additions-modifications-n-deletions-in-the-source-systems-without-manual-intervention.md)
