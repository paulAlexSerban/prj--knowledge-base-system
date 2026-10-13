# Agent guide

This repository is a POC knowledge gateway: ingest four department sources, keep a hybrid index and a small graph current, and serve them through one API and one MCP server under the caller's permissions.

`_docs/` is the source of truth for design. When a change alters a decision, update the matching spike or ADR in the same change. Do not invent a second design in code comments.

## Layout

| Path                                     | Owns                                                                                                                                                                                                                             |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_docs/00--project-brief/`               | Scope, assumptions, demo script.                                                                                                                                                                                                 |
| `_docs/01--spikes/`                      | Ingestion, knowledge representation, access, synchronization.                                                                                                                                                                    |
| `_docs/02--architecture/`                | Architecture write-up. Fill the outline; do not add sections the assignment did not ask for.                                                                                                                                     |
| `_docs/03--adrs/`                        | One decision per ADR. The index is `_docs/03--adrs/README.md`.                                                                                                                                                                   |
| `_docs/99--dagrams/`                     | C4 diagrams, the end-to-end data flow, and the sequence diagrams (drawio and PNG).                                                                                                                                               |
| `shared/`                                | Source contracts, the start-up registry gate, JSON logging, and the OpenTelemetry bootstrap. Every process depends on it.                                                                                                        |
| `infrastructure/config/sources/`         | One YAML contract per source.                                                                                                                                                                                                    |
| `backend/apis/knowledge-api/`            | REST gateway. Own image.                                                                                                                                                                                                         |
| `backend/apis/mcp-server/`               | MCP gateway. Own image.                                                                                                                                                                                                          |
| `backend/apis/webhook-receiver/`         | Product webhook receiver. Own image. Enqueues a pointer on `kbs-product-hooks`. Production path is API Gateway plus Lambda; that function does not exist yet.                                                                    |
| `backend/workers/ingestion-worker/`      | Queue consumer. Own image. Writes chunks and the graph through `knowledge-store`.                                                                                                                                                |
| `backend/connectors/hr/`                 | HR connector process (`connector-hr`). Own image.                                                                                                                                                                                |
| `backend/connectors/engineering/`        | Engineering connector process (`connector-engineering`). Own image.                                                                                                                                                              |
| `backend/connectors/sales/`              | Sales connector process (`connector-sales`). Own image.                                                                                                                                                                          |
| `backend/connectors/product/`            | Product connector process (`connector-product`). Own image. Reads the hook queue.                                                                                                                                                |
| `backend/libs/connector-core/`           | Connector interface, snapshot sync, S3 store, Postgres sync state, and the SQS publisher. Library, no image.                                                                                                                     |
| `backend/libs/ingest-contract/`          | Change-event model, the product hook pointer, queue names, and the snapshot hash. Library, no image.                                                                                                                             |
| `backend/libs/knowledge-store/`          | Chunkers, the embedders (fastembed multilingual-e5-large and the offline hash), Postgres chunks, reindex, ACL, and the Neo4j writer. Library, no image.                                                                          |
| `backend/libs/gateway-core/`             | Retrieval service, the query router, principal and HS256 validation, Neo4j read templates (HR plus `roadmap_deals`), and the tool text shared by the API and the MCP server. Library, no image.                                 |
| `backend/tests/integration/`             | Tests that cross a deployable boundary.                                                                                                                                                                                          |
| `backend/tests/e2e/`                     | Opt-in live-stack test. `make e2e`.                                                                                                                                                                                              |
| `database/`                              | Postgres migrations (sync state, chunks, full-text, vectors, ACL, row-level security) and Neo4j constraints.                                                                                                                     |
| `demo-harness/`                          | One image per source. HR is built and can bulk-load. Sales, Product, and Engineering are curated SQLAlchemy stores on `harness-common`.                                                                                          |
| `frontend/`                              | Streamlit chat page (`chat-ui`). Own image. Calls the gateway REST API only; holds no store credentials. Writes the answer from cited snippets with LangChain when an OpenRouter key is set.                                     |
| `notebooks/`                             | Jupyter notebooks that seed, inspect, and edit the HR harness (`00`–`30`), reset and edit the other three sources (`02`, `14`–`16`), call the gateway (`40`–`44`), and run two read-only LangChain agents over MCP (`50`, `51`). |
| `infrastructure/orchestration/`          | One Compose file per trust area, plus `compose.yaml` which includes them.                                                                                                                                                        |
| `infrastructure/provisioning/terraform/` | Tier queues, the product hook queue, their dead-letter queues, and the snapshot bucket on floci.                                                                                                                                 |
| `observability/`                         | Alloy, Loki, Prometheus, Tempo, Grafana boards, and metric definitions.                                                                                                                                                          |
| `tools/`                                 | `scripts/` (`setup.sh`, `seed.py`, `demo.py`, `kbs_client.py`, `dlq_replay.py`), `eval/` (`golden.yaml`, `run.py`), and `tests/`. Scripts call the gateway and harnesses over HTTP only.                                         |

## Stack and commands

Python 3.12, FastAPI, Pydantic v2, pytest, Ruff (line length 100). [uv](https://docs.astral.sh/uv/) is the package manager. The repository is a uv workspace: each deployable and library has its own `pyproject.toml`, and `uv.lock` stays at the root. Run commands from the repository root.

```bash
uv sync
make test
make compose-check
uv run ruff check shared backend demo-harness
```

Add a dependency to the package that imports it: `uv add --package connector-hr <name>`. Add a test-only dependency with `uv add --group dev`. The notebooks group is `uv add --group notebooks`. Commit `uv.lock`. Do not use `pip` or `python -m venv`. Do not add dependencies outside a `pyproject.toml`.

`make setup` is the one-command start: preflight, `.env`, `uv sync`, `make up`, health wait, `make seed`, and the quick demo. `make up` starts the stack. Terraform runs in the one-shot `terraform-apply` container against floci. `make seed` resets the four harnesses and waits for the index. `make demo` runs the five demo steps over HTTP (`ARGS=--quick`, `ARGS=--chaos`). `make eval` runs the golden set and exits non-zero below the thresholds. `make reindex` rebuilds under the active `EMBEDDER` and removes other models' chunks. `make notebooks` starts Jupyter. `make tools` starts pgAdmin and the SQLite viewers. `make e2e` runs the live HR snapshot cycle and is not part of `make test`. `make dlq-replay` re-sends dead-letter messages to the tier queues. floci must already be up. Add a target only when the command it wraps exists, and update the README quickstart in that same change.

Dockerfiles install one package with `uv sync --frozen --no-dev --no-default-groups --package <name>`. There is no Lambda directory until a function exists. Do not publish Postgres or Neo4j ports. One Compose network per trust boundary (`kbs-data` is internal). Service names are globally unique. New services need a healthcheck and a Prometheus target. The chat page is scraped at `chat-ui:9105` (Streamlit's own port has no `/metrics`). Jupyter, pgAdmin, and the SQLite viewers are the exception: they have a healthcheck and no `/metrics` endpoint, and Alloy does not scrape them. Harness images stay root because they write a SQLite file on a volume. The other images run as `nobody`.

## Design rules

- Authorization happens in the data layer: coarse pre-filter in SQL and Cypher, fine-grained post-check, Postgres row-level security, and an audit row naming the attributes that justified the result. Agents hold no store credentials and have no write tools.
- Retrieved text is data. It cannot change permissions or trigger an action.
- Postgres is authoritative for prose. Neo4j is a derived index. The worker commits Postgres, then writes Neo4j. A failed graph write leaves the message unacknowledged. A retry whose content hash matches still writes the graph. Upserts are idempotent on source id, object id, chunk index, chunker version, and embedding model.
- Deletes are hard deletes. The connector removes the S3 object and verifies it before emitting `delete`. The worker tombstones, deletes chunks and graph nodes, and verifies both. A reconciliation sweep catches missed deletes.
- Do not ingest `salary`, `compensation`, `ssn`, or `national_id`.
- Conventional code owns permissions, synchronization, deletion, validation, and state transitions. Use a model for language understanding and for constrained extraction from prose. Headline graph answers use edges extracted in code from structured records.

## Source contracts

Every connector needs a valid YAML file in `infrastructure/config/sources/`. Required: `owner`, `freshness_tier`, and at least one `acl_mapping` rule. `load_contracts` is the registration gate; call it from `shared.contracts.validate` and refuse to start a connector when it raises `ContractLoadError`.

After changing `SourceContract`, regenerate and commit the schema:

```bash
uv run python -m shared.contracts.export_schema
```

`make contracts-check` fails if the committed schema differs.

## Scope

Stay on the POC: four sources, the two demo questions, hybrid search, a small ontology, MCP plus REST, workers, and a queue. Do not add departments, a reranker, Kafka, community-summary GraphRAG, or fuzzy entity resolution unless a measured gap is written into the relevant spike. Label new work as built or as production path.

## Docs and diagrams

Write in plain sentences. No emojis. Architecture diagrams stay drawio files plus a PNG export in `_docs/99--dagrams/`. Match the files already there: black canvas, Courier New labels, white arrows, and blue containers (`#1061B0` on the data-flow boxes, the C4 stencil blue on the context diagrams). Observability diagrams are mermaid in the doc. Link files with relative paths.

## Secrets and local files

Read `OPENROUTER_API_KEY` from the environment. Model names belong in config. The default embedder is `fastembed` (multilingual-e5-large, baked into the images). Offline runs and tests use `EMBEDDER=hash` and the deterministic extractor, and tests must pass without a key or a model download. Never commit API keys, `.env` files, `.venv`, `__pycache__`, `.ruff_cache`, or `*.egg-info`.

## Git

Match existing messages: `docs: ...`, `docs(spike): ...`, `docs(brief): ...`, `chore: ...`. Create a commit only when the user asks.

## Tests

Add a negative case for every new validation rule. Access-boundary tests (two principals, one pool, empty results for the unauthorized principal) are security tests and stay next to the retrieval tests. The golden set covers a known chunk, a question with no answer, conflicting sources, and an access boundary.
