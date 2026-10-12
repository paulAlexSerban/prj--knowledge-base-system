# ADR 0010: Dashboards and alerts as code, with no pager

Status: accepted for the POC.

## Context

The demo has to show sync lag, dead-letter depth, empty results by permission group, and a deny. Those views have to be the same on every machine, and a UI edit must not become the source of truth.

## Decision

Five Grafana dashboards are JSON in `observability/grafana/dashboards/` and are provisioned on start: overview, ingestion, retrieval and access, platform, and traces. The overview is the SLO row. The others are the detail. Recording rules turn the SLO numbers red: HR snapshot lag p95 over 30 seconds, search p95 over 0.25 seconds, any visible dead-letter message, any delete-verification failure in five minutes. There is no Alertmanager and no paging. The deny panel reads the log line (`decision: deny`). The boards do not query `kbs.audit_log`. Prometheus keeps 15 days locally. Edit the JSON in git.

## Alternatives rejected

- Alertmanager in the POC. Nothing is on call. A red panel is the signal the demo can point at.
- A Grafana service map. The trace list is enough to follow one change. A map needs span metrics this POC does not generate.
- Building the boards by hand in the UI. They would drift from git on the next `make up`.

## Consequences

- A new connector that does not emit the sync metrics shows up as a gap. That is intentional.
- Dashboard colors are not a paging policy. Production would page on P0 lag and dead-letter depth.
