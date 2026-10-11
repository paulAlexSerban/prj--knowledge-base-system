# Connector metrics

The HR connector exposes these on `connector-hr:9100`. Alloy scrapes that target as job `connector-hr` and remote-writes to Prometheus. Labels are `source_id` plus the label named below. Object ids and principals are not labels.

| Metric | Labels | Meaning |
| ------ | ------ | ------- |
| `kbs_connector_sync_runs_total` | `result` (`ok` or `error`) | Finished sync cycles. |
| `kbs_connector_sync_duration_seconds` | | Cycle duration. |
| `kbs_connector_last_success_timestamp_seconds` | | Unix time of the last cycle that advanced the watermark. |
| `kbs_connector_watermark_lag_seconds` | | Now minus the stored watermark. |
| `kbs_snapshot_object_lag_seconds` | | Snapshot time minus the source `updated_at`. The p95 is the freshness signal. The dashboard turns red above 30 seconds. |
| `kbs_snapshots_written_total` | `object_type` | Objects written to S3. |
| `kbs_snapshots_skipped_unchanged_total` | | Version, ACL, and hash already matched. |
| `kbs_snapshots_deleted_total` | | Keys removed after the source deleted the object. |
| `kbs_snapshot_bytes_total` | | Bytes written. |
| `kbs_snapshot_errors_total` | `stage` (`list`, `fetch`, `contract`, `s3_put`, `state`, `delete`) | Failures. A contract error is an object that carried an excluded field. |
| `kbs_delete_verification_failures_total` | | The key was still there after the delete. |
| `kbs_source_objects` | | Ids the source listed. |
| `kbs_snapshot_objects` | | Keys in the bucket for that source. |
| `kbs_reconcile_drift` | | Snapshot count minus source count. Zero is healthy. |
| `kbs_connector_dependency_up` | `dependency` (`postgres`, `s3`, `harness`) | 1 when that dependency answered. |

`kbs_worker_up{worker="connector"}` stays 1 after the contract registry loads, including while the worker is waiting for Postgres or the bucket.

Logs are JSON on stdout. `source_id` is a Loki label. A snapshot line names the object type, version, and byte count. It does not contain the body. `{service="connector-hr"}` is the connector panel.

Not built yet: queue depth, dead-letter depth, retrieval latency, and empty-result rate.
