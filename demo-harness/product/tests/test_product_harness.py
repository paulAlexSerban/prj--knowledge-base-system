import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from harness_product.app import create_app
from harness_product.notify import Notifier
from harness_product.store import Store

FORBIDDEN = {"salary", "compensation", "ssn", "national_id", "commission_rate"}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class Sink(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        self.server.bodies.append(body)  # type: ignore[attr-defined]
        self.send_response(202)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        return


@pytest.fixture
def harness(tmp_path):
    files = tmp_path / "specs"
    store = Store(tmp_path / "product.db", files, clock=Clock())
    server = HTTPServer(("127.0.0.1", 0), Sink)
    server.bodies = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    notifier = Notifier(f"http://127.0.0.1:{port}/webhooks/product")
    client = TestClient(create_app(store, notifier))
    try:
        yield client, files, server
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _keys(node: object) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {key for value in node.values() for key in _keys(value)}
    if isinstance(node, list):
        return {key for value in node for key in _keys(value)}
    return set()


def test_seed_loads_twelve_specs(harness):
    client, _files, _server = harness
    body = client.get("/discover").json()
    assert body["counts"] == {"spec": 12}
    assert body["capabilities"] == ["webhook"]
    content = client.get("/specs/spec-checkout/content")
    assert content.headers["content-type"].startswith("text/markdown")
    assert "## Decline retry" in content.text
    assert "epic-checkout-retry" in content.text
    assert client.get("/specs/spec-checkout/acl").json()["native_acl"] == ["product_org"]


def test_edit_and_delete_post_a_webhook(harness):
    client, files, server = harness
    spec_id = "spec-eu-tax"
    before = client.get(f"/specs/{spec_id}").json()
    edited = client.put(
        f"/admin/specs/{spec_id}",
        json={"body": "# EU tax\n\n## Scope\n\nChanged.\n"},
    )
    assert edited.status_code == 200
    assert edited.json()["version"] == before["version"] + 1
    assert edited.json()["content_hash"] != before["content_hash"]
    assert server.bodies[-1] == {
        "source_id": "product",
        "object_id": spec_id,
        "event_type": "update",
    }
    assert client.delete(f"/admin/specs/{spec_id}").status_code == 204
    assert server.bodies[-1]["event_type"] == "delete"
    assert server.bodies[-1]["object_id"] == spec_id
    ids = {row["id"] for row in client.get("/ids").json()["ids"]["spec"]}
    assert spec_id not in ids
    assert not (files / f"{spec_id}.md").exists()
    assert client.get(f"/specs/{spec_id}/content").status_code == 404


def test_unreachable_receiver_does_not_fail_the_write(tmp_path):
    store = Store(tmp_path / "product.db", tmp_path / "specs", clock=Clock())
    notifier = Notifier("http://127.0.0.1:1/webhooks/product", timeout=0.2)
    client = TestClient(create_app(store, notifier))
    created = client.post(
        "/admin/specs",
        json={
            "spec_id": "spec-extra-one",
            "title": "Extra",
            "product_key": "checkout",
            "body": "# Extra\n\n## Note\n\nHello.\n",
        },
    )
    assert created.status_code == 201
    assert client.get("/specs/spec-extra-one").status_code == 200


def test_no_read_path_returns_forbidden_fields(harness):
    client, _files, _server = harness
    for path in ("/discover", "/ids", "/specs", "/specs/spec-billing", "/specs/spec-billing/acl"):
        assert not _keys(client.get(path).json()) & FORBIDDEN, path


def test_invalid_acl_and_product_key_are_rejected(harness):
    client, _files, server = harness
    before = len(server.bodies)
    assert (
        client.put(
            "/admin/specs/spec-billing/acl", json={"native_acl": ["project_member"]}
        ).status_code
        == 422
    )
    assert client.put("/admin/specs/spec-billing/acl", json={"native_acl": []}).status_code == 422
    assert (
        client.post(
            "/admin/specs",
            json={
                "spec_id": "spec-extra-two",
                "title": "Extra",
                "product_key": "nope",
                "body": "# Extra\n\n## Note\n\nHello.\n",
            },
        ).status_code
        == 422
    )
    assert len(server.bodies) == before


def test_unchanged_edit_does_not_notify(harness):
    client, _files, server = harness
    current = client.get("/specs/spec-billing/content").text
    before = len(server.bodies)
    edited = client.put("/admin/specs/spec-billing", json={"body": current})
    assert edited.status_code == 200
    assert edited.json()["version"] == 1
    assert len(server.bodies) == before
