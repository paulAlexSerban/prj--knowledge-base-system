# Unified Knowledge Gateway

The Unified Knowledge Gateway is an event-driven hybrid knowledge base. It ingests department data, turns it into knowledge agents can use, and serves it through one API and one MCP server with the caller's permissions applied in the data layer.

This repository is a proof of concept for that gateway. The design is written down. The source-contract gate is implemented and checked in CI. Compose starts the harness, workers, stores, API, MCP server, floci, Jupyter, pgAdmin, the SQLite viewers, and the observability stack (Alloy, Loki, Prometheus, and Grafana). Four connectors write raw snapshots to S3 and enqueue change events. The ingestion worker chunks those objects, embeds them with a local semantic model (bge-small through fastembed), and writes Postgres and Neo4j. The API and the MCP server search under the caller's token and return cited snippets. They do not write an answer. Demo question 2 is a graph lookup, `find_related` kind `roadmap_deals`. A Streamlit chat page (`frontend/`) calls the REST API as a chosen persona. A suggested question uses that graph lookup. Any other question uses search. The page writes the answer from the cited snippets or paths.

## Status


| Built | Production path, not built |
| ----- | -------------------------- |
| Project brief, spikes, ADRs, the architecture document, four C4 diagrams, one end-to-end data-flow diagram, and two sequence diagrams (ingestion, and the knowledge-store read path). | Query router. Question 2 is one fixed graph template, `roadmap_deals`. |
| Source-contract model, four YAML contracts, validator, tests, and CI. Sales, Product, Engineering, and HR harnesses and connectors. | OIDC in place of the shared-secret JWT. A principal that can hold more than one department. |
| `make setup`: preflight, `.env`, build, start, health wait, seed, and a smoke test. `make seed`, `make demo`, `make eval`, `make reindex`. | Loki and Tempo on object storage. Alertmanager. |
| Compose for the harness, gateway, ingestion, store, floci, Jupyter, pgAdmin, SQLite viewers, and observability. Terraform runs in a one-shot container against floci: three tier queues, the product hook queue, their dead-letter queues, and one snapshot bucket. `make dlq-replay`. | A deployed agent runtime. Agents are two notebooks and one chat page. |
| Ingestion worker: chunking, embeddings, Postgres chunks (full-text, HNSW, ACL columns, row-level security, tombstones), and the Neo4j writer. HR writes Person, Team, and Policy. A deal depends on an epic. An epic is owned by an HR team. Specs stay prose. | Spec extractor and edges extracted from prose. |
| Embedder: `bge-small-en-v1.5` through fastembed (384 dimensions), baked into the images. `hash-v1` stays the default for tests and offline runs. `make reindex` rebuilds the index when `EMBEDDER` changes. Hybrid search is pgvector cosine plus full-text, merged with reciprocal rank fusion, with a similarity floor so a question with no answer returns nothing. | Cross-encoder reranker. The golden set shows no gap, so none was added. A multilingual model if policies stop being English. |
| REST and MCP retrieval over `gateway-core`: search (one chunk per object), get document, HR graph lookup, and `roadmap_deals`. Dev-only `POST /dev/token` signs an HS256 JWT. HR policy search re-checks the harness ACL. | Lambda behind API Gateway for the webhook receiver. See the AWS production path in the architecture document. |
| Golden set of 20 cases and `make eval`: known chunk, paraphrase, no answer, conflicting sources, access boundary, and the graph. Recall at 5, MRR, and p95 latency are printed and gated. | A model-graded faithfulness judge. |
| Grafana: overview, ingestion, retrieval and access, platform, and traces. A deny is a log line, and the same `trace_id` opens the trace. Notebooks `00`–`30` edit HR, `02` and `14`–`16` edit the other sources, `40`–`44` call the gateway, `50` and `51` run LangChain agents over MCP. | |


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
| Observability                                     | Alloy collects metrics, container logs, and OTLP traces. Prometheus stores metrics, Loki stores logs, Tempo stores traces, and Grafana shows them. |


Decisions, and what was rejected:

- Connectors implement one interface. Each source has an owner, a freshness tier, and an ACL mapping. Generic ELT and live federated query were rejected. [Spike 01](_docs/01--spikes/01--handling-data-from-distributed-sources-w-diff-formats-access-methods-n-ownership.md).
- Hybrid search (pgvector plus full-text, merged with reciprocal rank fusion) answers prose. The graph answers multi-hop questions. A reranker waits for a measured precision gap. [Spike 02](_docs/01--spikes/02--transforming-data-into-knowledge-that-ai-agents-can-easily-interpret-and-use.md), [ADR 0001](_docs/03--adrs/0001-postgres-hybrid-index.md), [ADR 0002](_docs/03--adrs/0002-neo4j-graph.md), [ADR 0008](_docs/03--adrs/0008-hybrid-retrieval.md).
- Agents never hold store credentials. Authorization is a coarse pre-filter, a fine-grained check, and Postgres row-level security, with an audit row per result. [Spike 03](_docs/01--spikes/03--exposing-knowledge-to-ai-agents-though-consistent-access-layer.md).
- Ingestion is a stream of create, update, delete, and ACL events. Deletes are hard deletes plus a reconciliation sweep. SQS is enough at the stated volume; Kafka is a production path. [Spike 04](_docs/01--spikes/04--self-maintaing-over-time-refelcting-additions-modifications-n-deletions-in-the-source-systems-without-manual-intervention.md).
- Chunks record `embedding_model` and `chunker_version`. [ADR 0005](_docs/03--adrs/0005-embedding-model.md), [ADR 0006](_docs/03--adrs/0006-chunk-versioning.md). Sync lag, dead-letter depth, and empty-result rate by permission group are the dashboard. [ADR 0009](_docs/03--adrs/0009-observability-stack.md), [ADR 0010](_docs/03--adrs/0010-dashboards-as-code.md).



## Quickstart

From the repository root. You need Docker with Compose v2 and [uv](https://docs.astral.sh/uv/). Terraform runs in a container, so it is not a prerequisite.

```bash
make setup
```

`make setup` checks the prerequisites, creates `.env` from `.env.example` if it is missing, runs `uv sync`, builds and starts every Compose project, waits until every service is healthy, loads the demo data, and runs steps 1 to 3 of the demo as a smoke test. It prints the URLs at the end. It is safe to run again. `make setup ARGS=--reset` removes the containers and volumes first. The first run downloads images and the embedding model layer, and Neo4j needs about two minutes. Expect several gigabytes of RAM. Measured on the development machine: `make setup ARGS=--reset` with images already cached took 280 seconds; a first run adds the image and model downloads.

Then:

```bash
make demo        # the five-step demo script over HTTP, add ARGS=--chaos for the worker restart
make eval        # the golden set, prints recall@5, MRR, p95, exits non-zero below the thresholds
make test        # unit and store tests, no stack required beyond Docker for the store tests
make down
```

The pieces `make setup` wraps are also separate targets: `make sync`, `make up`, `make seed`, `make tf-apply`. `make test` runs the contract tests, the connector tests, the queue tests, the store tests, and the script tests. Postgres and Neo4j are not published on the host.

Postgres 18 cannot open a data directory written by 16. If `kbs_pgdata` already exists from the older image, run `make setup ARGS=--reset` or `docker volume rm kbs_pgdata`. There is no upgrade step in this POC.


| Port      | Service                                           |
| --------- | ------------------------------------------------- |
| 8000      | Knowledge API. `/health`, `/metrics`, `/v1/sources`, `/v1/search`, `/v1/documents/{id}`, `/v1/graph/related`, and dev-only `/dev/token`. |
| 8001      | MCP server. `/health`, `/metrics`, and streamable HTTP at `/mcp`. Tools: `search_knowledge`, `get_document`, `find_related`, `list_sources`. |
| 8501      | Chat page (Streamlit). Personas: `employee_eng`, `hr_admin`, `outsider`, `product_lead`. Sidebar buttons ask the two demo questions. A typed question uses search. The question-2 button uses `roadmap_deals`. With `OPENROUTER_API_KEY` set the page writes an answer. Without a key, search shows passages and question 2 shows the path table. |
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

`make dlq-replay` moves dead-letter messages back onto the tier queues. floci must already be up. `make notebooks` starts Jupyter on port 8888. The seed notebook resets the HR harness and loads 200 people by default (`SEED_COUNT=10000` is the scale load). Notebook `02` resets the curated sales, product, and engineering sets. Notebooks `10`–`30` create, update, and delete HR rows and check the S3 snapshots. Notebooks `14`–`16` do the same for a deal, an epic, and a spec. Notebooks `40`–`43` mint tokens, search as three personas, call the MCP tools, and edit a policy through the gateway. Notebook `44` asks question 2 as `product_lead` and shows the empty result for `employee_eng`. Notebooks `50` and `51` ask the same two questions through read-only LangChain agents that call MCP. They need a tool-calling model. `make tools` starts pgAdmin and the four SQLite viewers, plus Postgres and the harness so those databases exist. `make tf-apply` runs the Terraform container again; floci keeps data in memory, so run it after a floci restart. `make reindex` rebuilds the index under the active `EMBEDDER` and removes chunks written by any other model. Details of networks and how to start one project alone are in [infrastructure/README.md](infrastructure/README.md). Set `GATEWAY_JWT_SECRET` in `.env` before `make up`. `POST /dev/token` stays off unless `DEV_TOKEN_ENDPOINT=1`. `.env.example` sets that for the local demo.

## Demo script

These five steps are the acceptance test. The gateway returns cited chunks. It does not write the answer. `make demo` runs steps 1 to 4 over HTTP, and `make demo ARGS=--chaos` adds step 5. Notebooks `41` and `43` run the HR steps by hand.

1. Demonstrable. `POST /v1/search` as `employee_eng` with "parental leave Germany" returns the Germany policy chunk, its version, and its sync time. Notebook `41`.
2. Demonstrable. The same query as `outsider` returns an empty list. `kbs.audit_log` records the deny, and the gateway log line has `decision: deny`. Open that search on KBS / Traces, then the deny log by `trace_id`. The empty-result rate for the `none` group moves. Notebook `41`.
3. Demonstrable through the gateway. `POST /v1/graph/related` with kind `roadmap_deals` as `product_lead` returns the Q3 deals whose ARR is at least 200,000 and whose epic is not released, each with the epic and the team that owns it. Defaults are quarter `2026-Q3` and ARR 200,000. Leadership-only deals stay hidden. `employee_eng` and `outsider` get no paths. Notebook `44`. The chat sidebar sends this question to `roadmap_deals` and sends a typed question to search. Notebooks `50` and `51` ask these same two questions through a LangChain agent that picks the MCP tool.
4. Demonstrable for an HR policy. Notebook `43` creates a policy, closes its ACL, and deletes it. Search and `find_related` drop it. Releasing the epic, or deleting the deal, drops that path from `roadmap_deals`. Notebook `14` deletes a deal. Question 2 is a graph lookup and does not depend on search.
5. Demonstrable at the worker. Stop the ingestion worker mid-batch and start it again. Messages retry or land in the dead-letter queue. Replaying them does not duplicate chunks or edges.



## Source contracts

A connector starts only when its YAML contract validates. Required: an owner (team and contact), a freshness tier (`P0`, `P1`, or `P2`), and at least one ACL mapping. Each rule requires `group`, `clearance`, and `department`. `department: "*"` means every department. `region` is optional and is not an access predicate. High sensitivity requires an ACL re-check at read time. Fields named `salary`, `compensation`, `ssn`, or `national_id` cannot be in scope.

Contracts live in [infrastructure/config/sources](infrastructure/config/sources). The model is [shared/src/shared/contracts/source_contract.py](shared/src/shared/contracts/source_contract.py). To add a source, add a YAML file and run `make contracts-check`. A process calls `shared.registry.load_registry` and refuses to start when that raises.

## Repository map


| Path                             | Role                                                                            |
| -------------------------------- | ------------------------------------------------------------------------------- |
| `_docs/00--project-brief/`       | Brief, scope, and the demo script.                                              |
| `_docs/01--spikes/`              | Decisions for ingestion, knowledge representation, access, and synchronization. |
| `_docs/02--architecture/`        | Architecture document (Deliverable 1).                                          |
| `_docs/99--dagrams/`             | C4 diagrams, the end-to-end data flow, and the two sequence diagrams.           |
| `_docs/03--adrs/`                | One ADR per decision. Index in [the ADR readme](_docs/03--adrs/README.md).      |
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
| `notebooks/`                     | Jupyter notebooks that seed and edit HR (`00`–`30`), reset and edit the other three sources (`02`, `14`–`16`), call the gateway (`40`–`44`), and run two read-only LangChain agents (`50`, `51`). |
| `infrastructure/`                | Compose projects, Terraform for floci, source contracts.                        |
| `observability/`                 | Alloy, Loki, Tempo, Prometheus, five Grafana dashboards, and metric definitions. |
| `tools/`                         | `scripts/` (`setup.sh`, `seed.py`, `demo.py`, `dlq_replay.py`), `eval/` (golden set and runner), and their tests. |
| `.github/workflows/`             | Contract gate and Compose config CI.                                            |




## Assumptions and limits

Production assumptions (about 10 million objects, about 100,000 changes a day, freshness tiers, 250 ms retrieval) are design targets. The POC uses four sources and a few hundred documents. The 30-second tier applies only where the source can push or be polled that fast. The 250 ms budget covers hybrid search with an authorization filter and does not include a reranker or model generation. Freshness and latency will be measured; they are not load-test results.

floci's own documentation lists many AWS APIs, including SQS, S3, Scheduler, IAM, and Bedrock Runtime. A local apply created the FIFO queues and a send/receive on `kbs-p0.fifo` succeeded. That is not a fidelity or load test. Model calls stay on OpenRouter or the local fallback because the gateway should not depend on a specific cloud model API, not because floci lacks one.

EventBridge Scheduler cannot run faster than once a minute, on AWS or on floci. It is the wrong driver for the 30-second tier. P0 polling stays inside the connector worker. Scheduler, when wired, covers P1/P2 polls and the reconciliation sweep.

Docker networks show the intended trust boundaries. They are not a security boundary. Terraform in this repo talks only to floci. It creates no VPC, database, or compute. It runs in a one-shot container with throwaway state, which is correct because floci keeps data in memory. pgAdmin is a dev-only loopback bridge onto the internal data network. The Postgres port stays unpublished.

REST and MCP answer both demo questions. Notebooks `50` and `51` are two read-only LangChain agents over MCP, one per question. They need `OPENROUTER_API_KEY` and a model that can call tools, such as `openai/gpt-4o-mini`. A small model usually cannot. There is no memory and no write tool. The two agents are separate, and an orchestrator is more than this POC needs. Agent answers are not in the golden set, which is not written. The agents are notebooks, not a service. Question 2 is one hard-coded graph template, `roadmap_deals`. A query router is the production path. The `roadmap_readers` rule uses `department: "*"`, because a principal has one department and a deal and an epic would otherwise never match the same person. That star rule is a shortcut. The real fix is a principal that can hold more than one department. Leadership deals and leadership epics do not carry the extra grant. The default embedder is `bge-small-en-v1.5`, which is English only and small. The similarity floor (`VECTOR_MAX_DISTANCE`, 0.40) was calibrated on this corpus and will need re-calibration on another. Full-text search uses `plainto_tsquery`, which ANDs every term, so a long exact-words query can miss; the dense side covers it. `ts_rank` is lexical scoring, not BM25. Question 2 uses the graph, not search. The JWT is HS256 with a shared secret. That is a stub for OIDC. Neo4j Community has no roles, so graph authorization is the Cypher template plus a read-only session.

Compensation and national identifiers are not ingested. The graph trusts edges extracted in code from structured records. Edges extracted from prose carry confidence and a source span and do not decide the headline answer. Entity resolution beyond shared ids and email addresses is deferred, and the demo data shares one product key on purpose.

The full risk list is in the [project brief](_docs/00--project-brief/00-project-brief.md).

## How AI was used

This is Deliverable 2's process description. The work was done with an AI coding agent in Cursor. A human set the scope, accepted each plan before anything was written, ran the stack, and decided what to cut. The plans are kept in `.cursor/plans/` (not committed).

- Design. The assignment, the job description, and research notes went in. The agent drafted spikes and ADRs: problem, POC choice, alternatives rejected, risk. The human narrowed the requirements (the 30-second SLA, the 250 ms budget, "without manual intervention", compensation fields) and held the scope at four sources and two questions. The agent was told to say what is not built, and the status table exists because of that.
- Implementation. Each slice (contracts, connectors and snapshots, queue and worker, store and ACL, gateway and MCP, observability, the chat page, the notebook agents, then setup, embeddings, and eval) started as a written plan with files and an out-of-scope list. The agent implemented one plan at a time. Kafka, a reranker, extra departments, LangGraph for a single prompt, and LangSmith were proposed or tried and removed. The reranker stayed out because the golden set showed no gap.
- Testing. The agent wrote tests next to each change, with a negative case per validation rule. Access-boundary tests use two principals on one pool. Store tests run against real Postgres and Neo4j in containers. `make demo` and `make eval` are black-box checks over HTTP as each persona, so they test what an agent would get, not what the code intends.
- Review. The human read every plan against the assignment, then ran `make setup`, `make demo`, and `make eval` on a live stack and read the Grafana boards and traces. Docs are updated in the same change as the code they describe.
- Where the AI was wrong, and how it was caught. The first embedder was a hash, and a filter that dropped vector-only hits made search look like retrieval while it was lexical. Writing paraphrase cases for the golden set exposed it: `hash-v1` scored 0.20 recall at 5 on paraphrases, bge-small scored 1.00. Status text in the docs drifted from the code more than once, and a review pass against the code corrected it. During the setup work, the seed script asked the gateway for 50 results where the cap is 25, and the reindex command died on one stale snapshot. Both showed up only when run against the live stack, which is why the scripts run against it.

### Retrieval measurements

The same 20 cases, the same corpus, one reindex between runs (`make eval ARGS="--label ..."`):

| Run | recall at 5 | paraphrase recall at 5 | MRR | p95 latency |
| --- | ----------- | ---------------------- | --- | ----------- |
| `hash-v1` plus full-text, fused | 0.50 | 0.20 | 0.50 | 167 ms |
| `bge-small-en-v1.5` plus full-text, fused | 1.00 | 1.00 | 0.95 | 216 ms |

Latency is measured from the host through the REST API on a laptop that also runs the whole stack, and it includes the live HR ACL check. The 250 ms target is a design target and not a load test. The corpus is about 500 chunks. These numbers say the reranker has nothing to fix here. They say nothing about 10 million objects.

## Documentation

- [Project brief](_docs/00--project-brief/00-project-brief.md)
- [Spike 01: connecting sources](_docs/01--spikes/01--handling-data-from-distributed-sources-w-diff-formats-access-methods-n-ownership.md)
- [Spike 02: knowledge representation](_docs/01--spikes/02--transforming-data-into-knowledge-that-ai-agents-can-easily-interpret-and-use.md)
- [Spike 03: agent access](_docs/01--spikes/03--exposing-knowledge-to-ai-agents-though-consistent-access-layer.md)
- [Spike 04: staying current](_docs/01--spikes/04--self-maintaing-over-time-refelcting-additions-modifications-n-deletions-in-the-source-systems-without-manual-intervention.md)
- [Architecture document](_docs/02--architecture/01--architecture-document.md)
- [ADRs](_docs/03--adrs/README.md)

