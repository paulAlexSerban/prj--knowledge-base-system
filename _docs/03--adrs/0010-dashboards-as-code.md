# ADR 0010: Dashboards and alerts as code, with no pager

Status: accepted for the POC.

## Context

The demo has to show sync lag, dead-letter depth, empty results by permission group, and a deny. Those views have to be the same on every machine, and a UI edit must not become the source of truth.

## Decision

Grafana dashboards are JSON in `observability/grafana/dashboards/` and are provisioned into one KBS folder on start. The overview is the SLO row. Ingestion, retrieval and access, platform, and traces are the detail. `kbs-service-graph` is the node graph from Tempo span metrics ([ADR 0027](0027-service-graph.md)). One board per component is `kbs-gateway`, `kbs-ingestion-pipeline`, `kbs-store`, `kbs-harness`, and `kbs-frontend` (the chat page, titled KBS / Demo model). Recording rules turn the SLO numbers red: HR snapshot lag p95 over 30 seconds, search p95 over 0.25 seconds, any visible dead-letter message, any delete-verification failure in five minutes. There is no Alertmanager and no paging. The deny panel reads the log line (`decision: deny`) on the overview and retrieval boards. The boards do not query `kbs.audit_log`. Prometheus keeps 15 days locally. Edit the JSON in git.

## Alternatives rejected

- Alertmanager in the POC. Nothing is on call. A red panel is the signal the demo can point at.
- Building the boards by hand in the UI. They would drift from git on the next `make up`.
- Leaving hops only as a trace list. Span metrics and `kbs-service-graph` are [ADR 0027](0027-service-graph.md).

## Consequences

- A new connector that does not emit the sync metrics shows up as a gap. That is intentional.
- Dashboard colors are not a paging policy. Production would page on P0 lag and dead-letter depth.
