# Unified Knowledge Gateway

The Unified Knowledge Gateway is an event-driven hybrid knowledge base. It ingests department data, turns it into knowledge agents can use, and serves it through one API and one MCP server with the caller's permissions applied in the data layer.

This repository is a proof of concept for that gateway. The design is written down. The source-contract gate is implemented and checked in CI. Compose starts the harness, workers, stores, API, MCP server, floci, Jupyter, pgAdmin, the SQLite viewers, and the observability stack (Alloy, Loki, Prometheus, and Grafana). Four connectors write raw snapshots to S3 and enqueue change events. The ingestion worker chunks those objects, embeds them with a deterministic hash, and writes Postgres and Neo4j. The API and the MCP server search HR chunks under the caller's token. They return cited snippets. They do not write an answer, and they do not yet answer from deals or epics. A Streamlit chat page (`frontend/`) calls the REST API as a chosen persona and writes the answer from those snippets.

## Status


| Built | Planned |
| ----- | ------- |
| Project brief, spikes, ADRs, the architecture document, and the C4 diagrams | Query router, answer synthesis, and a gateway answer for demo question 2 |
| Source-contract model, four YAML contracts, validator, tests, and CI. Sales, Product, and Engineering harnesses and connectors. | `make demo` |
| Compose for the harness, gateway, ingestion, store, floci, Jupyter, pgAdmin, SQLite viewers, and observability. HR can bulk-load. The other three sources are a curated set. | Golden evaluation set and `make eval` |
| Terraform on floci: three tier queues, their dead-letter queues, the product hook queue and its dead-letter queue, and one snapshot bucket. `make dlq-replay` sends dead-letter messages back. | Loki and Tempo on object storage |
| Ingestion worker: chunking, hash embeddings (`hash-v1`, 384 dimensions, not semantic), Postgres chunks (full-text, HNSW, ACL columns, row-level security, tombstones), and the Neo4j writer. HR writes Person, Team, and Policy. A deal stores arr, quarter, stage, and product_key and depends on an epic. An epic stores team and product_key and is owned by an HR team. Specs stay prose. | A semantic embedder in place of `hash-v1` |
| REST and MCP retrieval over `gateway-core`: search (one chunk per object), get document, bounded HR graph lookup, and list sources. Dev-only `POST /dev/token` signs an HS256 JWT. HR policy search re-checks the harness ACL. | OIDC in place of the shared-secret JWT. Gateway routing for deals, epics, and specs. |
| Grafana: overview, ingestion, retrieval and access, platform, and traces. One series per source for sync lag and drift, plus retrieval p95, empty-result rate by group, and auth failures. A deny is a log line, and the same `trace_id` opens the trace. Notebooks `00`–`30` edit HR, `02` and `14`–`16` edit the other sources, and `40`–`43` call the gateway. | Alertmanager |




## Problem

A company keeps operational knowledge in systems owned by different departments, with no shared schema and no shared access model. Agents that automate work across departments need that knowledge to be current and authorized at the moment they act.

The POC is sized to answer two questions:

1. "What is our parental leave policy in Germany?" Answer from authorized policy chunks, with a citation.
2. "Which high-ARR Sales deals in Q3 depend on unreleased Engineering epics?" Answer from the knowledge graph, with the path.

Assumptions, the requirements that were narrowed, and the in-scope list are in the [project brief](_docs/00--project-brief/00-project-brief.md).

## Architecture

System context: agents talk to the gateway, and the gateway reads department sources.

System context

Containers: an access layer, an ingestion pipeline, and a knowledge store.

Containers

Connector containers: one worker per harness service, a validated contract, and a queue.

Connector containers

Component view of the gateway:

Components


| Concern                                           | Choice                                                                                                                                                                      |
| ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Language and HTTP                                 | Python 3.12, FastAPI. The API and the MCP server are thin adapters over one service layer.                                                                                  |
| Documents, vectors, lexical search, authorization | Postgres with pgvector, full-text search, and row-level security.                                                                                                           |
| Knowledge graph                                   | Neo4j, small typed ontology.                                                                                                                                                |
| Messaging and objects                             | SQS FIFO per object, with a dead-letter queue, and S3, both on floci. Scheduler, when added, drives P1/P2 polls and reconciliation. P0 polling is in the connector process. |
| Models                                            | OpenRouter when `OPENROUTER_API_KEY` is set. The chat page calls it through LangChain and falls back to the cited snippets. A local embedder and a deterministic extractor for offline runs. |
| Observability                                     | Alloy collects metrics, container logs, and OTLP traces. Prometheus stores metrics, Loki stores logs, Tempo stores traces, and Grafana shows them. LangSmith is off unless `LANGSMITH_API_KEY` is set. |


Decisions, and what was rejected:

- Connectors implement one interface. Each source has an owner, a freshness tier, and an ACL mapping. Generic ELT and live federated query were rejected. [Spike 01](_docs/01--spikes/01--handling-data-from-distributed-sources-w-diff-formats-access-methods-n-ownership.md).
- Hybrid search (pgvector plus full-text, merged with reciprocal rank fusion) answers prose. The graph answers multi-hop questions. A reranker waits for a measured precision gap. [Spike 02](_docs/01--spikes/02--transforming-data-into-knowledge-that-ai-agents-can-easily-interpret-and-use.md), [ADR 0001](_docs/03--adrs/0001-storage.md).
- Agents never hold store credentials. Authorization is a coarse pre-filter, a fine-grained check, and Postgres row-level security, with an audit row per result. [Spike 03](_docs/01--spikes/03--exposing-knowledge-to-ai-agents-though-consistent-access-layer.md).
- Ingestion is a stream of create, update, delete, and ACL events. Deletes are hard deletes plus a reconciliation sweep. SQS is enough at the stated volume; Kafka is a production path. [Spike 04](_docs/01--spikes/04--self-maintaing-over-time-refelcting-additions-modifications-n-deletions-in-the-source-systems-without-manual-intervention.md).
- Chunks record `embedding_model` and `chunker_version`. [ADR 0003](_docs/03--adrs/0003-embedding-and-chunk-versioning.md). Sync lag, dead-letter depth, and empty-result rate by permission group are the dashboard. [ADR 0002](_docs/03--adrs/0002-observability.md).



## Quickstart

Works today. From the repository root. [uv](https://docs.astral.sh/uv/) installs Python 3.12 and the dependencies:

```bash
make sync
make test
cp .env.example .env
make up
```

`make test` runs the contract tests, the connector tests, the queue tests, and the store tests. `make up` builds the images and starts every Compose project, then runs Terraform on the host. The first start downloads images and the AWS provider. Neo4j needs about two minutes before it is healthy. Expect several gigabytes of RAM. Postgres and Neo4j are not published on the host. `terraform` must be on `PATH` (1.5 or newer).

Postgres 18 cannot open a data directory written by 16. If `kbs_pgdata` already exists from the older image, remove it once before `make up`: `docker volume rm kbs_pgdata`. There is no upgrade step in this POC.


| Port      | Service                                           |
| --------- | ------------------------------------------------- |
| 8000      | Knowledge API. `/health`, `/metrics`, `/v1/sources`, `/v1/search`, `/v1/documents/{id}`, `/v1/graph/related`, and dev-only `/dev/token`. |
| 8001      | MCP server. `/health`, `/metrics`, and streamable HTTP at `/mcp`. Tools: `search_knowledge`, `get_document`, `find_related`, `list_sources`. |
| 8501      | Chat page (Streamlit). Pick a persona in the sidebar. With `OPENROUTER_API_KEY` set it writes an answer with citations; without a key it shows the retrieved passages. |
| 8090      | Product webhook receiver                          |
| 8101–8104 | HR, Sales, Product, Engineering harness           |
| 9100      | HR connector metrics                              |
| 9101      | Ingestion worker metrics                          |
| 9102–9104 | Engineering, sales, and product connector metrics |
| 8111–8114 | SQLite viewers for HR, Sales, Product, Engineering. Read-only. |
| 8888      | Jupyter (`make notebooks`). Token is `JUPYTER_TOKEN`. |
| 5050      | pgAdmin, desktop mode, no login. Dev only.        |
| 9090      | Prometheus                                        |
| 3000      | Grafana. Log in as `admin` / `admin`, or set `GRAFANA_ADMIN_PASSWORD`. Logs are read here. Loki and Alloy are not published. |
| 4566      | floci, bound to 127.0.0.1                         |


```bash
make down
```

`make dlq-replay` moves dead-letter messages back onto the tier queues. floci must already be up. `make notebooks` starts Jupyter on port 8888. The seed notebook resets the HR harness and loads 200 people by default (`SEED_COUNT=10000` is the scale load). Notebook `02` resets the curated sales, product, and engineering sets. Notebooks `10`–`30` create, update, and delete HR rows and check the S3 snapshots. Notebooks `14`–`16` do the same for a deal, an epic, and a spec. Notebooks `40`–`43` mint tokens, search as three personas, call the MCP tools, and edit a policy through the gateway. `make tools` starts pgAdmin and the four SQLite viewers, plus Postgres and the harness so those databases exist. `make tf-apply` creates the queues and the snapshot bucket again. `make seed`, `make demo`, and `make eval` do not exist yet. Details of networks and how to start one project alone are in [infrastructure/README.md](infrastructure/README.md). Set `GATEWAY_JWT_SECRET` in `.env` before `make up`. `POST /dev/token` stays off unless `DEV_TOKEN_ENDPOINT=1`. `.env.example` sets that for the local demo.

## Demo script

These five steps are the acceptance test. The gateway returns cited chunks. It does not write the answer. `hash-v1` matches shared words, not meaning. Notebooks `41` and `43` run the HR steps.

1. Demonstrable. `POST /v1/search` as `employee_eng` with "parental leave Germany" returns the Germany policy chunk, its version, and its sync time. Notebook `41`.
2. Demonstrable. The same query as `outsider` returns an empty list. `kbs.audit_log` records the deny, and the gateway log line has `decision: deny`. Open that search on KBS / Traces, then the deny log by `trace_id`. The empty-result rate for the `none` group moves. Notebook `41`.
3. Demonstrable on the graph, not through the gateway. After ingestion, a Q3 deal whose ARR is 200,000 or more and whose epic is not released has a Neo4j path from the deal to that epic to the HR team that owns it. `find_related` still covers HR only: teammates, a team, and policies. Notebook `02` resets the three sources.
4. Demonstrable for an HR policy. Notebook `43` creates a policy, closes its ACL, and deletes it. Search and `find_related` drop it. Deleting a deal is demonstrable in the snapshot, the chunks, and the graph. Notebook `14`. Search does not cover deals yet.
5. Demonstrable at the worker. Stop the ingestion worker mid-batch and start it again. Messages retry or land in the dead-letter queue. Replaying them does not duplicate chunks or edges.



## Source contracts

A connector starts only when its YAML contract validates. Required: an owner (team and contact), a freshness tier (`P0`, `P1`, or `P2`), and at least one ACL mapping. Each rule requires `group`, `clearance`, and `department`. `department: "*"` means every department. `region` is optional and is not an access predicate. High sensitivity requires an ACL re-check at read time. Fields named `salary`, `compensation`, `ssn`, or `national_id` cannot be in scope.

Contracts live in [infrastructure/config/sources](infrastructure/config/sources). The model is [shared/src/shared/contracts/source_contract.py](shared/src/shared/contracts/source_contract.py). To add a source, add a YAML file and run `make contracts-check`. A process calls `shared.registry.load_registry` and refuses to start when that raises.

## Repository map


| Path                             | Role                                                                            |
| -------------------------------- | ------------------------------------------------------------------------------- |
| `_docs/00--project-brief/`       | Brief and C4 diagrams.                                                          |
| `_docs/01--spikes/`              | Decisions for ingestion, knowledge representation, access, and synchronization. |
| `_docs/02--architecture/`        | Architecture document (Deliverable 1).                                          |
| `_docs/03--adrs/`                | Storage, observability, and embedding and chunk versioning.                     |
| `infrastructure/config/sources/` | The four YAML contracts.                                                        |
| `shared/`                        | Source contracts, registry gate, JSON logging, and the OpenTelemetry bootstrap. The model is [shared/src/shared/contracts/source_contract.py](shared/src/shared/contracts/source_contract.py). |
| `backend/apis/`                  | `knowledge-api`, `mcp-server`, and `webhook-receiver`, each with its own image. |
| `backend/connectors/`            | One process per source: `connector-hr`, `connector-engineering`, `connector-sales`, `connector-product`. |
| `backend/workers/ingestion-worker/` | Queue consumer. Writes chunks and the graph. |
| `backend/libs/`                  | `connector-core`, `ingest-contract`, `knowledge-store`, and `gateway-core`. Libraries, not services. |
| `backend/tests/`                 | Integration tests, plus `e2e/` for `make e2e`. |
| `database/`                      | Postgres migrations (sync state, chunks, full-text, vectors, ACL, row-level security) and Neo4j constraints. |
| `demo-harness/`                  | One image per source. HR can bulk-load. Sales, Product, and Engineering are a curated set. |
| `frontend/`                      | Streamlit chat page `chat-ui`. Own image. Calls the REST API only. |
| `notebooks/`                     | Jupyter notebooks that seed and edit HR (`00`–`30`), reset and edit the other three sources (`02`, `14`–`16`), and call the gateway (`40`–`43`). |
| `infrastructure/`                | Compose projects, Terraform for floci, source contracts.                        |
| `observability/`                 | Alloy, Loki, Tempo, Prometheus, five Grafana dashboards, and metric definitions. |
| `tools/`                         | `scripts/dlq_replay.py`, used by `make dlq-replay`. Evaluation scripts are not written. |
| `.github/workflows/`             | Contract gate and Compose config CI.                                            |




## Assumptions and limits

Production assumptions (about 10 million objects, about 100,000 changes a day, freshness tiers, 250 ms retrieval) are design targets. The POC uses four sources and a few hundred documents. The 30-second tier applies only where the source can push or be polled that fast. The 250 ms budget covers hybrid search with an authorization filter and does not include a reranker or model generation. Freshness and latency will be measured; they are not load-test results.

floci's own documentation lists many AWS APIs, including SQS, S3, Scheduler, IAM, and Bedrock Runtime. A local apply created the FIFO queues and a send/receive on `kbs-p0.fifo` succeeded. That is not a fidelity or load test. Model calls stay on OpenRouter or the local fallback because the gateway should not depend on a specific cloud model API, not because floci lacks one.

EventBridge Scheduler cannot run faster than once a minute, on AWS or on floci. It is the wrong driver for the 30-second tier. P0 polling stays inside the connector worker. Scheduler, when wired, covers P1/P2 polls and the reconciliation sweep.

Docker networks show the intended trust boundaries. They are not a security boundary. Terraform in this repo talks only to floci. It creates no VPC, database, or compute. State stays in `infrastructure/provisioning/terraform/terraform.tfstate` on the machine that ran `make tf-apply`. That file is not committed. pgAdmin is a dev-only loopback bridge onto the internal data network. The Postgres port stays unpublished.

REST and MCP search are in place for HR. Deals, epics, and specs are ingested. The next slice is a gateway answer for question 2, and a semantic embedder. `hash-v1` is not a semantic model, so the parental-leave result depends on shared words. The JWT is HS256 with a shared secret. That is a stub for OIDC. Neo4j Community has no roles, so graph authorization is the Cypher template plus a read-only session.

Compensation and national identifiers are not ingested. The graph trusts edges extracted in code from structured records. Edges extracted from prose carry confidence and a source span and do not decide the headline answer. Entity resolution beyond shared ids and email addresses is deferred, and the demo data shares one product key on purpose.

The full risk list is in the [project brief](_docs/00--project-brief/00-project-brief.md).

## How AI was used

Design, implementation, tests, and review on this POC were done with an AI coding agent in Cursor, with a human setting scope and accepting each plan before code or docs were written.

- Design. Research notes were rewritten into decision records. Each spike states the problem, the POC choice, the alternatives rejected, and the risk. The agent drafted those from the assignment and the notes. The human kept the scope to four sources and to what a demo can show.
- Implementation. Each slice started as a written plan (stack, files, and out-of-scope list) and was implemented only after that plan was accepted. The contract model, the connector diagram, and CI came out of that loop. The agent did not add Kafka, a reranker, or extra departments when the plan had deferred them.
- Testing. The contract tests were generated with the model: the four real contracts pass, and incomplete contracts fail (missing owner, missing tier, empty ACL mapping, forbidden fields). `make contracts-check` is the command a reviewer can run today.
- Review. The human reads the docs against the assignment: reasoning, rejected alternatives, explicit assumptions, and honesty about what is not built. The status table in this file is part of that review. API tests cover a known chunk, a query with no shared terms, two countries in one result, and an access boundary. The golden set of about 20 questions is not built.



## Documentation

- [Project brief](_docs/00--project-brief/00-project-brief.md)
- [Spike 01: connecting sources](_docs/01--spikes/01--handling-data-from-distributed-sources-w-diff-formats-access-methods-n-ownership.md)
- [Spike 02: knowledge representation](_docs/01--spikes/02--transforming-data-into-knowledge-that-ai-agents-can-easily-interpret-and-use.md)
- [Spike 03: agent access](_docs/01--spikes/03--exposing-knowledge-to-ai-agents-though-consistent-access-layer.md)
- [Spike 04: staying current](_docs/01--spikes/04--self-maintaing-over-time-refelcting-additions-modifications-n-deletions-in-the-source-systems-without-manual-intervention.md)
- [Architecture document](_docs/02--architecture/01--architecture-document.md)
- [ADR 0001: storage](_docs/03--adrs/0001-storage.md)
- [ADR 0002: observability](_docs/03--adrs/0002-observability.md)
- [ADR 0003: embedding and chunk versioning](_docs/03--adrs/0003-embedding-and-chunk-versioning.md)

