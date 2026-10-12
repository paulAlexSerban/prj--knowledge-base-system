# Spike 04: Staying current as sources change

Status: decision for the POC. Production scale is called out and is not built.

## Problem

The hard part of a knowledge base over heterogeneous sources is keeping it aligned after the first load. Sources were not designed to talk to each other. Each has its own API shape, rate limit, authentication, and notion of "what changed". Some push webhooks, some offer a cursor, some offer `updated_since`, and some require a full listing diff. Deletes are the change most indexes miss first: the source forgets the object, and the index keeps answering from it.

Freshness is also not uniform. Re-embedding the whole corpus on a schedule either spends embedding cost on unchanged documents or leaves stale ones in place because the schedule was too slow. Access metadata has to ride the same pipeline as content. A pipeline that updates text and forgets that a group's permission was revoked is the leak described in spike 01.

At the assumed production volume, 100,000 changes per day is about 1.2 events per second on average. The design constraint is correctness under retries, delete propagation, and embedding cost, not bus throughput.

## Decision (MVP)

Treat ingestion as a stream of change events (create, update, delete, acl_change). Layers communicate through the queue. A periodic job still exists, and its job is reconciliation, not re-reading every body.

### Pipeline

1. Connector worker runs on the cadence in the source contract. P0 (HR) is an in-process loop, because EventBridge Scheduler cannot fire faster than once a minute. Scheduler, when wired, sends P1/P2 poll ticks and the reconciliation sweep to a queue. P1 polls in this POC are also in-process, every 60 seconds. The Product harness posts a pointer at the webhook receiver. The receiver enqueues it on `kbs-product-hooks`. The product connector fetches the spec, writes the snapshot, and emits the change event on the P1 queue.
2. `list_changes` yields events. The HR connector writes the raw payload to S3, enqueues the change event, then upserts `kbs.source_objects`. The enqueue is before the state upsert, so a failed publish leaves the previous row in place and does not advance the watermark. The message `s3_pointer` is the key already stored, `raw/<source>/<type>/<id>.<ext>`. An ACL-only change (same version, different native ACL) is `acl_change`.
3. The ingestion worker chunks, embeds, and upserts Postgres, then Neo4j, or applies a tombstone. It does not delete the S3 object. The connector does that, and checks the key is gone, before it emits `delete`.
4. Retrieval reads the stores, plus the HR ACL re-check from spike 03. REST and MCP search call that store path. They do not read SQLite or S3 for the answer.

Message body: `source_id`, `object_id`, `event_type`, `version`, `content_hash`, `occurred_at`, and the S3 pointer. A delete is valid without a pointer. SQS on floci, with a FIFO message group per `source_id:object_id` so two updates to the same object stay ordered. Visibility timeout on the three tier queues is 60 seconds. Terraform sets `maxReceiveCount` to 5. Whether floci applies that redrive was not observed, so the worker copies the message to the dead-letter queue itself when `ApproximateReceiveCount` is greater than 5.

### Idempotency and cost

- Chunk identity is `source_id`, `object_id`, `chunk_index`, `chunker_version`, and `embedding_model`.
- If the stored content hash matches, the worker skips the embedding call and still merges the graph. A retry after a Neo4j failure would otherwise never create the nodes.
- Replaying a message after a crash does not duplicate chunks or edges.
- A dead-letter replay does not reuse the original deduplication id. FIFO would drop that send for five minutes. The replay id is `{original}:replay:{message_id}`, in the same message group. `make dlq-replay` runs `tools/scripts/dlq_replay.py`.
- ACL-only changes update attributes in place and do not re-embed. The HR snapshot hash is sha256 of the raw bytes plus the sorted native ACL, so an ACL-only change still rewrites the S3 object. The harness does not bump the policy version for that change.

### Deletes

- The connector removes every S3 version, checks the key is gone, enqueues `delete` with the version and content hash from the row and no pointer, then deletes the `kbs.source_objects` row. A failed check or a failed enqueue keeps the row. HR and Engineering have no delete feed, so this runs from the `/ids` sweep. Sales puts `op: delete` on the cursor feed, and the same verified delete runs immediately. The sweep stays the backstop. A product delete pointer takes the same path. A pointer whose object is already gone is treated as a delete.
- The worker inserts `kbs.tombstones` (`source_id`, `object_id`, `version`, `content_hash`, `deleted_at`), hard-deletes the chunks and the graph nodes this object owns, then checks both counts are zero. There is no `documents` table. Lineage stays in `kbs.source_objects`. A failed verification increments `kbs_delete_verification_failures_total` with a `store` label (`s3` on the connector, `postgres` or `neo4j` on the worker) and the message retries.
- A replayed create with the same version and content hash does not resurrect a tombstoned object. A newer version clears the tombstone and writes again.
- Index reconciliation runs in the worker every `RECONCILE_INDEX_SECONDS` (default 60). Chunks with no `source_objects` row are deleted. A `source_objects` row with no chunk at the current version and embedding model is read back from S3 and processed. `kbs_index_drift` is the indexed object count minus the `source_objects` count. The sweep lists ids, not bodies.

### Priority

Three queues, matching the freshness tiers in the brief:

| Tier | Examples | Queue |
| ---- | -------- | ----- |
| P0 | HR policy body changes, any ACL change | Highest priority. Target under 30 seconds where the source can push or is polled that fast. |
| P1 | Sales deals, engineering epics, product specs | Target under 5 minutes. |
| P2 | Bulk backfill and the reconciliation sweep | Daily, or on demand. HR bulk load is `POST /admin/seed` from [notebooks/00-seed-hr.ipynb](../../notebooks/00-seed-hr.ipynb). `make seed` is not a target yet. |

Workers drain P0 before P1 before P2. The POC runs one worker process with that order, which is enough at demo volume. Index reconciliation is not a P2 message. The worker runs it on a timer, default 60 seconds. Nothing publishes to the P2 queue yet. Scheduler is not wired.

### What is measured

Per source: last successful sync, event lag, queue depth, dead-letter depth, embed calls skipped by hash, and delete verification failures. Event lag is `kbs_ingest_event_lag_seconds`, now minus that event's `occurred_at`, recorded when the worker finishes the message. SQS does not expose the age of the oldest message outside CloudWatch, so this is not oldest-message age. Queue depth comes from `GetQueueAttributes` in the worker. The publisher puts a W3C `traceparent` on the message, and the worker continues that trace, so one object change is one trace. Definitions live with the dashboards in [ADR 0002](../03--adrs/0002-observability.md).

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
- Two-store writes can fail halfway. Postgres commits first. If Neo4j fails, the message stays unacknowledged and the event retries. The chunk can exist without the node until that retry. The upsert key makes the retry safe, and a matching content hash still writes the graph. A poison message is copied to the dead-letter queue and shows up as dead-letter depth. It does not block the FIFO group forever, because the message group is per object and the poison message is removed from the main queue.
- A local Terraform apply created `kbs-p0.fifo`, `kbs-p1.fifo`, `kbs-p2.fifo`, and a FIFO dead-letter queue for each. A send and receive on `kbs-p0.fifo` succeeded. That checks the API shape. It is not a test of ordering under load, and it is not a test of production SQS.

## Production path

- Move the bus to Kinesis or Kafka when a second independent consumer (analytics, audit export) needs replay without disturbing ingestion.
- Scale ingestion workers horizontally. FIFO message groups keep per-object order under multiple consumers.
- Per-source rate-limit and backoff tokens, because real APIs will throttle `list_changes` long before the queue does.
- A rebuild workflow for embedding-model migrations that dual-writes and cuts over, rather than a flag day.

## Open TODOs

Built: the event schema in `backend/libs/ingest-contract`, the product hook pointer, `make dlq-replay`, and the index reconciliation sweep in the ingestion worker. A cursor watermark is stored as the cursor. Lag is not computed from it, because it is not a timestamp.

Still open: the production path for the receiver is API Gateway plus Lambda, and that function is not built. floci's FIFO redrive was not observed; the worker's own copy at receive count 5 is what the demo relies on. The live cycle replays that copy.
