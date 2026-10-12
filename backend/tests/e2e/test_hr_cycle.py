"""Live stack: seed, edit, delete, lose floci, recover, reset.

Run with `make e2e` while `make up` is healthy. It is not part of `make test`.
"""

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import boto3
import pytest

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
HARNESS = os.environ.get("HARNESS_HR_URL", "http://127.0.0.1:8101")
METRICS = os.environ.get("CONNECTOR_METRICS_URL", "http://127.0.0.1:9100/metrics")
BUCKET = "kbs-raw-snapshots"
QUEUES = (
    "kbs-p0-dlq.fifo",
    "kbs-p1-dlq.fifo",
    "kbs-p2-dlq.fifo",
    "kbs-p0.fifo",
    "kbs-p1.fifo",
    "kbs-p2.fifo",
)
HOOK_QUEUES = ("kbs-product-hooks-dlq.fifo", "kbs-product-hooks.fifo")


def _aws(service: str):
    return boto3.client(
        service,
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL", "http://127.0.0.1:4566"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _request(method: str, path: str, body: dict | None = None, timeout: int = 120) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"{HARNESS}{path}",
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def _s3_count() -> int:
    client = _aws("s3")
    total = 0
    token = None
    while True:
        kwargs = {"Bucket": BUCKET, "Prefix": "raw/hr/"}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        total += page.get("KeyCount", 0)
        if not page.get("IsTruncated"):
            return total
        token = page["NextContinuationToken"]


def _metric(name: str, **labels: str) -> float | None:
    with urllib.request.urlopen(METRICS, timeout=5) as response:
        text = response.read().decode()
    for line in text.splitlines():
        if not line.startswith(name):
            continue
        if labels and not all(f'{key}="{value}"' in line for key, value in labels.items()):
            continue
        if line.startswith("#"):
            continue
        return float(line.rsplit(" ", 1)[-1])
    return None


def _versions(key: str) -> int:
    client = _aws("s3")
    page = client.list_object_versions(Bucket=BUCKET, Prefix=key)
    entries = page.get("Versions", []) + page.get("DeleteMarkers", [])
    return len([item for item in entries if item["Key"] == key])


def _wait_until(predicate, seconds: int, label: str):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last is True or last == "ok":
            return
        time.sleep(5)
    raise AssertionError(f"{label} did not finish: {last}")


def _restore_floci() -> None:
    """floci is in memory. A restart drops the bucket and the queues."""
    subprocess.run([*COMPOSE, "start", "floci"], check=True)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:4566/", timeout=2)
            break
        except OSError:
            time.sleep(1)
    else:
        raise AssertionError("floci did not answer")
    s3 = _aws("s3")
    s3.create_bucket(Bucket=BUCKET)
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
    s3.put_public_access_block(
        Bucket=BUCKET,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    sqs = _aws("sqs")
    for name in QUEUES:
        sqs.create_queue(
            QueueName=name,
            Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "true"},
        )
    for name in HOOK_QUEUES:
        sqs.create_queue(
            QueueName=name,
            Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "false"},
        )


def test_hr_snapshot_cycle() -> None:
    try:
        urllib.request.urlopen(f"{HARNESS}/health", timeout=3)
    except OSError:
        pytest.skip("HR harness is not up; start the stack with make up")

    _request("POST", "/admin/reset")
    _wait_until(
        lambda: (
            _metric("kbs_reconcile_drift", source_id="hr") == 0
            and _metric("kbs_source_objects", source_id="hr") == 86
            and _s3_count() == 86
        ),
        seconds=1200,
        label="reset to the curated seed",
    )

    seeded = _request(
        "POST",
        "/admin/seed",
        {"kind": "person", "count": 10000, "seed": 1, "replace": False},
        timeout=600,
    )
    assert seeded["inserted"] == 10000
    expected = sum(_request("GET", "/discover")["counts"].values())
    _wait_until(
        lambda: _s3_count() == expected and _metric("kbs_reconcile_drift", source_id="hr") == 0,
        seconds=600,
        label="catch up after 10000 people",
    )

    key = "raw/hr/policy/pol-benefits-overview.md"
    before = _versions(key)
    _request(
        "PUT",
        "/admin/policies/pol-benefits-overview",
        {"body": "# Benefits Overview\n\nEdited by the end-to-end check.\n"},
    )
    _wait_until(lambda: _versions(key) == before + 1, seconds=35, label="one policy rewrite")
    body = _aws("s3").get_object(Bucket=BUCKET, Key=key)["Body"].read()
    assert b"Edited by the end-to-end check" in body

    deleted = "raw/hr/policy/pol-code-of-conduct.pdf"
    _request("DELETE", "/admin/policies/pol-code-of-conduct")
    _wait_until(lambda: _versions(deleted) == 0, seconds=100, label="delete sweep")

    subprocess.run([*COMPOSE, "stop", "floci"], check=True)
    try:
        _wait_until(
            lambda: (
                _metric("kbs_connector_dependency_up", dependency="s3") == 0
                or (_metric("kbs_snapshot_errors_total", source_id="hr", stage="s3_put") or 0) > 0
            ),
            seconds=90,
            label="S3 outage on the errors panel",
        )
    finally:
        _restore_floci()

    live = sum(_request("GET", "/discover")["counts"].values())
    _wait_until(
        lambda: _s3_count() == live and _metric("kbs_reconcile_drift", source_id="hr") == 0,
        seconds=900,
        label="refill after floci restarted",
    )
    assert _versions(key) == 1

    _request("POST", "/admin/reset")
    _wait_until(
        lambda: (
            _request("GET", "/discover")["counts"] == {"person": 48, "policy": 38}
            and _metric("kbs_reconcile_drift", source_id="hr") == 0
            and _s3_count() == 86
        ),
        seconds=1200,
        label="reset after the cycle",
    )
