# Agent guide

This repository is a POC knowledge gateway: ingest four department sources, keep a hybrid index and a small graph current, and serve them through one API and one MCP server under the caller's permissions.

`_docs/` is the source of truth for design. When a change alters a decision, update the matching spike or ADR in the same change. Do not invent a second design in code comments.

## Layout

| Path | Owns |
| ---- | ---- |
| `_docs/00--project-brief/` | Scope, assumptions, demo script, C4 diagrams (drawio and PNG). |
| `_docs/01--spikes/` | Ingestion, knowledge representation, access, synchronization. |
| `_docs/02--architecture/` | Architecture write-up. Fill the outline; do not add sections the assignment did not ask for. |
| `_docs/03--adrs/` | Storage, observability, embedding and chunk versioning. |
| `shared/` | Source contracts, the start-up registry gate, and JSON logging. Every process depends on it. |
| `infrastructure/config/sources/` | One YAML contract per source. |
| `backend/apis/knowledge-api/` | REST gateway. Own image. |
| `backend/apis/mcp-server/` | MCP gateway. Own image. |
| `backend/apis/webhook-receiver/` | Product webhook stub. Own image. Production path is API Gateway plus Lambda; that function does not exist yet. |
| `backend/workers/ingestion-worker/` | Queue consumer. Own image. Writes chunks and the graph through `knowledge-store`. |
| `backend/connectors/hr/` | HR connector process (`connector-hr`). Own image. |
| `backend/libs/connector-core/` | Connector interface, snapshot sync, S3 store, Postgres sync state, and the SQS publisher. Library, no image. |
| `backend/libs/ingest-contract/` | Change-event model, queue names, and the snapshot hash. Library, no image. |
| `backend/libs/knowledge-store/` | Chunkers, the hash embedder, Postgres chunks, ACL, and the Neo4j writer. Library, no image. |
| `backend/tests/integration/` | Tests that cross a deployable boundary. |
| `backend/tests/e2e/` | Opt-in live-stack test. `make e2e`. |
| `database/` | Postgres migrations (sync state, chunks, full-text, vectors, ACL, row-level security) and Neo4j constraints. |
| `demo-harness/` | One image per source. HR is built. Sales, Product, and Engineering are stubs on `harness-common`. |
| `notebooks/` | Jupyter notebooks that seed, inspect, and edit the HR harness. |
| `infrastructure/orchestration/` | One Compose file per trust area, plus `compose.yaml` which includes them. |
| `infrastructure/provisioning/terraform/` | Queues, dead-letter queues, and the snapshot bucket on floci. |
| `observability/` | Alloy, Loki, Prometheus, one dashboard, and metric definitions. |
| `tools/` | `scripts/dlq_replay.py`. Seed, mutation, and evaluation scripts are not written. |

## Stack and commands

Python 3.12, FastAPI, Pydantic v2, pytest, Ruff (line length 100). [uv](https://docs.astral.sh/uv/) is the package manager. The repository is a uv workspace: each deployable and library has its own `pyproject.toml`, and `uv.lock` stays at the root. Run commands from the repository root.

```bash
uv sync
make test
make compose-check
uv run ruff check shared backend demo-harness
```

Add a dependency to the package that imports it: `uv add --package connector-hr <name>`. Add a test-only dependency with `uv add --group dev`. The notebooks group is `uv add --group notebooks`. Commit `uv.lock`. Do not use `pip` or `python -m venv`. Do not add dependencies outside a `pyproject.toml`.

`make up` starts the stack, then applies Terraform on the host. `make notebooks` starts Jupyter. `make tools` starts pgAdmin and the SQLite viewers. `make e2e` runs the live HR snapshot cycle and is not part of `make test`. `make dlq-replay` re-sends dead-letter messages to the tier queues. floci must already be up. `make seed`, `make demo`, and `make eval` are not written yet. Add a target only when the command it wraps exists, and update the README quickstart in that same change. HR bulk load is the notebook, which calls `POST /admin/seed`.

Dockerfiles install one package with `uv sync --frozen --no-dev --no-default-groups --package <name>`. There is no Lambda directory until a function exists. Do not publish Postgres or Neo4j ports. One Compose network per trust boundary (`kbs-data` is internal). Service names are globally unique. New services need a healthcheck and a Prometheus target. Jupyter, pgAdmin, and the SQLite viewers are the exception: they have a healthcheck and no `/metrics` endpoint, and Alloy does not scrape them. Harness images stay root because they write a SQLite file on a volume. The other images run as `nobody`.

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

Write in plain sentences. No emojis. No mermaid in docs. C4 diagrams are drawio files plus a PNG export in `_docs/00--project-brief/`, using the same black canvas, Helvetica labels, solid white arrows, and blue `#1061B0` containers as the existing diagrams. Link files with relative paths.

## Secrets and local files

Read `OPENROUTER_API_KEY` from the environment. Model names belong in config. Offline runs use the local embedder and the deterministic extractor, and tests must pass without a key. Never commit API keys, `.env` files, `.venv`, `__pycache__`, `.ruff_cache`, or `*.egg-info`.

## Git

Match existing messages: `docs: ...`, `docs(spike): ...`, `docs(brief): ...`, `chore: ...`. Create a commit only when the user asks.

## Tests

Add a negative case for every new validation rule. Access-boundary tests (two principals, one pool, empty results for the unauthorized principal) are security tests and stay next to the retrieval tests. The golden set covers a known chunk, a question with no answer, conflicting sources, and an access boundary.
