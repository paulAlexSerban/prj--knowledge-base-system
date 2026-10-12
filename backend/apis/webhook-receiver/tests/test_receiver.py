"""The receiver enqueues a pointer. An invalid body never reaches the queue."""

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws
from prometheus_client import REGISTRY

from ingest_contract.hooks import HOOK_QUEUE, HookNotice
from webhook_receiver.app import create_app

NOTICE = {"source_id": "product", "object_id": "spec-checkout", "event_type": "update"}


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        client = boto3.client("sqs", region_name="us-east-1")
        url = client.create_queue(
            QueueName=HOOK_QUEUE,
            Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "false"},
        )["QueueUrl"]
        yield client, url


def test_unknown_path_is_not_a_metric_label() -> None:
    raw = "/webhooks/product/spec-checkout"
    assert TestClient(create_app()).get(raw).status_code == 404
    routes: set[str] = set()
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == "kbs_http_requests_total":
                route = sample.labels.get("route")
                if route is not None:
                    routes.add(route)
    assert "unmatched" in routes
    assert raw not in routes


def test_valid_notice_is_enqueued(aws):
    client, url = aws
    response = TestClient(create_app()).post("/webhooks/product", json=NOTICE)
    assert response.status_code == 202
    assert response.json()["enqueued"] is True
    messages = client.receive_message(QueueUrl=url, MaxNumberOfMessages=1)
    notice = HookNotice.from_json(messages["Messages"][0]["Body"])
    assert notice.object_id == "spec-checkout"
    assert notice.event_type == "update"


def test_two_edits_of_the_same_spec_both_arrive(aws):
    client, url = aws
    app = TestClient(create_app())
    assert app.post("/webhooks/product", json=NOTICE).status_code == 202
    assert app.post("/webhooks/product", json=NOTICE).status_code == 202
    messages = client.receive_message(QueueUrl=url, MaxNumberOfMessages=10)
    assert len(messages["Messages"]) == 2


def test_invalid_body_is_rejected_and_not_enqueued(aws):
    client, url = aws
    app = TestClient(create_app())
    refused = app.post("/webhooks/product", json={"source_id": "sales", "object_id": "x"})
    assert refused.status_code == 422
    garbage = app.post(
        "/webhooks/product",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert garbage.status_code == 422
    attributes = client.get_queue_attributes(
        QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"]
    )
    assert attributes["Attributes"]["ApproximateNumberOfMessages"] == "0"


def test_missing_queue_returns_503(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        response = TestClient(create_app()).post("/webhooks/product", json=NOTICE)
    assert response.status_code == 503
    assert response.json()["detail"] == "not enqueued"
