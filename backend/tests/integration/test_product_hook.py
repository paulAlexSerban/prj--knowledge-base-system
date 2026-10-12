"""A product write pointer crosses the receiver and becomes a snapshot."""

from pathlib import Path

import boto3
import httpx
from fastapi.testclient import TestClient
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from connector_core.state import State
from connector_core.store import SnapshotStore, object_key
from connector_product.apply import apply_notice
from connector_product.client import ProductConnector
from connector_product.queue import HookQueue
from harness_product.app import create_app as harness_app
from harness_product.notify import Notifier
from harness_product.store import Store
from ingest_contract.events import RAW_BUCKET
from ingest_contract.hooks import HOOK_QUEUE
from shared.contracts.validate import load_contracts
from webhook_receiver.app import create_app as receiver_app

ROOT = Path(__file__).resolve().parents[3]


class _AppTransport(httpx.BaseTransport):
    def __init__(self, app) -> None:
        self.app = TestClient(app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.app.request(request.method, request.url.path)
        return httpx.Response(response.status_code, content=response.content)


def test_posted_hook_is_snapshotted(tmp_path, monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    store = Store(tmp_path / "product.db", tmp_path / "specs")
    harness = harness_app(store, notifier=Notifier(None))
    connector = ProductConnector(
        httpx.Client(transport=_AppTransport(harness), base_url="http://product"),
        backoff=0,
    )
    contract = next(
        item
        for item in load_contracts(ROOT / "infrastructure" / "config" / "sources")
        if item.source_id == "product"
    )
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    state = State(engine)
    state.create_tables()
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=RAW_BUCKET)
        sqs = boto3.client("sqs", region_name="us-east-1")
        sqs.create_queue(
            QueueName=HOOK_QUEUE,
            Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "false"},
        )
        posted = TestClient(receiver_app()).post(
            "/webhooks/product",
            json={"source_id": "product", "object_id": "spec-checkout", "event_type": "create"},
        )
        assert posted.status_code == 202
        hooks = HookQueue(sqs)
        assert hooks.ready()
        notices = hooks.receive(wait=0)
        assert len(notices) == 1
        notice, handle = notices[0]
        report = apply_notice(
            connector, contract, state, SnapshotStore(s3, RAW_BUCKET), None, notice
        )
        assert report.ok and report.written == 1
        hooks.ack(handle)
        key = object_key("product", "spec", "spec-checkout", "md")
        assert s3.get_object(Bucket=RAW_BUCKET, Key=key)["Body"].read().startswith(b"# Checkout")
