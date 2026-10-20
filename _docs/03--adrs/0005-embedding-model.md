# ADR 0005: Local multilingual-e5-large, with a hash embedder for tests

Status: accepted. Replaces the earlier bge-small choice. The column change is migration `005-embedding-1024.sql`.

## Context

Retrieval needs a dense vector. The demo and the tests also have to run with no API key and no model download at test time. A chunk embedded by one model must not be ranked against a query from another. The company is global, and an English-only embedder leaves non-English policies as an open risk. bge-small was the first running model: 384 dimensions, fast on CPU, and English only.

## Decision

The running embedder is `fastembed-e5-large-v1`: `intfloat/multilingual-e5-large` through fastembed, 1024 dimensions, ONNX on CPU. fastembed does not add the e5 prefixes, so this code does. A stored passage is prefixed with `passage: `. A query is prefixed with `query: `. Compose and `.env.example` set `EMBEDDER=fastembed`. The worker, the API, and the MCP server download the weights at image build (`FASTEMBED_CACHE_PATH=/opt/fastembed`), so a demo run does not fetch them. The ONNX weights are about 2.2 GB, and each of those three images carries its own copy. A shared base image was not added. `hash-v1` (`EMBEDDER=hash`) is deterministic, 1024 dimensions, and not semantic. The library uses it when `EMBEDDER` is unset. Tests and offline runs use it. Lexical search still finds exact terms. A query filters to the active model, so the two are never ranked together.

Postgres full-text stays `english`. The multilingual model covers the dense side only.

1024 is under the pgvector HNSW limit of 2000 for the `vector` type, so the index stays HNSW cosine. A database created at `vector(384)` is emptied and altered by migration 005. `make reindex` or `make seed` fills it again. A column that is already `vector(1024)` is left alone, including its rows.

## Alternatives rejected

- `BAAI/bge-small-en-v1.5`. The previous model. 384 dimensions, about 130 MB, and English only. It was fast enough for the 250 ms budget on this laptop. It does not cover the other languages the company operates in.
- `intfloat/multilingual-e5-small`. 384 dimensions, so the column would not have changed. Weaker retrieval than e5-large, and the assignment asked for the model the design should run, not the smallest one that fits.
- `BAAI/bge-base-en-v1.5` and `bge-large-en-v1.5`. English only. Base is 768 dimensions. Large is 1024 and still English.
- `mixedbread-ai/mxbai-embed-large-v1`. Strong English retrieval, 1024 dimensions, and not multilingual.
- `all-MiniLM-L6-v2`. Weaker English model of the small size. Not a step up from bge-small.
- OpenRouter embeddings. A network and a key inside ingestion. Tests would no longer run offline.

## Consequences

- Switching models is a rebuild ([ADR 0006](0006-chunk-versioning.md)), not a mixed index. The dimension change deletes the old vectors.
- `hash-v1` nearest neighbours are dropped at search time. Nearest-neighbour search always returns something, and a hash vector is not a meaning.
- Image builds download about 2.2 GB, three times. CPU query embedding is slower than bge-small. `make eval` on this laptop scored p95 at 372 ms, which misses the 250 ms retrieval budget. The budget is not raised. The regression guard in the golden file stays 500 ms. A shared embedding service, or a smaller model, is the production-path response if that budget is a hard requirement.
- `VECTOR_MAX_DISTANCE` is 0.20. On the seeded chunks, paraphrase distances sat around 0.11 to 0.18, and "xylophone quantum payroll" sat at about 0.21 against a learning-budget chunk. One query embed on this laptop was about 150 to 300 ms after the model was loaded. That leaves little of the 250 ms budget once the HR ACL re-check is added. The floor has to be re-measured on another corpus.
- Policies in another language can match on the dense side. A lexical-only query in another language still depends on the English full-text configuration.
