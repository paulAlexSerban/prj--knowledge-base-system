"""Chat page metrics. Alloy scrapes the side port. Streamlit's own port is not scraped.

Labels are the sets below. A persona outside the demo list becomes `other`.
The question, the token, and retrieved text are not labels.
"""

import logging

from prometheus_client import Counter, Histogram, start_http_server

METRICS_PORT = 9105
BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60)
HOP_BUCKETS = (0, 1, 2, 3, 4, 8)

PERSONAS = frozenset({"employee_eng", "hr_admin", "outsider", "product_lead", "other"})
ROUTES = frozenset({"semantic", "relational", "hybrid", "unknown"})
MODES = frozenset({"none", "model", "retrieval", "error", "unknown"})
OUTCOMES = frozenset({"ok", "empty", "error", "unknown"})
TOKEN_KINDS = frozenset({"prompt", "completion"})

TURNS = Counter(
    "kbs_chat_turns_total",
    "Chat turns finished",
    ["persona", "route", "mode", "outcome"],
)
TURN_SECONDS = Histogram(
    "kbs_chat_turn_seconds",
    "Chat turn duration, including the gateway and the model",
    buckets=BUCKETS,
)
GATEWAY_SECONDS = Histogram(
    "kbs_chat_gateway_seconds",
    "Time spent calling the gateway for one turn",
    buckets=BUCKETS,
)
LLM_SECONDS = Histogram(
    "kbs_chat_llm_seconds",
    "Time spent in one model call",
    buckets=BUCKETS,
)
TOKENS = Counter(
    "kbs_chat_llm_tokens_total",
    "Tokens reported by the model call",
    ["kind"],
)
HOPS = Histogram(
    "kbs_chat_graph_hops",
    "Edges drawn for one graph or hybrid turn",
    buckets=HOP_BUCKETS,
)

_STARTED = False


def serve_metrics(port: int = METRICS_PORT) -> None:
    """Listen once, on all interfaces, so Alloy on kbs-obs can scrape.

    A second process in the same container, such as a test runner, leaves the
    existing listener in place.
    """
    global _STARTED
    if _STARTED:
        return
    try:
        start_http_server(port)
    except OSError as exc:
        _STARTED = True
        logging.getLogger("chat").warning("metrics port %s is not available: %s", port, exc)
        return
    _STARTED = True


def record_turn(
    *,
    persona: str,
    route: str,
    mode: str,
    outcome: str,
    seconds: float,
) -> None:
    TURNS.labels(
        persona=_one(persona, PERSONAS, "other"),
        route=_one(route, ROUTES, "unknown"),
        mode=_one(mode, MODES, "unknown"),
        outcome=_one(outcome, OUTCOMES, "unknown"),
    ).inc()
    TURN_SECONDS.observe(max(seconds, 0))


def record_gateway(seconds: float) -> None:
    GATEWAY_SECONDS.observe(max(seconds, 0))


def record_llm(seconds: float) -> None:
    LLM_SECONDS.observe(max(seconds, 0))


def record_tokens(kind: str, count: int) -> None:
    if kind not in TOKEN_KINDS or count <= 0:
        return
    TOKENS.labels(kind=kind).inc(count)


def record_hops(count: int) -> None:
    HOPS.observe(max(count, 0))


def _one(value: str, allowed: frozenset[str], fallback: str) -> str:
    return value if value in allowed else fallback
