# Unified Knowledge Gateway

The Unified Knowledge Gateway is an event-driven hybrid knowledge base. It ingests department data, turns it into knowledge agents can use, and serves it through one API and one MCP server with the caller's permissions applied in the data layer.

This repository is a proof of concept for that gateway. The design is written down. The source-contract gate is implemented and checked in CI. Compose starts the harness, workers, stores, API, MCP server, floci, Jupyter, pgAdmin, the SQLite viewers, and the observability stack (Alloy, Loki, Prometheus, and Grafana). The gateway and the ingestion worker are stubs: they answer health checks and do not retrieve or answer questions yet. The HR harness serves a seeded directory and policies. The HR connector polls it and writes raw snapshots to S3 on floci.

## Status


| Built                                                                                                | Planned                                                       |
| ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| Project brief, spikes, ADRs, and the architecture document                                           | Retrieval, chunking, embeddings, and the two demo answers     |
| C4 diagrams (context, containers, components, connector containers)                                  | Mock identity provider and access-boundary tests              |
| Source-contract model, four YAML contracts, validator, tests, and CI                                 | Golden evaluation set                                         |
| Compose projects for the harness, gateway, ingestion, store, floci, Jupyter, pgAdmin, SQLite viewers, and observability (Alloy, Loki, Prometheus, Grafana). The HR harness is seeded. The HR connector writes raw snapshots to S3. | Queue emit, chunking, retrieval, and the remaining dashboard panels |
| Terraform on floci: three FIFO queues, three dead-letter queues, one snapshot bucket                 | Seed data for Sales, Product, and Engineering, and `make demo` |




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
| Models                                            | OpenRouter when `OPENROUTER_API_KEY` is set. A local embedder and a deterministic extractor for offline runs.                                                               |
| Observability                                     | Alloy collects metrics and container logs. Prometheus stores metrics, Loki stores logs, Grafana shows both. Traces are deferred.                                             |


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

`make test` runs the contract tests and the stub tests. `make up` builds the images and starts every Compose project, then runs Terraform on the host. The first start downloads images and the AWS provider. Neo4j needs about two minutes before it is healthy. Expect several gigabytes of RAM. Postgres and Neo4j are not published on the host. `terraform` must be on `PATH` (1.5 or newer).

Postgres 18 cannot open a data directory written by 16. If `kbs_pgdata` already exists from the older image, remove it once before `make up`: `docker volume rm kbs_pgdata`. There is no upgrade step in this POC.


| Port      | Service                                           |
| --------- | ------------------------------------------------- |
| 8000      | Knowledge API (`/health`, `/v1/sources`)          |
| 8001      | MCP server (`/health`, streamable HTTP at `/mcp`) |
| 8090      | Product webhook receiver                          |
| 8101–8104 | HR, Sales, Product, Engineering harness           |
| 8111–8114 | SQLite viewers for HR, Sales, Product, Engineering. Read-only. |
| 8888      | Jupyter (`make notebooks`). Token is `JUPYTER_TOKEN`. |
| 5050      | pgAdmin, desktop mode, no login. Dev only.        |
| 9090      | Prometheus                                        |
| 3000      | Grafana, anonymous viewer, dev only. Logs are read here. Loki and Alloy are not published. |
| 4566      | floci, bound to 127.0.0.1                         |


```bash
make down
```

`make notebooks` starts Jupyter on port 8888. The seed notebook loads up to 10,000 HR people or policies through the harness. `make tools` starts pgAdmin and the four SQLite viewers, plus Postgres and the harness so those databases exist. `make tf-apply` creates the queues and the snapshot bucket again. `make seed`, `make demo`, and `make eval` do not exist yet. Details of networks and how to start one project alone are in [infrastructure/README.md](infrastructure/README.md).

## Demo script

These five steps are the acceptance test. The processes start today. None of these questions can be answered until retrieval exists.

1. Ask the Germany parental-leave question as a user who may read HR policies. The answer cites the chunk, source version, and last sync time.
2. Ask the same question as a user whose claims do not include that policy. The result set is empty. The audit log records the deny, and Grafana opens that line by `request_id`.
3. Ask which high-ARR Q3 deals depend on unreleased engineering epics. The answer lists deals, epics, and the path.
4. Edit a policy and delete a deal in the harness. The edited chunk is re-embedded and the deal disappears from Postgres and Neo4j. Sync lag for that source is on the dashboard.
5. Stop an ingestion worker mid-batch and start it again. Messages retry or land in the dead-letter queue. Replaying them does not duplicate chunks or edges.



## Source contracts

A connector starts only when its YAML contract validates. Required: an owner (team and contact), a freshness tier (`P0`, `P1`, or `P2`), and at least one ACL mapping onto `department`, `clearance`, `region`, or `group`. High sensitivity requires an ACL re-check at read time. Fields named `salary`, `compensation`, `ssn`, or `national_id` cannot be in scope.

Contracts live in [infrastructure/config/sources](infrastructure/config/sources). The model is [shared/src/shared/contracts/source_contract.py](shared/src/shared/contracts/source_contract.py). To add a source, add a YAML file and run `make contracts-check`. A process calls `shared.registry.load_registry` and refuses to start when that raises.

## Repository map


| Path                             | Role                                                                            |
| -------------------------------- | ------------------------------------------------------------------------------- |
| `_docs/00--project-brief/`       | Brief and C4 diagrams.                                                          |
| `_docs/01--spikes/`              | Decisions for ingestion, knowledge representation, access, and synchronization. |
| `_docs/02--architecture/`        | Architecture document (Deliverable 1).                                          |
| `_docs/03--adrs/`                | Storage, observability, and embedding and chunk versioning.                     |
| `infrastructure/config/sources/` | The four YAML contracts.                                                        |
| `shared/`                        | Source contracts, registry gate, and JSON logging. The model is [shared/src/shared/contracts/source_contract.py](shared/src/shared/contracts/source_contract.py). |
| `backend/apis/`                  | `knowledge-api`, `mcp-server`, and `webhook-receiver`, each with its own image. |
| `backend/connectors/hr/`         | HR connector process `connector-hr`. |
| `backend/workers/ingestion-worker/` | Queue consumer stub. |
| `backend/libs/`                  | `connector-core` and `ingest-contract`. Libraries, not services. |
| `backend/tests/`                 | Integration tests, plus `e2e/` for `make e2e`. |
| `database/`                      | Postgres init (pgvector, roles, placeholder RLS) and Neo4j constraints.         |
| `demo-harness/`                  | One image per source. HR is seeded. Sales, Product, and Engineering are empty stubs. |
| `notebooks/`                     | Jupyter seed and inspect notebooks for HR.                                      |
| `infrastructure/`                | Compose projects, Terraform for floci, source contracts.                        |
| `observability/`                 | Alloy and Loki config, Prometheus scrape config, and the Grafana dashboard.    |
| `tools/`                         | Seed, mutation, and evaluation scripts. Empty.                                  |
| `.github/workflows/`             | Contract gate and Compose config CI.                                            |




## Assumptions and limits

Production assumptions (about 10 million objects, about 100,000 changes a day, freshness tiers, 250 ms retrieval) are design targets. The POC uses four sources and a few hundred documents. The 30-second tier applies only where the source can push or be polled that fast. The 250 ms budget covers hybrid search with an authorization filter and does not include a reranker or model generation. Freshness and latency will be measured; they are not load-test results.

floci's own documentation lists many AWS APIs, including SQS, S3, Scheduler, IAM, and Bedrock Runtime. A local apply created the FIFO queues and a send/receive on `kbs-p0.fifo` succeeded. That is not a fidelity or load test. Model calls stay on OpenRouter or the local fallback because the gateway should not depend on a specific cloud model API, not because floci lacks one.

EventBridge Scheduler cannot run faster than once a minute, on AWS or on floci. It is the wrong driver for the 30-second tier. P0 polling stays inside the connector worker. Scheduler, when wired, covers P1/P2 polls and the reconciliation sweep.

Docker networks show the intended trust boundaries. They are not a security boundary. Terraform in this repo talks only to floci. It creates no VPC, database, or compute. State stays in `infrastructure/provisioning/terraform/terraform.tfstate` on the machine that ran `make tf-apply`. That file is not committed. pgAdmin is a dev-only loopback bridge onto the internal data network. The Postgres port stays unpublished.

The next useful slice is one HR document through chunk, embed, hybrid search, and a cited API answer. More platform work before that does not make the demo.

Compensation and national identifiers are not ingested. The graph trusts edges extracted in code from structured records. Edges extracted from prose carry confidence and a source span and do not decide the headline answer. Entity resolution beyond shared ids and email addresses is deferred, and the demo data shares one product key on purpose.

The full risk list is in the [project brief](_docs/00--project-brief/00-project-brief.md).

## How AI was used

Design, implementation, tests, and review on this POC were done with an AI coding agent in Cursor, with a human setting scope and accepting each plan before code or docs were written.

- Design. Research notes were rewritten into decision records. Each spike states the problem, the POC choice, the alternatives rejected, and the risk. The agent drafted those from the assignment and the notes. The human kept the scope to four sources and to what a demo can show.
- Implementation. Each slice started as a written plan (stack, files, and out-of-scope list) and was implemented only after that plan was accepted. The contract model, the connector diagram, and CI came out of that loop. The agent did not add Kafka, a reranker, or extra departments when the plan had deferred them.
- Testing. The contract tests were generated with the model: the four real contracts pass, and incomplete contracts fail (missing owner, missing tier, empty ACL mapping, forbidden fields). `make contracts-check` is the command a reviewer can run today.
- Review. The human reads the docs against the assignment: reasoning, rejected alternatives, explicit assumptions, and honesty about what is not built. The status table in this file is part of that review. Access-boundary tests and the golden set are specified in the spikes and are not implemented yet.



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

