"""Prometheus metrics for the connector. Names and labels are the contract with the dashboard."""

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

SYNC_RUNS = Counter(
    "kbs_connector_sync_runs_total",
    "Sync cycles finished",
    ["source_id", "result"],
)
SYNC_DURATION = Histogram(
    "kbs_connector_sync_duration_seconds",
    "Sync cycle duration",
    ["source_id"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
LAST_SUCCESS = Gauge(
    "kbs_connector_last_success_timestamp_seconds",
    "Unix time of the last sync that advanced the watermark",
    ["source_id"],
)
WATERMARK_LAG = Gauge(
    "kbs_connector_watermark_lag_seconds",
    "Now minus the stored watermark",
    ["source_id"],
)
OBJECT_LAG = Histogram(
    "kbs_snapshot_object_lag_seconds",
    "Snapshot time minus the source updated_at",
    ["source_id"],
    buckets=(1, 5, 10, 15, 30, 60, 120, 300, 600),
)
WRITTEN = Counter(
    "kbs_snapshots_written_total",
    "Snapshots written to S3",
    ["source_id", "object_type"],
)
SKIPPED = Counter(
    "kbs_snapshots_skipped_unchanged_total",
    "Objects skipped because version, ACL, and hash matched",
    ["source_id"],
)
DELETED = Counter(
    "kbs_snapshots_deleted_total",
    "Snapshots removed after the source deleted the object",
    ["source_id"],
)
BYTES = Counter(
    "kbs_snapshot_bytes_total",
    "Bytes written to S3",
    ["source_id"],
)
ERRORS = Counter(
    "kbs_snapshot_errors_total",
    "Snapshot failures by stage",
    ["source_id", "stage"],
)
def _counter(name: str, documentation: str, labelnames: tuple[str, ...]) -> Counter:
    """Reuse a collector when another package registered the same name in-process."""
    found = REGISTRY._names_to_collectors.get(name)
    if found is not None:
        return found
    return Counter(name, documentation, labelnames)


DELETE_FAILURES = _counter(
    "kbs_delete_verification_failures_total",
    "Deletes whose verification still found the object",
    ("source_id", "store"),
)
SOURCE_OBJECTS = Gauge(
    "kbs_source_objects",
    "Objects reported by the source",
    ["source_id"],
)
SNAPSHOT_OBJECTS = Gauge(
    "kbs_snapshot_objects",
    "Objects present in the snapshot bucket",
    ["source_id"],
)
DRIFT = Gauge(
    "kbs_reconcile_drift",
    "Snapshot count minus source count",
    ["source_id"],
)
DEPENDENCY = Gauge(
    "kbs_connector_dependency_up",
    "1 when a dependency answered",
    ["dependency"],
)
PUBLISHED = Counter(
    "kbs_events_published_total",
    "Change events sent to the tier queue",
    ["source_id", "event_type"],
)

# stage label values. enqueue is a send that failed after the snapshot write.
STAGES = ("list", "fetch", "contract", "s3_put", "state", "delete", "enqueue")

METRIC_LABELS = {
    "kbs_connector_sync_runs_total": ("source_id", "result"),
    "kbs_connector_sync_duration_seconds": ("source_id",),
    "kbs_connector_last_success_timestamp_seconds": ("source_id",),
    "kbs_connector_watermark_lag_seconds": ("source_id",),
    "kbs_snapshot_object_lag_seconds": ("source_id",),
    "kbs_snapshots_written_total": ("source_id", "object_type"),
    "kbs_snapshots_skipped_unchanged_total": ("source_id",),
    "kbs_snapshots_deleted_total": ("source_id",),
    "kbs_snapshot_bytes_total": ("source_id",),
    "kbs_snapshot_errors_total": ("source_id", "stage"),
    "kbs_delete_verification_failures_total": ("source_id", "store"),
    "kbs_source_objects": ("source_id",),
    "kbs_snapshot_objects": ("source_id",),
    "kbs_reconcile_drift": ("source_id",),
    "kbs_connector_dependency_up": ("dependency",),
    "kbs_events_published_total": ("source_id", "event_type"),
}
