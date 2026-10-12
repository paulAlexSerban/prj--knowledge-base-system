# ADR 0009: Alloy, Prometheus, Loki, Tempo, and Grafana

Status: accepted for the POC.

## Context

Freshness and authorization are distributions, so the demo needs metrics. The deny in the demo is one log line, so it needs a log store. One object change crosses a queue, so it needs a trace. Grep on a container does not survive the demo, and it cannot tie a `request_id` on a chart back to a line.

## Decision

Grafana Alloy (`grafana/alloy:v1.20.1`) is the collector. It scrapes `/metrics` and remote-writes to Prometheus, pushes container logs to Loki, and exports OTLP traces to Tempo. Grafana is the UI. Every feature process writes JSON logs to stdout and exposes metrics. The publisher puts a W3C `traceparent` on the queue message, and the worker continues that trace. Definitions are in [observability/metrics.md](../../observability/metrics.md). Cardinality is bounded: source id and permission group, not user id, object id, or chunk text. Per-user detail stays in `kbs.audit_log` and in the log line.

## Alternatives rejected

- Logs only. Sync lag and empty-result rate are distributions. Grep does not show them during the demo.
- Prometheus scraping targets directly, with no collector. Enough for metrics. Logs stay in `docker logs`, and traces would be a second pipeline.
- Jaeger beside Tempo. Tempo is the store Alloy already exports to. A second trace backend adds a UI and no new signal.
- CloudWatch on floci. floci is here for queues and the bucket. Metrics and logs stay on this stack so they behave the same in Compose.
- Grafana Agent. Alloy replaces it and is the supported collector.

## Consequences

- A new service needs a scrape entry, a healthcheck, and JSON logs. Jupyter, the chat page, pgAdmin, and the SQLite viewers are the exception: a healthcheck, and no scrape.
- The read-only Docker socket is a separate decision ([ADR 0011](0011-alloy-docker-socket.md)). Dashboards and alerts are too ([ADR 0010](0010-dashboards-as-code.md)).

## Production path

The same Alloy, with OTLP to Datadog or Grafana Cloud. CloudWatch only for AWS-service metrics. Loki chunks and Tempo blocks move to object storage when local disk is not enough.
