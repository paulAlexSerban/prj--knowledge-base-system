"""Helpers for the HR notebooks.

Notebooks call the harness over HTTP and read raw snapshots from S3. They do
not open SQLite, Postgres, or Neo4j. Connector metrics are optional: when the
metrics URL does not answer, callers check S3 versions instead.
"""

import json
import os
import time

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError

HARNESS = os.environ.get("HARNESS_HR_URL", "http://127.0.0.1:8101")
BUCKET = os.environ.get("RAW_BUCKET", "kbs-raw-snapshots")
METRICS_URL = os.environ.get("CONNECTOR_METRICS_URL", "http://127.0.0.1:9100/metrics")
PREFIX = "raw/hr/"

# Names the connector refuses. Notebooks assert these are absent. Never send them.
FORBIDDEN = frozenset({"salary", "compensation", "ssn", "national_id"})

META_HASH = "kbs-content-hash"
META_VERSION = "kbs-version"
META_ACL = "kbs-native-acl"
META_UPDATED = "kbs-source-updated-at"
META_SNAPSHOT_AT = "kbs-snapshot-at"
META_KEYS = (META_HASH, META_VERSION, META_ACL, META_UPDATED, META_SNAPSHOT_AT)

# The connector polls every 10s and reconciles every 60s.
WRITE_WAIT = 90
DELETE_WAIT = 150
CATCHUP_WAIT = 180
QUIET_WAIT = 25

MISSING = frozenset({"404", "NoSuchKey", "NotFound"})
_S3 = None


def request(
    method: str,
    path: str,
    body: dict | None = None,
    params: dict | None = None,
    timeout: float = 120,
) -> httpx.Response:
    return httpx.request(method, f"{HARNESS}{path}", json=body, params=params, timeout=timeout)


def admin(
    method: str,
    path: str,
    body: dict | None = None,
    params: dict | None = None,
    timeout: float = 120,
) -> dict:
    """Call the harness and return JSON. 204 and an empty body return `{}`."""
    response = request(method, path, body, params, timeout)
    if response.status_code not in (200, 201, 204):
        raise SystemExit(f"{method} {path} returned {response.status_code}: {response.text[:300]}")
    if response.status_code == 204 or not response.content:
        return {}
    return response.json()


def expect_status(
    method: str,
    path: str,
    status: int,
    body: dict | None = None,
    params: dict | None = None,
) -> httpx.Response:
    response = request(method, path, body, params)
    if response.status_code != status:
        raise AssertionError(
            f"{method} {path} returned {response.status_code}, expected {status}: "
            f"{response.text[:300]}"
        )
    return response


def delete_if_present(object_type: str, object_id: str) -> int:
    """Remove a demo row so a notebook can be run again. 404 is fine."""
    path = f"/admin/{_collection(object_type)}/{object_id}"
    response = request("DELETE", path)
    if response.status_code not in (204, 404):
        raise SystemExit(f"DELETE {path} returned {response.status_code}: {response.text[:300]}")
    return response.status_code


def s3_client():
    global _S3
    if _S3 is None:
        _S3 = boto3.client(
            "s3",
            endpoint_url=os.environ.get("AWS_ENDPOINT_URL", "http://127.0.0.1:4566"),
            region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
            config=Config(s3={"addressing_style": "path"}),
        )
    return _S3


def snapshot(key: str) -> dict | None:
    """Head of the current version, or None when the key is gone."""
    try:
        return s3_client().head_object(Bucket=BUCKET, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in MISSING:
            return None
        raise


def object_body(key: str) -> bytes:
    return s3_client().get_object(Bucket=BUCKET, Key=key)["Body"].read()


def versions(key: str) -> int:
    """Object versions plus delete markers for this key only."""
    count = 0
    paginator = s3_client().get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=key):
        entries = page.get("Versions", []) + page.get("DeleteMarkers", [])
        count += sum(1 for entry in entries if entry["Key"] == key)
    return count


def list_keys(prefix: str = PREFIX) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        kwargs: dict = {"Bucket": BUCKET, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3_client().list_objects_v2(**kwargs)
        keys.extend(item["Key"] for item in page.get("Contents", []))
        token = page.get("NextContinuationToken")
        if not token:
            return keys


def source_total() -> int:
    payload = admin("GET", "/ids")
    return sum(len(rows) for rows in payload["ids"].values())


def wait_until(predicate, seconds: float, label: str, pause: float = 2.0):
    """Return the first truthy predicate result, or raise TimeoutError."""
    deadline = time.monotonic() + seconds
    last = None
    while True:
        last = predicate()
        if last:
            return last
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"{label} did not finish: {last!r}")
        time.sleep(min(pause, remaining))


def parse_metric(text: str, name: str, labels: dict[str, str]) -> float | None:
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        head, sep, raw = line.rpartition(" ")
        if not sep or not head.startswith(name):
            continue
        rest = head[len(name) :]
        if rest and not rest.startswith("{"):
            continue
        if labels and not all(f'{key}="{label}"' in head for key, label in labels.items()):
            continue
        return float(raw)
    return None


def metric(name: str, **labels: str) -> float | None:
    """Read one Prometheus sample. None when the connector metrics are unreachable."""
    try:
        response = httpx.get(METRICS_URL, timeout=3)
        response.raise_for_status()
    except (httpx.HTTPError, OSError):
        return None
    return parse_metric(response.text, name, labels)


def wait_cycles(count: int = 1, seconds: float = WRITE_WAIT) -> None:
    """Wait until the connector finishes more successful syncs.

    Sleeps for a fixed interval when metrics are not reachable, which is enough
    to cover one poll (10s) before an assertion that a snapshot did not change.
    """
    start = metric("kbs_connector_sync_runs_total", source_id="hr", result="ok")
    if start is None:
        time.sleep(QUIET_WAIT)
        return

    def advanced() -> bool:
        current = metric("kbs_connector_sync_runs_total", source_id="hr", result="ok")
        return current is not None and current >= start + count

    wait_until(advanced, seconds, "connector sync")


def canonical(record: dict) -> bytes:
    """Stable JSON, matching the person snapshot the connector writes."""

    def stable(value: object) -> object:
        if isinstance(value, list):
            return sorted(str(item) for item in value)
        return value

    ordered = {key: stable(value) for key, value in sorted(record.items())}
    return json.dumps(ordered, separators=(",", ":"), ensure_ascii=True).encode()


def nested_keys(node: object) -> set[str]:
    if isinstance(node, dict):
        found = set(node)
        for value in node.values():
            found |= nested_keys(value)
        return found
    if isinstance(node, list):
        found: set[str] = set()
        for value in node:
            found |= nested_keys(value)
        return found
    return set()


def describe(step: str, object_type: str, object_id: str) -> str:
    """One row: source version and ACL beside the current snapshot."""
    key = f"{PREFIX}{object_type}/{object_id}.{'json' if object_type == 'person' else 'md'}"
    response = request("GET", f"/{_collection(object_type)}/{object_id}")
    if response.status_code == 404:
        source = "404"
    elif response.is_success:
        record = response.json()
        acl = ",".join(record.get("native_acl", []))
        source = f"v{record['version']} {acl}"
    else:
        source = str(response.status_code)
    head = snapshot(key)
    if head is None:
        stored = "missing"
    else:
        meta = head.get("Metadata", {})
        stored = (
            f"v{meta.get(META_VERSION, '?')} {meta.get(META_ACL, '?')} versions={versions(key)}"
        )
    return f"{step:<18}{source:<28}{stored}"


def _collection(object_type: str) -> str:
    if object_type == "person":
        return "people"
    if object_type == "policy":
        return "policies"
    raise ValueError(object_type)
