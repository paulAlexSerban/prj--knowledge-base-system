"""Small HTTP helpers shared by seed.py, demo.py, and the eval runner.

Everything goes through public ports: the harnesses, the gateway API, and the
worker metrics. No script here opens Postgres, Neo4j, or SQLite.
"""

import os
import re
import time
from collections.abc import Callable

import httpx

API = os.environ.get("GATEWAY_API_URL", "http://127.0.0.1:8000")
HARNESS = {
    "hr": os.environ.get("HARNESS_HR_URL", "http://127.0.0.1:8101"),
    "sales": os.environ.get("HARNESS_SALES_URL", "http://127.0.0.1:8102"),
    "product": os.environ.get("HARNESS_PRODUCT_URL", "http://127.0.0.1:8103"),
    "engineering": os.environ.get("HARNESS_ENGINEERING_URL", "http://127.0.0.1:8104"),
}
WORKER_METRICS = os.environ.get("WORKER_METRICS_URL", "http://127.0.0.1:9101/metrics")

_TOKENS: dict[str, str] = {}


class Failure(Exception):
    """A step did not hold. The message is printed and the script exits non-zero."""


def token(persona: str) -> str:
    if persona not in _TOKENS:
        response = httpx.post(f"{API}/dev/token", json={"persona": persona}, timeout=30)
        if response.status_code != 200:
            raise Failure(f"token for {persona}: {response.status_code} {response.text[:120]}")
        _TOKENS[persona] = str(response.json()["token"])
    return _TOKENS[persona]


def search(persona: str, query: str, limit: int = 5) -> list[dict]:
    response = httpx.post(
        f"{API}/v1/search",
        json={"query": query, "limit": limit},
        headers={"authorization": f"Bearer {token(persona)}"},
        timeout=60,
    )
    if response.status_code != 200:
        raise Failure(f"search as {persona}: {response.status_code} {response.text[:120]}")
    return list(response.json()["results"])


def related(persona: str, kind: str, entity_id: str | None = None, limit: int = 25) -> list[dict]:
    body: dict[str, object] = {"kind": kind, "limit": limit}
    if entity_id:
        body["entity_id"] = entity_id
    response = httpx.post(
        f"{API}/v1/graph/related",
        json=body,
        headers={"authorization": f"Bearer {token(persona)}"},
        timeout=60,
    )
    if response.status_code != 200:
        raise Failure(f"related as {persona}: {response.status_code} {response.text[:120]}")
    return list(response.json()["paths"])


def harness(source: str, method: str, path: str, body: dict | None = None) -> dict:
    try:
        response = httpx.request(method, f"{HARNESS[source]}{path}", json=body, timeout=600)
    except httpx.HTTPError as exc:
        raise Failure(f"{source} {method} {path}: {type(exc).__name__}") from exc
    if response.status_code not in (200, 201, 204):
        raise Failure(f"{source} {method} {path}: {response.status_code} {response.text[:160]}")
    return response.json() if response.content else {}


def deal_ids(paths: list[dict]) -> set[str]:
    return {s["id"] for p in paths for s in p["steps"] if s["type"] == "Deal"}


def wait_until(check: Callable[[], bool], seconds: float, label: str, every: float = 3) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            if check():
                return
        except (httpx.HTTPError, Failure):
            pass
        if time.monotonic() > deadline:
            raise Failure(f"timed out after {seconds:.0f}s: {label}")
        time.sleep(every)


def queue_backlog() -> float | None:
    """Messages waiting on the three tier queues, or None when metrics do not answer."""
    try:
        text = httpx.get(WORKER_METRICS, timeout=5).text
    except httpx.HTTPError:
        return None
    values = re.findall(r'^kbs_queue_depth\{queue="[^"]+"\} ([0-9.]+)', text, re.MULTILINE)
    return sum(float(v) for v in values) if values else None
