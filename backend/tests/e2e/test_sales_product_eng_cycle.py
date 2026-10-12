"""Live cycle for sales, product, and engineering.

Run with `make e2e` while `make up` is healthy. It resets the three harnesses,
checks one deal-epic-team path, then puts them back.
"""

import json
import os
import shlex
import subprocess
import time
import urllib.request
from pathlib import Path

import boto3
import pytest

from ingestion_worker.replay import replay_all

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(ROOT / ".env"),
    "-f",
    str(ROOT / "infrastructure" / "orchestration" / "compose.yaml"),
]
HARNESS = {
    "engineering": os.environ.get("HARNESS_ENGINEERING_URL", "http://127.0.0.1:8104"),
    "sales": os.environ.get("HARNESS_SALES_URL", "http://127.0.0.1:8102"),
    "product": os.environ.get("HARNESS_PRODUCT_URL", "http://127.0.0.1:8103"),
}
DEAL = "deal-northwind-q3"
EPIC = "epic-checkout-retry"
SPEC = "spec-checkout"
POISON = "deal-filler-01"
POISON_ARR = 777001
P1 = "kbs-p1.fifo"
P1_DLQ = "kbs-p1-dlq.fifo"


def _aws(service: str):
    return boto3.client(
        service,
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL", "http://127.0.0.1:4566"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _request(base: str, method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def _run(args: list[str], env: dict | None = None) -> str:
    result = subprocess.run(
        args, check=True, capture_output=True, text=True, env=env or os.environ.copy()
    )
    return result.stdout


def _psql(sql: str) -> str:
    return _run(
        [*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "kbs", "-d", "kbs", "-tAc", sql]
    ).strip()


def _cypher(statement: str) -> str:
    script = 'cypher-shell -u neo4j -p "$KBS_NEO4J_PASSWORD" --format plain ' + shlex.quote(
        statement
    )
    return _run([*COMPOSE, "exec", "-T", "neo4j", "sh", "-c", script])


def _chunks(source: str, object_id: str) -> int:
    return int(
        _psql(
            "SELECT count(*) FROM kbs.chunks "
            f"WHERE source_id = '{source}' AND object_id = '{object_id}'"
        )
    )


def _text(source: str, object_id: str) -> str:
    return _psql(
        "SELECT coalesce(string_agg(content, E'\\n'), '') FROM kbs.chunks "
        f"WHERE source_id = '{source}' AND object_id = '{object_id}'"
    )


def _queue_url(name: str) -> str:
    return _aws("sqs").get_queue_url(QueueName=name)["QueueUrl"]


def _depth(name: str) -> int:
    attributes = _aws("sqs").get_queue_attributes(
        QueueUrl=_queue_url(name), AttributeNames=["ApproximateNumberOfMessages"]
    )
    return int(attributes["Attributes"]["ApproximateNumberOfMessages"])


def _wait_until(predicate, seconds: int, label: str):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last is True:
            return
        time.sleep(5)
    raise AssertionError(f"{label} did not finish: {last}")


def _reset() -> None:
    for base in HARNESS.values():
        _request(base, "POST", "/admin/reset")


def _worker(fail: str) -> None:
    env = os.environ.copy()
    env["FAIL_OBJECT_ID"] = fail
    _run([*COMPOSE, "up", "-d", "--no-deps", "--force-recreate", "ingestion-worker"], env)
    _wait_until(_worker_up, 60, "ingestion worker metrics")


def _worker_up() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:9101/metrics", timeout=2)
    except OSError:
        return False
    return True


def _indexed() -> bool:
    if _chunks("sales", DEAL) != 1 or _chunks("engineering", EPIC) != 1:
        return False
    if _chunks("product", SPEC) < 2:
        return False
    if "arr: 21000" not in _text("sales", POISON):
        return False
    path = _path()
    needed = ("480000", "2026-Q3", "negotiation", "checkout", "in_progress", "payments")
    return all(part in path for part in needed)


def _path() -> str:
    return _cypher(
        "MATCH (d:Deal {id: 'deal-northwind-q3'})-[:DEPENDS_ON]->"
        "(e:Epic {id: 'epic-checkout-retry'})-[:OWNED_BY]->(t:Team {id: 'payments'}) "
        "RETURN d.arr, d.quarter, d.stage, d.product_key, e.status, t.id"
    )


def test_three_sources_reach_the_graph() -> None:
    for base in HARNESS.values():
        try:
            urllib.request.urlopen(f"{base}/health", timeout=3)
        except OSError:
            pytest.skip("harnesses are not up; start the stack with make up")
    visibility = None
    try:
        _reset()
        _wait_until(_indexed, 240, "deal, epic, and spec indexed")
        deal_text = _text("sales", DEAL)
        assert "commission_rate" not in deal_text
        assert "arr: 480000" in deal_text

        _request(
            HARNESS["engineering"],
            "PATCH",
            f"/admin/epics/{EPIC}",
            {"status": "released"},
        )
        _wait_until(
            lambda: "released" in _path() and _chunks("engineering", EPIC) == 1, 150, "status"
        )

        _request(
            HARNESS["engineering"],
            "PUT",
            f"/admin/epics/{EPIC}/acl",
            {"native_acl": ["eng_leadership"]},
        )
        _wait_until(
            lambda: (
                "eng_leads"
                in _cypher("MATCH (e:Epic {id: 'epic-checkout-retry'}) RETURN e.acl_groups")
            ),
            150,
            "epic acl",
        )

        _request(HARNESS["sales"], "DELETE", "/admin/deals/deal-proseware-q3")
        _request(HARNESS["engineering"], "DELETE", "/admin/epics/epic-platform-cost")
        _wait_until(
            lambda: (
                _chunks("sales", "deal-proseware-q3") == 0
                and _chunks("engineering", "epic-platform-cost") == 0
                and "payments" in _path()
            ),
            180,
            "deleted deal and epic",
        )

        _run([*COMPOSE, "stop", "ingestion-worker"])
        for index in range(2, 10):
            _request(
                HARNESS["sales"],
                "PATCH",
                f"/admin/deals/deal-filler-{index:02d}",
                {"arr": 80000 + index},
            )
        _wait_until(lambda: _depth(P1) > 0, 120, "edits waiting on the tier queue")
        _run([*COMPOSE, "start", "ingestion-worker"])
        time.sleep(1)
        _run([*COMPOSE, "kill", "ingestion-worker"])
        _run([*COMPOSE, "start", "ingestion-worker"])
        _wait_until(_worker_up, 60, "worker after restart")
        _wait_until(
            lambda: all(
                _chunks("sales", f"deal-filler-{index:02d}") == 1
                and f"arr: {80000 + index}" in _text("sales", f"deal-filler-{index:02d}")
                for index in range(2, 10)
            ),
            180,
            "restart left one chunk per deal",
        )

        url = _queue_url(P1)
        visibility = _aws("sqs").get_queue_attributes(
            QueueUrl=url, AttributeNames=["VisibilityTimeout"]
        )["Attributes"]["VisibilityTimeout"]
        _aws("sqs").set_queue_attributes(QueueUrl=url, Attributes={"VisibilityTimeout": "2"})
        _worker(POISON)
        _request(HARNESS["sales"], "PATCH", f"/admin/deals/{POISON}", {"arr": POISON_ARR})
        _wait_until(lambda: _depth(P1_DLQ) >= 1, 180, "poisoned deal on the DLQ")
        assert str(POISON_ARR) not in _text("sales", POISON)
        _worker("")
        assert replay_all(_aws("sqs")).get(P1_DLQ, 0) >= 1
        _wait_until(
            lambda: (
                _chunks("sales", POISON) == 1 and f"arr: {POISON_ARR}" in _text("sales", POISON)
            ),
            180,
            "replayed deal",
        )
        edges = _cypher(
            "MATCH (d:Deal {id: 'deal-filler-01'})-[:DEPENDS_ON]->(:Epic) RETURN count(d)"
        )
        assert "1" in edges
    finally:
        if visibility is not None:
            _aws("sqs").set_queue_attributes(
                QueueUrl=_queue_url(P1), Attributes={"VisibilityTimeout": visibility}
            )
        _worker("")
        _reset()
