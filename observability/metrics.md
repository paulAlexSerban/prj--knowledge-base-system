# Metrics

Alloy scrapes `connector-hr:9100` and `ingestion-worker:9101` and remote-writes to Prometheus. Labels are the ones named below. Object ids, principals, native ACL lists, and chunk text are not labels.

`kbs_worker_up{worker="connector"}` stays 1 after the connector loads its contracts, including while it waits for Postgres or the bucket. `kbs_worker_up{worker="ingestion"}` stays 1 after the ingestion worker loads its contracts.

## Connector

The HR connector exposes these. Every series also has `source_id`, except `kbs_connector_dependency_up` and `kbs_worker_up`.

| Metric | Extra labels | Meaning |
| ------ | ------------ | ------- |
| `kbs_connector_sync_runs_total` | `result` (`ok` or `error`) | Finished sync cycles. |
| `kbs_connector_sync_duration_seconds` | | Cycle duration. |
| `kbs_connector_last_success_timestamp_seconds` | | Unix time of the last cycle that advanced the watermark. |
| `kbs_connector_watermark_lag_seconds` | | Now minus the stored watermark. |
| `kbs_snapshot_object_lag_seconds` | | Snapshot time minus the source `updated_at`. The p95 is the freshness signal. The dashboard turns red above 30 seconds. |
| `kbs_snapshots_written_total` | `object_type` | Objects written to S3. |
| `kbs_snapshots_skipped_unchanged_total` | | Version, ACL, and hash already matched. |
| `kbs_snapshots_deleted_total` | | Keys removed after the source deleted the object. |
| `kbs_snapshot_bytes_total` | | Bytes written. |
| `kbs_snapshot_errors_total` | `stage` (`list`, `fetch`, `contract`, `s3_put`, `state`, `delete`, `enqueue`) | Failures. A contract error is an object that carried an excluded field. `enqueue` is a publish that failed after the snapshot write. |
| `kbs_delete_verification_failures_total` | `store` | The object was still there after a delete. The connector uses `store="s3"`. The worker uses `postgres` and `neo4j`. |
| `kbs_source_objects` | | Ids the source listed. |
| `kbs_snapshot_objects` | | Keys in the bucket for that source. |
| `kbs_reconcile_drift` | | Snapshot count minus source count. Zero is healthy. |
| `kbs_connector_dependency_up` | `dependency` (`postgres`, `s3`, `harness`, `sqs`) | 1 when that dependency answered. |
| `kbs_events_published_total` | `event_type` | Change events sent to a tier queue. |

Logs are JSON on stdout. `source_id` is a Loki label. A snapshot line names the object type, version, and byte count. It does not contain the body. `{service="connector-hr"}` is the connector panel.

## Ingestion worker

Exposed on `ingestion-worker:9101`. Queue depth is `GetQueueAttributes` (`ApproximateNumberOfMessages`), not CloudWatch.

| Metric | Labels | Meaning |
| ------ | ------ | ------- |
| `kbs_queues_reachable` | | 1 when the P0, P1, and P2 queues all answer. |
| `kbs_ingest_events_total` | `source_id`, `event_type`, `result` (`ok`, `retry`, `invalid`) | Events the worker finished or refused. |
| `kbs_ingest_event_lag_seconds` | `source_id` | Now minus `occurred_at`, recorded when the event completes. Not the age of the oldest message. |
| `kbs_queue_depth` | `queue` | Visible messages on `kbs-p0.fifo`, `kbs-p1.fifo`, or `kbs-p2.fifo`. |
| `kbs_dlq_depth` | `queue` | Visible messages on that tier's dead-letter queue. |
| `kbs_embed_calls_total` | `model` | Embedding calls. One increment per text, not per object. |
| `kbs_embed_skipped_unchanged_total` | `source_id` | Objects whose content hash already matched, so nothing was embedded. The graph write still runs. |
| `kbs_store_write_seconds` | `store` (`postgres` or `neo4j`), `op` (`upsert`, `delete`, or `acl`) | Time spent on one store write. |
| `kbs_store_errors_total` | `store`, `op` | Store write failures. |
| `kbs_index_objects` | `source_id` | Distinct objects that have at least one chunk. |
| `kbs_index_drift` | `source_id` | Indexed object count minus `source_objects` count. |
| `kbs_worker_dependency_up` | `dependency` (`postgres`, `neo4j`, `s3`, `sqs`) | 1 when that dependency answered. |

`kbs_delete_verification_failures_total` is the same series as the connector, with `store` `postgres` or `neo4j`.

## Retrieval

The API exposes these on `gateway-api:8000/metrics`. The MCP server exposes the same names on `gateway-mcp:8001/metrics`. `surface` is `rest` or `mcp`. A principal, an object id, and chunk text are not labels. `permission_group` is the caller's highest-privilege group (`hr_admins`, then `employees`), or `none`.

| Metric | Labels | Meaning |
| ------ | ------ | ------- |
| `kbs_retrieval_seconds` | `tool`, `surface` | Time in `search`, `get_document`, or `find_related`. The 0.25 bucket is the 250 ms target. |
| `kbs_retrieval_requests_total` | `tool`, `surface`, `result` (`ok` or `empty`) | Finished retrieval calls. |
| `kbs_retrieval_empty_total` | `source_id`, `permission_group` | Searches that returned no chunk. `source_id` is `none` when nothing matched. |
| `kbs_retrieval_total` | `source_id`, `permission_group` | Searches, including ones that returned chunks. The empty-result rate is the empty counter divided by this one. |
| `kbs_auth_failures_total` | `reason` (`missing`, `expired`, `invalid`, `missing_claim`, `clearance`, `secret`) | Rejected bearer tokens. |
| `kbs_acl_recheck_seconds` | `source_id` | Time spent asking HR for the current policy ACL. |
| `kbs_acl_recheck_failures_total` | `source_id` | A re-check that timed out or returned nothing usable. The search then returns no chunks. |

A retrieval log line has `request_id`, `principal`, `decision` (`allow` or `deny`), `tool`, and `count`. It has no snippet. Grafana's Loki datasource links `request_id` to the other lines for that request. The deny panel is `{service=~".+"} | json | decision="deny"`.

`hash-v1` only returns a chunk that also matched full text. A vector-only neighbor is not an answer, so a query with no shared terms is an empty result.
