# ADR 0003: Embedding and chunk versioning

Status: accepted for the POC.

## Context

Retrieval quality depends on how a document is split and which model embeds it. A chunk written by an old splitter and queried with a new model is a silent regression. Spike 01 requires format-aware chunking. Spike 02 stores a contextual header and runs hybrid search. Migrations have to be possible later without a flag day that the POC does not need to execute.

## Decision

Every chunk row stores:

| Column | Meaning |
| ------ | ------- |
| `chunker_version` | Identifier of the splitter and its parameters, for example `markdown-headings-v1`. |
| `embedding_model` | Model name that produced the vector, for example the OpenRouter model or `fastembed-bge-small-v1`. |
| `section_path` | Heading or section path the chunk was cut from. |
| `content_hash` | Hash of the normalized body. Unchanged hash skips re-embedding (spike 04). |

Chunkers are a registry keyed by source format, not one token window:

- Markdown: split on headings, keep the heading path, prepend a contextual header (document title plus path).
- PDF: split on detected sections, same header rule.
- Structured records: one chunk per row, plus a stable textual rendering of the columns that are in the source contract.

The active `chunker_version` and `embedding_model` are configuration. A query filters to the active embedding model so mixed vectors are never ranked together. Changing either value is a rebuild: enqueue all objects at P2, write new chunks alongside the old version, switch the active version, then delete the old one. The POC ships a single version and the rebuild command. It does not run a migration in the demo.

Lexical search does not depend on the embedding model and stays valid across an embedding rebuild.

## Why

Version columns make the index explainable. When an answer is wrong, the chunk says which splitter and which model produced it. Format-specific splitters avoid cutting a policy in the middle of a section, which is the common way a parental-leave answer loses the country it applies to. The contextual header keeps "this section" interpretable when the chunk is retrieved alone.

Skipping a cross-encoder and a second embedding model in the POC follows spike 02: add complexity when the golden set shows a gap.

## Alternatives rejected

| Alternative | Why it was rejected for the POC |
| ----------- | ------------------------------- |
| One fixed-size token splitter for every format | Cuts tables, headings, and list items. Looks like a model failure and is a chunking failure. |
| Unversioned index, drop and recreate on any change | Acceptable for a notebook. Unacceptable once the demo must keep serving during a re-embed, and it destroys the audit trail of what produced a citation. |
| BM25 in a separate engine | Postgres full-text search is enough for identifier and policy-name matches at this corpus size, and it sits under the same row-level security as the vectors. |
| Embed ACL attributes into the vector | Permissions change more often than body text. They belong in columns so an ACL event does not re-embed (spike 04). |

## Consequences

- Local fallback embeddings and OpenRouter embeddings must not be mixed in one active version. Config selects one active model per run. Switching models is a rebuild.
- Chunk text stored in Postgres is the citation source. The vector is not decoded back into text.
- The golden set (spike 02) is the gate for changing `chunker_version`.

## Production path

Dual-write old and new versions during a model migration, compare recall@k on the golden set, publish a retirement date, then drop the old version. Keep chunker and embedding versions in the public changelog of `/v1`.
