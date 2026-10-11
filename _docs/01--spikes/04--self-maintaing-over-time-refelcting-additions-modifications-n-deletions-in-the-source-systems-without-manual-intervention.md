# Spike 04: Staying current as sources change

Status: decision for the POC. Production scale is called out and is not built.

## Problem

The hard part of a knowledge base over heterogeneous sources is keeping it aligned after the first load. Sources were not designed to talk to each other. Each has its own API shape, rate limit, authentication, and notion of "what changed". Some push webhooks, some offer a cursor, some offer `updated_since`, and some require a full listing diff. Deletes are the change most indexes miss first: the source forgets the object, and the index keeps answering from it.

Freshness is also not uniform. Re-embedding the whole corpus on a schedule either spends embedding cost on unchanged documents or leaves stale ones in place because the schedule was too slow. Access metadata has to ride the same pipeline as content. A pipeline that updates text and forgets that a group's permission was revoked is the leak described in spike 01.

At the assumed production volume, 100,000 changes per day is about 1.2 events per second on average. The design constraint is correctness under retries, delete propagation, and embedding cost, not bus throughput.

## Decision (MVP)

Treat ingestion as a stream of change events (create, update, delete, acl_change). Layers communicate through the queue. A periodic job still exists, and its job is reconciliation, not re-reading every body.

### Pipeline

1. Connector worker runs on the cadence in the source contract. P0 (HR) is an in-process loop, because EventBridge Scheduler cannot fire faster than once a minute. Scheduler, when wired, sends P1/P2 poll ticks and the reconciliation sweep to a queue. The Product harness calls the webhook receiver directly.
2. `list_changes` yields events. The worker writes the raw payload to S3 and enqueues a message.
3. Ingestion worker chunks, embeds, and upserts Postgres and Neo4j, or applies a tombstone.
4. Retrieval reads only the stores, plus the high-sensitivity ACL re-check from spike 03.

Message body: `source_id`, `object_id`, `event_type`, `version` or `content_hash`, `occurred_at`, and the S3 pointer. SQS on floci, with a FIFO message group per `source_id + object_id` so two updates to the same object stay ordered. A dead-letter queue receives messages that exhaust retries. Visibility timeout is longer than a single embed call so a crashed worker's message becomes visible again.

### Idempotency and cost

- Upsert key is `source_id + object_id + version` (or content hash when the source has no version).
- If the stored content hash matches, the worker updates sync metadata and skips the embedding call.
- Replaying a message after a crash does not duplicate chunks or edges.
- ACL-only changes update attributes in place and do not re-embed.

### Deletes

- A delete event writes a tombstone, then hard-deletes the chunks (pgvector and full-text), the graph nodes that this object owns, and the S3 object.
- A verification query after the delete asserts the id is gone from both stores. Failure retries the tombstone.
- A reconciliation sweep on a slower cadence lists ids from the source and diffs them against indexed ids. Ids present only in the index are tombstoned. This catches deletes that a cursor or webhook missed. It lists ids, not bodies, so it is cheap relative to a full re-embed.

### Priority

Three queues, matching the freshness tiers in the brief:

| Tier | Examples | Queue |
| ---- | -------- | ----- |
| P0 | HR policy body changes, any ACL change | Highest priority. Target under 30 seconds where the source can push or is polled that fast. |
| P1 | Sales deals, engineering epics, product specs | Target under 5 minutes. |
| P2 | Bulk backfill and the reconciliation sweep | Daily, or on demand via `make seed`. |

Workers drain P0 before P1 before P2. The POC runs one worker process with that order, which is enough at demo volume.

### What is measured

Per source: last successful sync, lag (now minus the newest unprocessed `occurred_at`), queue depth, dead-letter depth, embed calls skipped by hash, and delete verification failures. Definitions live with the dashboard in [ADR 0002](../03--adrs/0002-observability.md).

## Why

- Events decouple extract from embed. A slow embedding call does not block the connector, and a connector outage does not require re-reading sources that did not change.
- The metadata row (lineage, source id, content hash, ACL, last synced version) is what makes the system idempotent and debuggable. When a retrieval result looks wrong, the chunk points at the source object, the version, and the sync time.
- FIFO-by-object avoids applying an older update after a newer one without operating a log-structured broker.
- Reconciliation is the honest answer to "without manual intervention". Connectors miss events. The sweep is the backstop, and it is automated.

## Alternatives rejected

| Alternative | Why it was rejected for the POC |
| ----------- | ------------------------------- |
| Kafka or Kinesis as the POC bus | Replay and high fan-out are real production needs. At about 1.2 events per second they are not the POC's need. SQS with a dead-letter queue is operable inside floci and still shows workers, retries, and ordering. Kafka remains the production path if a second consumer needs independent replay. |
| Full reindex on a schedule | Re-embeds unchanged content or misses the freshness tier. Kept only as an explicit rebuild command when `chunker_version` or `embedding_model` changes (ADR 0003). |
| Change-data-capture from the source databases | The platform does not own those databases, and a mock SQLite file is not evidence that CDC against HubSpot or Drive is available. Capability flags (spike 01) meet the source where it is. |
| Soft-delete only | Hidden rows still surface through a missed filter, and the graph keeps traversable edges. Hard delete plus verification is the POC behavior. A legal hold, if one appears, belongs on the source contract as an exception. |
| One queue for every tier | A bulk backfill then blocks a policy update. Separate queues are the smallest fix. |

## Trade-offs and risks

- SQS FIFO throughput and exactly-once semantics are not what a log provides. The idempotent upsert is the real exactly-once. The queue is at-least-once delivery.
- Ordering is per object, not across objects. A deal update and the epic it depends on can land out of order. The graph upsert is commutative for the edges in this ontology (`DEPENDS_ON` from deal to epic is written when either side is seen, and dropped when either side is tombstoned).
- Reconciliation lag means a missed delete can live until the next sweep. P0 sources run the sweep more often. The dashboard shows indexed-minus-source id drift so the gap is visible.
- Two-store writes can fail halfway. The worker treats Postgres and Neo4j as one unit: on partial failure it retries the whole message. The upsert key makes the retry safe. A poison message lands in the dead-letter queue and pages the sync-lag metric; it does not block the FIFO group forever, because the message group is per object and the poison message is removed from the main queue on dead-letter.
- A local Terraform apply created `kbs-p0.fifo`, `kbs-p1.fifo`, `kbs-p2.fifo`, and a FIFO dead-letter queue for each. A send and receive on `kbs-p0.fifo` succeeded. That checks the API shape. It is not a test of ordering under load, and it is not a test of production SQS.

## Production path

- Move the bus to Kinesis or Kafka when a second independent consumer (analytics, audit export) needs replay without disturbing ingestion.
- Scale ingestion workers horizontally. FIFO message groups keep per-object order under multiple consumers.
- Per-source rate-limit and backoff tokens, because real APIs will throttle `list_changes` long before the queue does.
- A rebuild workflow for embedding-model migrations that dual-writes and cuts over, rather than a flag day.

## Open TODOs

- Event schema in `backend/messaging`, shared with the harness webhook payload.
- Dead-letter replay command in `tools/`, used by demo step 5.
- Reconciliation query that diffs source ids against indexed ids and emits tombstones.
