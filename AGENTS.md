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
| `shared/contracts/` | Source-contract model, generated JSON Schema, validator, tests. |
| `infrastructure/config/sources/` | One YAML contract per source. |
| `backend/` | API, MCP, service layer, workers, messaging. |
| `database/` | Postgres and Neo4j schema. |
| `demo-harness/` | HR, Sales, Product, Engineering mocks. |
| `notebooks/` | Jupyter seed and inspect notebooks for the HR harness. |
| `infrastructure/orchestration/` | One Compose file per trust area, plus `compose.yaml` which includes them. |
| `infrastructure/provisioning/terraform/` | Queues, dead-letter queues, and the snapshot bucket on floci. |
| `observability/` | Alloy, Loki, Prometheus, one dashboard, and metric definitions. |
| `tools/` | Seed, mutation, and evaluation scripts. |

## Stack and commands

Python 3.12, FastAPI, Pydantic v2, pytest, Ruff (line length 100, first-party package `shared`). [uv](https://docs.astral.sh/uv/) is the package manager and the way to run Python. Run commands from the repository root.

```bash
uv sync
make test
make compose-check
uv run ruff check shared backend
```

Add a runtime dependency with `uv add` and a dev dependency with `uv add --dev`. Image-only libraries go in the `backend`, `harness`, or `notebooks` dependency group (`uv add --group backend ...`). Commit `uv.lock`. Do not use `pip` or `python -m venv`. Do not add dependencies outside `pyproject.toml`.

`make up` starts the stub stack. `make notebooks` starts Jupyter. `make seed`, `make demo`, and `make eval` are not written yet. Add a target only when the command it wraps exists, and update the README quickstart in that same change. HR bulk load is the notebook, which calls `POST /admin/seed`.

Dockerfiles install with `uv sync --frozen`. Do not publish Postgres or Neo4j ports. One Compose network per trust boundary (`kbs-data` is internal). Service names are globally unique. New services need a healthcheck and a Prometheus target. Jupyter is the exception: it has a healthcheck and no `/metrics` endpoint, and Alloy does not scrape it.

## Design rules

- Authorization happens in the data layer: coarse pre-filter in SQL and Cypher, fine-grained post-check, Postgres row-level security, and an audit row naming the attributes that justified the result. Agents hold no store credentials and have no write tools.
- Retrieved text is data. It cannot change permissions or trigger an action.
- Postgres is authoritative for prose. Neo4j is a derived index. A failed graph write retries with the chunk write. Upserts are idempotent on source id plus version or content hash.
- Deletes are hard deletes of chunks, graph nodes, and the S3 object, followed by a verification query. A reconciliation sweep catches missed deletes.
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
