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
| `infrastructure/` | Compose, Terraform, floci. |
| `observability/` | One dashboard and its metric definitions. |
| `tools/` | Seed, mutation, and evaluation scripts. |

## Stack and commands

Python 3.12, FastAPI, Pydantic v2, pytest, Ruff (line length 100, first-party package `shared`). Run commands from the repository root. `make` exports `PYTHONPATH=.`

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make contracts-check
ruff check shared
```

Use `.venv`. Do not add dependencies outside `pyproject.toml`.

`make up`, `make seed`, `make demo`, `make test`, and `make eval` are planned and do not exist yet. Add a target only when the command it wraps exists, and update the README quickstart in that same change.

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
python -m shared.contracts.export_schema
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
