# Metrics

Alloy scrapes `gateway-api:8000`, `gateway-mcp:8001`, `connector-hr:9100`, `connector-engineering:9102`, `connector-sales:9103`, `connector-product:9104`, `ingestion-worker:9101`, and `webhook-receiver:8090`, and remote-writes to Prometheus. The four harnesses are scraped too. Labels are the ones named below. Object ids, principals, native ACL lists, and chunk text are not labels.

`kbs_worker_up{worker="connector"}` stays 1 after the connector loads its contracts, including while it waits for Postgres or the bucket. `kbs_worker_up{worker="ingestion"}` stays 1 after the ingestion worker loads its contracts.

The overview SLO row is four stats. HR snapshot lag p95 turns red at 30 seconds. Search p95 turns red at 0.25 seconds. Dead-letter depth turns red when any message is visible. Delete verification turns red on any failure in the selected range. Sales, product, and engineering stay on the per-source panels, where yellow is 30 seconds and red is 300 seconds.

## Connector

Every connector exposes these through `connector-core`. Every series also has `source_id`, except `kbs_connector_dependency_up` and `kbs_worker_up`.

| Metric | Extra labels | Meaning |
| ------ | ------------ | ------- |
| `kbs_connector_sync_runs_total` | `result` (`ok` or `error`) | Finished sync cycles. |
| `kbs_connector_sync_duration_seconds` | | Cycle duration. |
| `kbs_connector_last_success_timestamp_seconds` | | Unix time of the last cycle that advanced the watermark. |
| `kbs_connector_watermark_lag_seconds` | | Now minus the stored watermark. |
| `kbs_snapshot_object_lag_seconds` | | Snapshot time minus the source `updated_at`. The p95 is the freshness signal, charted per `source_id`. Yellow at 30 seconds is the HR contract. Red at 300 seconds is the sales, product, and engineering contract. |
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

Logs are JSON on stdout. `source_id` is a Loki label. A snapshot line names the object type, version, and byte count. It does not contain the body. The connector panel is `{service=~"connector-.*"}`, which covers `connector-hr`, `connector-sales`, `connector-product`, and `connector-engineering`.

A cursor watermark is not a timestamp. `kbs_connector_watermark_lag_seconds` stays unset for that cycle.

## Product hooks

The receiver exposes `kbs_webhook_events_total` on `webhook-receiver:8090/metrics`. The product connector exposes the queue depth on `connector-product:9104`.

| Metric | Labels | Meaning |
| ------ | ------ | ------- |
| `kbs_webhook_events_total` | `result` (`ok`, `invalid`, or `error`) | Notices accepted, rejected as an invalid body, or refused because SQS did not take them. The dashboard uses `increase` over the selected range. The raw counter is the total since process start. |
| `kbs_hook_queue_depth` | `queue` | Visible messages on `kbs-product-hooks.fifo` or `kbs-product-hooks-dlq.fifo`. This is a gauge. The live queue can hold work. Only the dead-letter queue turns the panel red. |

## Ingestion worker

Exposed on `ingestion-worker:9101`. Queue depth is `GetQueueAttributes` (`ApproximateNumberOfMessages`), not CloudWatch.

| Metric | Labels | Meaning |
| ------ | ------ | ------- |
| `kbs_queues_reachable` | | 1 when the P0, P1, and P2 queues all answer. |
| `kbs_ingest_events_total` | `source_id`, `event_type`, `result` (`ok`, `retry`, `invalid`) | Events the worker finished or refused. |
| `kbs_ingest_event_lag_seconds` | `source_id` | Now minus `occurred_at`, recorded when the event completes. Not the age of the oldest message. |
| `kbs_queue_depth` | `queue` | Visible messages on `kbs-p0.fifo`, `kbs-p1.fifo`, or `kbs-p2.fifo`. |
| `kbs_dlq_depth` | `queue` | Visible messages on that tier's dead-letter queue. This is a gauge, charted per queue. It is not wrapped in `increase`. |
| `kbs_embed_calls_total` | `model` | Embedding calls. One increment per text, not per object. |
| `kbs_embed_skipped_unchanged_total` | `source_id` | Objects whose content hash already matched, so nothing was embedded. The graph write still runs. |
| `kbs_store_write_seconds` | `store` (`postgres` or `neo4j`), `op` (`upsert`, `delete`, or `acl`) | Time spent on one store write. |
| `kbs_store_errors_total` | `store`, `op` | Store write failures. The dashboard uses `increase` over the selected range, by store and operation. |
| `kbs_index_objects` | `source_id` | Distinct objects that have at least one chunk. |
| `kbs_index_drift` | `source_id` | Indexed object count minus `source_objects` count. |
| `kbs_worker_dependency_up` | `dependency` (`postgres`, `neo4j`, `s3`, `sqs`) | 1 when that dependency answered. |

`kbs_delete_verification_failures_total` is the same series as the connector, with `store` `postgres` or `neo4j`. The dashboard uses `increase` over the selected range, by source and store. The raw counter is the total since process start.

## HTTP

The API, the MCP server, and the webhook receiver expose these. `job` from the scrape names the process. A principal and the raw URL are not labels. `route` is a path template the process registered, or `unmatched` when the path matches none of them. `/health` and `/metrics` are templates. The dashboard leaves them out of the rate so scrapes do not hide real traffic.

| Metric | Labels | Meaning |
| ------ | ------ | ------- |
| `kbs_http_requests_total` | `route`, `method`, `status` | Finished HTTP requests. `status` is the response code. |
| `kbs_http_request_seconds` | `route`, `method` | Request duration. The 0.25 bucket is one second quarter, not by itself the search target. |

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
