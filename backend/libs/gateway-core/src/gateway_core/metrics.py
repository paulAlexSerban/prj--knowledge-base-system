"""Retrieval metrics. Both gateway processes expose them on /metrics.

Labels stay coarse. A principal, an object id, and chunk text are never labels.
The empty-result rate is kbs_retrieval_empty_total divided by kbs_retrieval_total.
"""

from prometheus_client import Counter, Histogram

from knowledge_store.acl import Principal

BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5)
# Highest privilege first. Anything else uses the caller's first group name.
_RANK = ("hr_admins", "employees")

RETRIEVAL_SECONDS = Histogram(
    "kbs_retrieval_seconds",
    "Time spent in one retrieval tool",
    ["tool", "surface"],
    buckets=BUCKETS,
)
REQUESTS = Counter(
    "kbs_retrieval_requests_total",
    "Retrieval requests",
    ["tool", "surface", "result"],
)
EMPTY = Counter(
    "kbs_retrieval_empty_total",
    "Searches that returned no chunk",
    ["source_id", "permission_group"],
)
TOTAL = Counter(
    "kbs_retrieval_total",
    "Searches counted for the empty-result rate",
    ["source_id", "permission_group"],
)
AUTH_FAILURES = Counter(
    "kbs_auth_failures_total",
    "Rejected bearer tokens",
    ["reason"],
)


def permission_group(principal: Principal) -> str:
    for name in _RANK:
        if name in principal.groups:
            return name
    if principal.groups:
        return principal.groups[0]
    return "none"
