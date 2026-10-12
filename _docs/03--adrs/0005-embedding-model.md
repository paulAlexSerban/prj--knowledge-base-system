# ADR 0005: Local bge-small, with a hash embedder for tests

Status: accepted for the POC. Decided while the ingestion worker was built.

## Context

Retrieval needs a dense vector. The demo and the tests also have to run with no API key and no model download. A chunk embedded by one model must not be ranked against a query from another.

## Decision

The running embedder is `fastembed-bge-small-v1`: `BAAI/bge-small-en-v1.5` through fastembed, 384 dimensions, ONNX on CPU. A passage is embedded as is. A query gets the bge instruction prefix. Compose and `.env.example` set `EMBEDDER=fastembed`. The worker, the API, and the MCP server download the model at image build (`FASTEMBED_CACHE_PATH=/opt/fastembed`), so a demo run does not fetch it. `hash-v1` (`EMBEDDER=hash`) is deterministic, 384 dimensions, and not semantic. The library uses it when `EMBEDDER` is unset. Tests and offline runs use it. Lexical search still finds exact terms. A query filters to the active model, so the two are never ranked together.

bge-small is English only. Policies in another language are a stated risk, not a solved problem.

## Alternatives rejected

- `intfloat/multilingual-e5-small`. Also 384 dimensions, so the column would not change. It is the swap if policies stop being English. Not measured on this corpus.
- `bge-base` and `bge-large`. 768 and 1024 dimensions force a column and index change. No measured gain at this corpus size.
- `all-MiniLM-L6-v2`. Not measured here. It was set aside as the weaker English model of this size, not as the result of a bake-off on the golden set.
- OpenRouter embeddings. A network and a key inside ingestion. Tests would no longer run offline.

## Consequences

- Switching models is a rebuild ([ADR 0006](0006-chunk-versioning.md)), not a mixed index.
- `hash-v1` nearest neighbours are dropped at search time. Nearest-neighbour search always returns something, and a hash vector is not a meaning.
