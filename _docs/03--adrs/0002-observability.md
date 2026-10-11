# ADR 0002: Observability

Status: accepted for the POC.

## Context

Stale or unauthorized knowledge fails quietly. A global average latency hides the principal who can see almost nothing, and a green process hides a connector that has not completed a sync. Spike 01 requires ingestion health to be monitored with the same seriousness as uptime. Spike 03 requires empty-result rate by permission group. Spike 04 requires per-source lag and dead-letter depth.

Demo step 2 is a denied query. The empty-result rate shows that denies are happening. It does not show which request was denied. That line has to be queryable from the dashboard while the demo is running.

## Decision

Grafana Alloy is the collector. It scrapes `/metrics` on the API, the MCP server, the workers, and the harness, and remote-writes those series to Prometheus. Jupyter has no metrics endpoint and is not scraped. It also tails container logs from the Docker socket and pushes them to Loki.

Prometheus stores metrics and runs with remote write enabled. It scrapes only Alloy and Loki, so the collector and the log store appear on the same dashboard as the services. Loki is one process with filesystem storage. That is the built tier. Object storage on the floci S3 bucket is the production path.

Loki labels are `service`, `trust_area`, `level`, and `source_id`. Services write one JSON object per line on stdout. `request_id`, `principal`, `chunk_id`, and `decision` stay in the line and are not labels. The line never contains `salary`, `compensation`, `ssn`, `national_id`, an API key, or retrieved text. A deny is a line whose `decision` is `deny`. Grafana opens the matching lines from a derived field on `request_id`.

The dashboard that exists today has `up`, `kbs_worker_up`, and a logs panel for errors and audit denies. The panels below are the SLO view to add when those metrics exist:

| Panel | Why it exists |
| ----- | ------------- |
| Sync lag per source | Freshness SLO. Lag is now minus the newest unprocessed event time. |
| Last successful sync per source | Detects a connector that stopped. |
| Queue depth per priority (P0, P1, P2) | Shows a bulk backfill blocking, or not blocking, policy updates. |
| Dead-letter depth | Demo step 5 and any poison message. |
| Retrieval latency, split into search and high-sensitivity ACL re-check | The 250 ms target and the HR re-check are different numbers. |
| Empty-result rate by permission group | Post-filter starvation and mistaken denies. A global rate is not sufficient. |
| Embed calls skipped by content hash | Confirms the cost control in spike 04 is real. |
| Indexed id count minus source id count | Reconciliation drift. |

Alerts for the POC are dashboard thresholds, not a paging system: P0 lag over 30 seconds, any dead-letter message, ACL re-check failures.

Traces are deferred until a latency regression cannot be explained from the histograms. Alloy can grow an OTLP receiver later without changing the metric or log pipeline. Tempo is the production path for trace storage.

The Docker socket mount is a trust-boundary exception. It is read-only. It is how Alloy reads logs from containers that are not on `kbs-obs`, including Postgres, Neo4j, and floci. Alloy joins `kbs-obs` only. It has no path of its own onto `kbs-data`.

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
- A new service needs a scrape entry in the Alloy config, a healthcheck, and JSON logs on stdout. Alloy and Loki are scraped by Prometheus.
- The socket mount can read every container's stdout. Those lines must not contain store passwords or retrieved text. Compose does not put passwords in command arguments.

## Production path

Page on P0 lag and dead-letter depth. Move Loki chunks to the floci S3 bucket when a local disk is no longer enough. Add Tempo when a latency regression cannot be explained from the histograms, using an OTLP receiver on the same Alloy process. Keep this dashboard's panel list as the SLO view, and add detail dashboards underneath it.
