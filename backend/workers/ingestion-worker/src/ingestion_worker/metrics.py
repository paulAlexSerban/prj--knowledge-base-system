"""Prometheus metrics for the ingestion worker."""

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

# The connector process registers the same name. Tests import both.
UP = REGISTRY._names_to_collectors.get("kbs_worker_up") or Gauge(
    "kbs_worker_up", "1 when the worker process is up", ["worker"]
)
QUEUES = Gauge("kbs_queues_reachable", "1 when P0, P1, and P2 queues all answer")
EVENTS = Counter(
    "kbs_ingest_events_total",
    "Change events finished by the worker",
    ["source_id", "event_type", "result"],
)
EVENT_LAG = Histogram(
    "kbs_ingest_event_lag_seconds",
    "Now minus occurred_at when the worker finishes the event",
    ["source_id"],
    buckets=(1, 5, 10, 15, 30, 60, 120, 300, 600),
)
QUEUE_DEPTH = Gauge(
    "kbs_queue_depth",
    "Visible messages on a tier queue",
    ["queue"],
)
DLQ_DEPTH = Gauge(
    "kbs_dlq_depth",
    "Visible messages on a dead-letter queue",
    ["queue"],
)
