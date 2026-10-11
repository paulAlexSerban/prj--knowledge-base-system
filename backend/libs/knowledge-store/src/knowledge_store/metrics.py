"""Store metrics. The worker process exposes them on its /metrics port."""

from prometheus_client import REGISTRY, Counter, Gauge, Histogram


def _counter(name: str, documentation: str, labelnames: tuple[str, ...]) -> Counter:
    found = REGISTRY._names_to_collectors.get(name)
    if found is not None:
        return found
    return Counter(name, documentation, labelnames)


EMBED_CALLS = Counter(
    "kbs_embed_calls_total",
    "Embedding calls. One increment per text, not per object.",
    ["model"],
)
EMBED_SKIPPED = Counter(
    "kbs_embed_skipped_unchanged_total",
    "Objects whose content hash already matched, so nothing was embedded",
    ["source_id"],
)
STORE_WRITE = Histogram(
    "kbs_store_write_seconds",
    "Time spent writing one store",
    ["store", "op"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 15),
)
STORE_ERRORS = Counter(
    "kbs_store_errors_total",
    "Store write failures",
    ["store", "op"],
)
DELETE_FAILURES = _counter(
    "kbs_delete_verification_failures_total",
    "Deletes whose verification still found the object",
    ("source_id", "store"),
)
INDEX_OBJECTS = Gauge(
    "kbs_index_objects",
    "Distinct objects that have at least one chunk",
    ["source_id"],
)
INDEX_DRIFT = Gauge(
    "kbs_index_drift",
    "Indexed object count minus source_objects count",
    ["source_id"],
)
DEPENDENCY = Gauge(
    "kbs_worker_dependency_up",
    "1 when a worker dependency answered",
    ["dependency"],
)
