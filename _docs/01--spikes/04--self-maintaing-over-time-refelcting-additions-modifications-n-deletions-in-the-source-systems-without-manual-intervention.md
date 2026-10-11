# Self-Maintaing Over Time Refelcting Additions, Modifications, and Deletions in the Source Systems Without Manual Intervention

## Context
- the hard part in building an AI Knowledge Base from a heterogeneous collection of source systems is keeping it up to date as the source systems change over time
- the knowledge base needs to accurately reflect a constantly moving set of source systems - one or more documents get created, edited, archived, and deleted every hour, where access depends on who's asking and where freshness means something different for a legal policy than for Slack message

## Promblem
- enterprise knwoeldge is distributed across systems that were never designed to talk to eahc other
- each system used in the enterprise has it's own API shape, its own rate limits, it's own notion of "what changed" and it's own wuthentication model
- some systems expose webhooks, others only support polling, some have robust change feeds, other require you to diff full exports to ficure out what's is different
- ownership and access heterogeny - if you knowledge base flattens departamental access (Sharepoint), sccount team scope (Salesforce), named group of edditors restrictions (Confluence) into an undifferentiated vector index - you built a data leak generator - the system has to cary metadata though every satge of teh pipeline = ingestion, chunking, embeding, storage, and retrival - and enforce it at query time
- freshness - not all content decays at the same rate - a naive full-reindex-on-schedule approach either wastes enormous compute re-embeding unchanged content, or leaves genuinly stale information to the index becasue the schedule was not tight enough - freshness requires per-source, sometimes per-document, policies and mechanisms to detect and propagate deletions which is the piece most systems get wrong first

## Possible solution overview
- self-maininaing knowledge base with five layers
  - source connectors
  - change detection/capture layer
  - normalizaition and enrichment pipeline
  - storage layer (tupicaly a vector store plus a metadata store)
  - retrival layer which applies access control and freshness filtering at query time
- the critical design decision is that these layer communicate though events, not though periodic batch jobs that re-read everything - treating ingestion as a stream of change events (creates, updates, deletes) rather than a series of full snapshots is what allows the system to  stay current without re-processing the entire corpus on every run
- shape:
  - a connector per source that knwos how to extract both content and extract signals
  - a durable event log (Kafka or managed equivalent like AWS kinesis or Confluenct Cloud, or a simple queue like SQS for lower-volume sources) that decouples extraction from processing
  - a worker pool that chunks, embeds, and writes to storage
  - metadatalayer - frequently a relational database - that tracks document lineage, source IDs, content hashes, access control lists, and lats synced timestamps - the metadatlayer is what makes the system idempotent and debugable - when someghing looks wronh in retrival, you can trace a chunk back t its source document, its last sync time, and the exact revision it was derived from