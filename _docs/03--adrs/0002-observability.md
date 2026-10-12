# ADR 0002: Observability

Status: accepted for the POC.

## Context

Stale or unauthorized knowledge fails quietly. A global average latency hides the principal who can see almost nothing, and a green process hides a connector that has not completed a sync. Spike 01 requires ingestion health to be monitored with the same seriousness as uptime. Spike 03 requires empty-result rate by permission group. Spike 04 requires per-source lag and dead-letter depth.

Demo step 2 is a denied query. The empty-result rate shows that denies are happening. It does not show which request was denied. That line has to be queryable from the dashboard while the demo is running.

## Decision

Grafana Alloy is the collector. It scrapes `/metrics` on the API, the MCP server, the four connectors, the ingestion worker, the webhook receiver, and the four harnesses, and remote-writes those series to Prometheus. The product connector also reports hook-queue depth. The receiver counts accepted and refused notices. Jupyter, pgAdmin, and the SQLite viewers have no metrics endpoint and are not scraped. It also tails container logs from the Docker socket and pushes them to Loki.

Prometheus stores metrics and runs with remote write enabled. It scrapes only Alloy and Loki, so the collector and the log store appear on the same dashboard as the services. Loki is one process with filesystem storage. That is the built tier. Object storage on the floci S3 bucket is the production path.

Loki labels are `service`, `trust_area`, `level`, and `source_id`. Services write one JSON object per line on stdout. `request_id`, `principal`, `chunk_id`, `decision`, `trace_id`, and `span_id` stay in the line and are not labels. `trace_id` and `span_id` are null until a span is current. The line never contains `salary`, `compensation`, `ssn`, `national_id`, an API key, a password, or retrieved text. A deny is a line whose `decision` is `deny`. Grafana opens the matching lines from a derived field on `request_id`. Scraped metrics get the same `service` name as the Compose service, copied from `job`, and a `trust_area` of `access`, `sources`, or `ingestion`.

Grafana requires a sign-in. The local account is `admin`, and the password is `GRAFANA_ADMIN_PASSWORD`, which defaults to `admin`. The UI stays on `127.0.0.1`. Changes made in the UI are stored in Grafana's database and kept across restarts while the JSON file is unchanged. To keep a change in git, export the JSON and commit it under `observability/grafana/dashboards/`.

Definitions are in [metrics.md](../../observability/metrics.md). The overview is [kbs-overview.json](../../observability/grafana/dashboards/kbs-overview.json). It keeps the SLO row, scrape and worker health, and the error and deny log. Detail is on [kbs-ingestion.json](../../observability/grafana/dashboards/kbs-ingestion.json), [kbs-retrieval-access.json](../../observability/grafana/dashboards/kbs-retrieval-access.json), and [kbs-platform.json](../../observability/grafana/dashboards/kbs-platform.json). Each board links to the others. Built panels:

| Panel | Source |
| ----- | ------ |
| SLO row | Recording rules `kbs:hr_snapshot_lag_p95_seconds` (red at 30 seconds), `kbs:search_p95_seconds` (red at 0.25 seconds), `kbs:dlq_messages`, and `kbs:delete_verification_failures_5m`. Prometheus keeps 15 days. |
| Scrape targets and workers | `up`, `kbs_worker_up` (connector and ingestion), `kbs_worker_dependency_up` |
| Snapshot lag, last successful sync, write and skip rate, snapshot errors, snapshot drift, connector logs | The same series from every connector, broken out by `source_id`. Connector logs are `{service=~"connector-.*"}`. |
| Sync lag per source | `kbs_ingest_event_lag_seconds` p95 and `kbs_connector_watermark_lag_seconds`. Yellow above 30 seconds (HR contract). Red above 300 seconds (sales, product, and engineering contracts). Event lag is now minus `occurred_at` when the worker finishes the message. SQS does not expose oldest-message age outside CloudWatch, so that is not the panel. |
| Queue depth per tier | `kbs_queue_depth`, from `GetQueueAttributes` in the worker, not from CloudWatch |
| Dead-letter depth | `kbs_dlq_depth`. Red when any message is visible. |
| Embed calls and skips | `kbs_embed_calls_total` and `kbs_embed_skipped_unchanged_total` |
| Index drift | `kbs_index_drift`, next to snapshot reconciliation drift |
| Store write latency and errors | `kbs_store_write_seconds`. `kbs_store_errors_total` is an `increase` over the selected range, not the raw counter. |
| Delete verification failures | `increase(kbs_delete_verification_failures_total[$__range])` by source and store. Red on any failure in the range. |
| Ingestion logs | Loki `{service="ingestion-worker"}`. `event_type` and `decision` are in the line. `request_id` stays in the line. |
| HTTP rate, errors, and duration | `kbs_http_requests_total` and `kbs_http_request_seconds` on the API, the MCP server, and the webhook receiver. `route` is a template or `unmatched`. Health and metrics scrapes are filtered out of the panels. |
| Retrieval latency | `kbs_retrieval_seconds` p95 by tool. The 0.25 bucket is the 250 ms target. Search includes the HR ACL re-check. |
| ACL re-check | `kbs_acl_recheck_seconds` and `kbs_acl_recheck_failures_total`. |
| Empty-result rate | `kbs_retrieval_empty_total` divided by `kbs_retrieval_total`, by permission group. |
| Auth failures | `kbs_auth_failures_total` by reason. |
| Audit denies | Loki `{service=~".+"} \| json \| decision="deny"`. `request_id` is a derived field, not a label. |

Not built, and not charted: traces. The dashboard does not query `kbs.audit_log`. The deny panel reads the log line.

Alerts for the POC are dashboard colors, not a paging system and not Alertmanager: P0 lag over 30 seconds, any dead-letter message, any delete verification failure.

Traces are deferred until a latency regression cannot be explained from the histograms. Alloy can grow an OTLP receiver later without changing the metric or log pipeline. Tempo is the production path for trace storage.

The Docker socket mount is a trust-boundary exception. It is read-only. It is how Alloy reads logs from containers that are not on `kbs-obs`, including Postgres, Neo4j, and floci. Alloy joins `kbs-obs` only. It has no path of its own onto `kbs-data`.

## Dashboard review

Reviewed [kbs-overview.json](../../observability/grafana/dashboards/kbs-overview.json) before changing it. The metric names match [metrics.md](../../observability/metrics.md) and the spikes. The page is one flat board. These are the defects. The commits that follow this note fix the ones that mislead a reader. The metric names stay.

What is wrong:

- Eight panels have an empty description. One is titled "Not built" and only says that traces are absent. That panel is not a signal.
- `kbs_store_errors_total`, `kbs_delete_verification_failures_total`, and `kbs_webhook_events_total` are counters. The stat panels read the raw series, so the number is everything since the process started, not failures in the selected range.
- `kbs_dlq_depth` and `kbs_hook_queue_depth` are gauges of visible messages. The current value is the right reading, so they are not wrapped in `increase()`. The hook panel turns red at 1 for every queue, including the live hook queue, where a visible message is normal work. Only the dead-letter queue should turn red.
- Snapshot lag and the write rate are summed without `source_id`. The connector log query is `{service="connector-hr"}`. Four connectors emit the same series. A stale sales source disappears inside the HR number.
- Last successful sync and snapshot lag turn red at 30 seconds for every source. The HR contract target is 30 seconds. Sales, product, and engineering are 300 seconds and poll every 60 seconds. One red line marks a healthy slower source as failed.
- Scrape `up` turns red at 80. `up` is 0 or 1, so a down target stays green. The write-rate panel copies that same threshold.
- Reconciliation drift turns red at 0.001 and again at 1. Drift is a whole number of objects. A negative drift is the same failure as a positive one, and the panel does not take the absolute value.
- Retrieval latency has no unit and no line at 250 ms, even though that bucket exists on the histogram.
- There is no request rate, error rate, or duration for HTTP. The retrieval histogram does not show a 5xx.
- The board has no variables, so a demo cannot pin one source or one permission group.
- Datasource references are not checked by a test.
- Loki links `request_id` to other log lines. Nothing records a `trace_id`.

What stays:

- The metric names, the labels, and the rule that object ids, principals, and chunk text are not labels.
- Alloy remote-write, the Loki label set, and dashboard colors instead of Alertmanager.
- Dead-letter depth as the current gauge, red when any message is visible.
- The deny panel reading the log line, not `kbs.audit_log`.

## Why

Freshness and authorization are distributions, so the demo needs metrics. The deny in demo step 2 is one line, so the demo also needs a log store. Alloy is one collector for both, which is the pipeline this system would run in production, sized to one Compose project. Grep on a container does not survive the five-step demo, and it cannot tie a `request_id` on the dashboard back to the line.

## Alternatives rejected

| Alternative | Why it was rejected for the POC |
| ----------- | ------------------------------- |
| Logs only | Sync lag and empty-result rate are distributions. Grep does not show them during a five-step demo. |
| Prometheus scraping targets directly, with no collector | Enough for the nine metrics endpoints. Logs stay in `docker logs`, and traces would add a second pipeline later. |
| A tracing backend (Jaeger or Tempo) in this POC | Justified when a latency regression cannot be explained from the histograms. That regression has not been seen. Traces can be exported later to Tempo without changing the metric or log pipeline. |
| CloudWatch on floci | floci's purpose here is queues, buckets, and scheduler. Metrics and logs stay on Prometheus and Loki so they work the same in Compose. |
| Grafana Agent | Alloy replaces it and is the supported collector. |

## Consequences

- Every new connector must emit the sync metrics or the dashboard shows a gap. That is intentional.
- Cardinality is bounded. The empty-result metric uses source id and permission group, not user id. Per-user detail belongs in the audit table (spike 03) and in the log line, not in Prometheus and not in Loki labels.
- A new service needs a scrape entry in the Alloy config, a healthcheck, and JSON logs on stdout. Alloy and Loki are scraped by Prometheus. Jupyter, pgAdmin, and the SQLite viewers are dev tools: they have a healthcheck and are not scraped.
- The socket mount can read every container's stdout. Those lines must not contain store passwords or retrieved text. Compose does not put passwords in command arguments.

## Production path

Page on P0 lag and dead-letter depth. Move Loki chunks to the floci S3 bucket when a local disk is no longer enough. Add Tempo when a latency regression cannot be explained from the histograms, using an OTLP receiver on the same Alloy process. Keep this dashboard's panel list as the SLO view, and add detail dashboards underneath it.
