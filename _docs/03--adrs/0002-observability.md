# ADR 0002: Observability

Status: accepted for the POC.

## Context

Stale or unauthorized knowledge fails quietly. A global average latency hides the principal who can see almost nothing, and a green process hides a connector that has not completed a sync. Spike 01 requires ingestion health to be monitored with the same seriousness as uptime. Spike 03 requires empty-result rate by permission group. Spike 04 requires per-source lag and dead-letter depth.

## Decision

OpenTelemetry traces and metrics from the API, MCP server, and both workers. Prometheus scrapes them. One Grafana dashboard, checked in under `observability/`, with only these panels:

| Panel                                                                  | Why it exists                                                                |
| ---------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Sync lag per source                                                    | Freshness SLO. Lag is now minus the newest unprocessed event time.           |
| Last successful sync per source                                        | Detects a connector that stopped.                                            |
| Queue depth per priority (P0, P1, P2)                                  | Shows a bulk backfill blocking, or not blocking, policy updates.             |
| Dead-letter depth                                                      | Demo step 5 and any poison message.                                          |
| Retrieval latency, split into search and high-sensitivity ACL re-check | The 250 ms target and the HR re-check are different numbers.                 |
| Empty-result rate by permission group                                  | Post-filter starvation and mistaken denies. A global rate is not sufficient. |
| Embed calls skipped by content hash                                    | Confirms the cost control in spike 04 is real.                               |
| Indexed id count minus source id count                                 | Reconciliation drift.                                                        |

Alerts for the POC are dashboard thresholds, not a paging system: P0 lag over 30 seconds, any dead-letter message, ACL re-check failures.

## Why

The dashboard is the demo's evidence that freshness and authorization are measurable. A wider stack (multiple dashboards, log aggregation, tracing UI) would show tool familiarity and would not change a decision in this POC. Traces stay on so a single slow request can be opened during the demo if a panel looks wrong.

## Alternatives rejected

| Alternative                                                            | Why it was rejected for the POC                                                                                                                                          |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Logs only                                                              | Sync lag and empty-result rate are distributions. Grep does not show them during a five-step demo.                                                                       |
| A full tracing backend (Jaeger or Tempo) plus logs (Loki) plus metrics | Justified in production. For the POC the metric panels answer the questions the assignment asks. Traces can be exported later to Tempo without changing instrumentation. |
| CloudWatch on floci                                                    | floci's purpose here is queues, buckets, and scheduler. Application metrics stay on Prometheus so they work the same in Compose.                                         |

## Consequences

- Every new connector must emit the sync metrics or the dashboard shows a gap. That is intentional.
- Cardinality is bounded: source id and permission group, not user id, on the empty-result metric. Per-user detail belongs in the audit table (spike 03), not in Prometheus.

## Production path

Page on P0 lag and dead-letter depth. Add trace storage when a latency regression cannot be explained from the histograms. Keep the single dashboard's panel list as the SLO view, and add detail dashboards underneath it.
