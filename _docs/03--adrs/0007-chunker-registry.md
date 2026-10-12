# ADR 0007: One chunker per format, not one token window

Status: accepted for the POC.

## Context

Spike 01 forbids a single fixed-size splitter. A parental-leave answer that loses its country is usually a chunk cut in the middle of a section, and it looks like a model failure.

## Decision

Chunkers are chosen by object type. `markdown-headings-v1` splits on headings, keeps the heading path, and prepends that path to the chunk. `pdf-text-v1` extracts text and splits on paragraphs. `record-v1` is one chunk per row, rendered from in-scope fields only. `salary`, `compensation`, `ssn`, and `national_id` are never written into the text or the graph properties. The HR PDF renderer drops markdown heading marks, so `pdf-text-v1` does not recover sections. That is a limit of the harness, not a hidden capability of the chunker.

## Alternatives rejected

- One token window for every format. It cuts headings, lists, and tables. Rejected in spike 01.
- A layout-aware PDF parser that reconstructs headings. Not built. The harness does not emit the marks such a parser would need, and the demo does not justify one.

## Consequences

- A PDF policy is a sequence of paragraphs. The country can still be in a neighbouring paragraph that was not retrieved. The contextual header on markdown does not apply to these files.
- Format-specific code is a small registry. A new format is a new chunker and a golden-set check, not a parameter on the old one.
