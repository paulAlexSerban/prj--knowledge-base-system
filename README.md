# Unified Knowledge Gateway

The Unified Knowledge Gateway is an event-driven hybrid knowledge base. It ingests department data, turns it into knowledge agents can use, and serves it through one API and one MCP server with the caller's permissions applied in the data layer.

This repository is a proof of concept for that gateway. The design is written down. The source-contract gate is implemented and checked in CI. The running stack (harness, workers, stores, API) is the next implementation step and is marked as planned below.

## Status

| Built | Planned |
| ----- | ------- |
| Project brief, spikes, ADRs, and the architecture outline | Demo harness: FastAPI plus SQLite and files for HR, Sales, Product, and Engineering |
| C4 diagrams (context, containers, components, connector containers) | Connector and ingestion workers, SQS, S3, Postgres, Neo4j |
| Source-contract model, four YAML contracts, validator, tests, and CI | Knowledge API, MCP server, mock identity provider, evaluation set, one Grafana dashboard |
| | Docker Compose and Terraform against [floci](https://floci.io) |

## Problem

A company keeps operational knowledge in systems owned by different departments, with no shared schema and no shared access model. Agents that automate work across departments need that knowledge to be current and authorized at the moment they act.

The POC is sized to answer two questions:

1. "What is our parental leave policy in Germany?" Answer from authorized policy chunks, with a citation.
2. "Which high-ARR Sales deals in Q3 depend on unreleased Engineering epics?" Answer from the knowledge graph, with the path.

Assumptions, the requirements that were narrowed, and the in-scope list are in the [project brief](_docs/00--project-brief/00-project-brief.md).

## Architecture

System context: agents talk to the gateway, and the gateway reads department sources.

![System context](_docs/00--project-brief/00-c4-l1--system-context.png)

Containers: an access layer, an ingestion pipeline, and a knowledge store.

![Containers](_docs/00--project-brief/00-c4-l2--container-diagram--knowledge-base-system.png)

Connector containers: one worker per harness service, a validated contract, and a queue.

![Connector containers](_docs/00--project-brief/01-c4-l2--container-connectors.png)

Component view of the gateway:

![Components](_docs/00--project-brief/00-c4-l3--component-diagram--kowledge-base-system.png)

| Concern | Choice |
| ------- | ------ |
| Language and HTTP | Python 3.12, FastAPI. The API and the MCP server are thin adapters over one service layer. |
| Documents, vectors, lexical search, authorization | Postgres with pgvector, full-text search, and row-level security. |
| Knowledge graph | Neo4j, small typed ontology. |
| Messaging and objects | SQS (FIFO per object, dead-letter queue), S3, and EventBridge Scheduler, emulated with floci. |
| Models | OpenRouter when `OPENROUTER_API_KEY` is set. A local embedder and a deterministic extractor for offline runs. |
| Observability | OpenTelemetry, Prometheus, and one Grafana dashboard. |

Decisions, and what was rejected:

- Connectors implement one interface. Each source has an owner, a freshness tier, and an ACL mapping. Generic ELT and live federated query were rejected. [Spike 01](_docs/01--spikes/01--handling-data-from-distributed-sources-w-diff-formats-access-methods-n-ownership.md).
- Hybrid search (pgvector plus full-text, merged with reciprocal rank fusion) answers prose. The graph answers multi-hop questions. A reranker waits for a measured precision gap. [Spike 02](_docs/01--spikes/02--transforming-data-into-knowledge-that-ai-agents-can-easily-interpret-and-use.md), [ADR 0001](_docs/03--adrs/0001-storage.md).
- Agents never hold store credentials. Authorization is a coarse pre-filter, a fine-grained check, and Postgres row-level security, with an audit row per result. [Spike 03](_docs/01--spikes/03--exposing-knowledge-to-ai-agents-though-consistent-access-layer.md).
- Ingestion is a stream of create, update, delete, and ACL events. Deletes are hard deletes plus a reconciliation sweep. SQS is enough at the stated volume; Kafka is a production path. [Spike 04](_docs/01--spikes/04--self-maintaing-over-time-refelcting-additions-modifications-n-deletions-in-the-source-systems-without-manual-intervention.md).
- Chunks record `embedding_model` and `chunker_version`. [ADR 0003](_docs/03--adrs/0003-embedding-and-chunk-versioning.md). Sync lag, dead-letter depth, and empty-result rate by permission group are the dashboard. [ADR 0002](_docs/03--adrs/0002-observability.md).

## Quickstart

Works today. From the repository root, with Python 3.12:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make contracts-check
```

`make contracts-check` validates the four source contracts, runs their tests, and fails if the committed JSON Schema drifted from the Pydantic model.

Planned, once Compose and floci land. These Make targets do not exist yet:

```bash
export OPENROUTER_API_KEY=...   # optional; offline fallback runs without it
make up      # compose: harness, workers, postgres, neo4j, floci, grafana
make seed    # load the demo corpus
make demo    # run the script below
make test    # unit and access-boundary tests
make eval    # golden set: recall@k, MRR, abstention, access boundaries
```

## Demo script

These five steps are the acceptance test. They become runnable with `make demo`. None of them run against a live stack today.

1. Ask the Germany parental-leave question as a user who may read HR policies. The answer cites the chunk, source version, and last sync time.
2. Ask the same question as a user whose claims do not include that policy. The result set is empty, and the audit log records the deny.
3. Ask which high-ARR Q3 deals depend on unreleased engineering epics. The answer lists deals, epics, and the path.
4. Edit a policy and delete a deal in the harness. The edited chunk is re-embedded and the deal disappears from Postgres and Neo4j. Sync lag for that source is on the dashboard.
5. Stop an ingestion worker mid-batch and start it again. Messages retry or land in the dead-letter queue. Replaying them does not duplicate chunks or edges.

## Source contracts

A connector starts only when its YAML contract validates. Required: an owner (team and contact), a freshness tier (`P0`, `P1`, or `P2`), and at least one ACL mapping onto `department`, `clearance`, `region`, or `group`. High sensitivity requires an ACL re-check at read time. Fields named `salary`, `compensation`, `ssn`, or `national_id` cannot be in scope.

Contracts live in [infrastructure/config/sources](infrastructure/config/sources). The model is [shared/contracts/source_contract.py](shared/contracts/source_contract.py). To add a source, add a YAML file and run `make contracts-check`. Worker bootstrap will call `shared.contracts.validate.load_contracts` and refuse to start when that raises.

## Repository map

| Path | Role |
| ---- | ---- |
| `_docs/00--project-brief/` | Brief and C4 diagrams. |
| `_docs/01--spikes/` | Decisions for ingestion, knowledge representation, access, and synchronization. |
| `_docs/02--architecture/` | Architecture document. Outline, body not written yet. |
| `_docs/03--adrs/` | Storage, observability, and embedding and chunk versioning. |
| `shared/contracts/` | Source-contract model, schema, validator, and tests. |
| `infrastructure/config/sources/` | The four YAML contracts. |
| `backend/` | API, MCP, services, workers, and messaging. Empty. |
| `database/` | Postgres and Neo4j schema. Empty. |
| `demo-harness/` | HR, Sales, Product, and Engineering mocks. Empty. |
| `infrastructure/` | Compose, Terraform, and floci config. Contracts only, so far. |
| `observability/` | Dashboard and metric definitions. Empty. |
| `tools/` | Seed, mutation, and evaluation scripts. Empty. |
| `.github/workflows/contracts.yml` | CI for the contract gate. |

## Assumptions and limits

Production assumptions (about 10 million objects, about 100,000 changes a day, freshness tiers, 250 ms retrieval) are design targets. The POC uses four sources and a few hundred documents. The 30-second tier applies only where the source can push or be polled that fast. The 250 ms budget covers hybrid search with an authorization filter and does not include a reranker or model generation. Freshness and latency will be measured; they are not load-test results.

floci emulates SQS, S3, and EventBridge Scheduler. It does not emulate Bedrock or IAM evaluation. Model calls go to OpenRouter or the local fallback.

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
- [Architecture document](_docs/02--architecture/01--architecture-document.md) (outline)
- [ADR 0001: storage](_docs/03--adrs/0001-storage.md)
- [ADR 0002: observability](_docs/03--adrs/0002-observability.md)
- [ADR 0003: embedding and chunk versioning](_docs/03--adrs/0003-embedding-and-chunk-versioning.md)
