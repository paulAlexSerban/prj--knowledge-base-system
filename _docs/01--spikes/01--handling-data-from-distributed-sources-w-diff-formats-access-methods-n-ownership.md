# Handling data from distributed sources with different formats, access methods, and ownership

## Context
- dificulty stand in organizational complexity
- each department's data source is a product of decisions made independetly, years apart, by team with no shared data strategy (eg. support team in Zendesk as HTML with embeded screenshots, leagal team as PDFs in a DMS with strict retention rules, engineering team architecture as markdown in a git repos, product team roadmap as Notion workspace with hourly updats)
- no shared schema, no shared authentication mechanism, no consistent notion of what a document is or how to access it
- IMPORTANT: access control - if not addressed properly - instead of a convenience tool a data leak machine gets built
- ownership and lifecycle - each department produces data and updates data at different speeds, delete under different policies, and have different tollerances for staleness

## Problem
- how to access, ingest, embed and retrieve data from distributed sources with different formats, access methods, and ownership

## Lernings
- storage layer can combine semantic similarity with lexical matching - @TODO: adr on possible sotorage solution
- connector layer can benefit from Plug-in style architecture to extend to new sources without code changes - keeps orchestration logic (scheduling, retry handling, deduplication agains prior sync) separate from source specific extraction logic
- for access control - should follow NIST guidance on ABAC (Attribute-Based Access Control) - normalize each source's permissins into a set of attributes and principles (at ingestion time) and let retrival layer evaluate access as a policy decision at query time
  - extra: give clean audit trail - you can log which attributes justified showing a given chunk to a given user
- ownership - someone in each department needs to be accountable for accuracy of what gets indexed from their systems - ADR: establishing a lightweight contract per source: who owns it, how fresh it needs to be, what counts as sensitive
- common mistake - over-indexing - pulling in every available source becasue it is technically possible, without weighting the maintenance cost against the retrival value
  - every connector added is an ongoing commitment: API version change, OAuth token expire, rate limits shifts, source schema evolves, etc.
  - a knowledge base with 20 poorly maintained connectors is worsa than one with five well-maintained ones - broken or stale connectors silently degrade answer quality in ways that are hard to detect until a user notices something wrong
- common mistake - conflating chunking strategy across formats - each data source has it's different structure, and chunking them the same way degrades quality
- trade-off: freshness vs cost - real-time sync for every source sounds appealing, but re-embeding and re-indexing on every change is expensive at scale (compute, rate limits, etc.)
- observability from day one - @TODO: adr on possible observability stack
- version embedings and chunking logic together - @TODO: adr on possible versioning strategy
- favor normalized, source agnstic metadata schemas - it requires up-front translation logic per connector
- IMPORTANT: treat access control as a pipeline-stage concern, not a post-processing filter - resolve and attach permissions at ingestion time, and enforce them at query time via metadata filtering in the vector database
- IMPORTANT: build connectors behind common interface so source-specific complexity (auth, pagination, format quirks) stays isolated and does not leak into normalization or retrival logic
- IMPORTANT: use format aware, structure-respecting chunking rather than one fixed-size splitter aplied unifomly across all sources - each source has it's own structure and context, and chunking them the same way degrades quality
- IMPORTANT: establish per-source data contracts with clear ownership so freshness expectations and sensitivity level are explicit rahter than assumed or implicit
- monitor ingestion health (sync recency, document drift, failed syncs) as rigously as monitoring application uptime - stale data fails silently and erodes trust gradually