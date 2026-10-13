"""Product webhook receiver. Validates the pointer and enqueues it.

The production path is API Gateway plus Lambda. This process is the local stand-in.
It returns 202 only after SQS accepts the message. An invalid body is 422 and is
not enqueued. A queue failure is 503.
"""

import logging
import os
import uuid

import boto3
import uvicorn
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException, Request
from opentelemetry.trace import SpanKind
from prometheus_client import REGISTRY, Counter, make_asgi_app
from pydantic import ValidationError

from ingest_contract.events import fit_sqs_id
from ingest_contract.hooks import HOOK_QUEUE, HookNotice
from shared.http_metrics import HttpMetricsMiddleware
from shared.logging import configure, log_event
from shared.telemetry import FASTAPI_TELEMETRY, init_telemetry, span, sqs_message_attributes

LOG = logging.getLogger("webhook-receiver")


def _events() -> Counter:
    existing = REGISTRY._names_to_collectors.get("kbs_webhook_events_total")
    if isinstance(existing, Counter):
        return existing
    return Counter(
        "kbs_webhook_events_total",
        "Product webhook notices accepted or refused",
        ["result"],
    )


EVENTS = _events()

HTTP_ROUTES = ("/health", "/metrics", "/webhooks/product")


def create_app() -> FastAPI:
    app = FastAPI(title="Webhook receiver", version="0.2.0", telemetry=FASTAPI_TELEMETRY)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/product", status_code=202)
    async def product_change(request: Request) -> dict[str, object]:
        try:
            payload = await request.json()
            notice = HookNotice.model_validate(payload)
        except (ValueError, ValidationError) as exc:
            EVENTS.labels(result="invalid").inc()
            log_event(LOG, "rejected product webhook", level=logging.WARNING, error=str(exc))
            raise HTTPException(422, "invalid hook") from exc
        try:
            enqueue(notice)
        except (BotoCoreError, ClientError, OSError) as exc:
            EVENTS.labels(result="error").inc()
            log_event(
                LOG,
                "product webhook not enqueued",
                level=logging.ERROR,
                object_id=notice.object_id,
                error=str(exc),
            )
            raise HTTPException(503, "not enqueued") from exc
        EVENTS.labels(result="ok").inc()
        log_event(
            LOG,
            "enqueued product webhook",
            source_id="product",
            object_id=notice.object_id,
            event_type=notice.event_type,
            enqueued=True,
        )
        return {"accepted": True, "enqueued": True}

    app.mount("/metrics", make_asgi_app())
    app.add_middleware(HttpMetricsMiddleware, templates=HTTP_ROUTES)
    return app


def enqueue(notice: HookNotice) -> None:
    client = _sqs_client()
    url = client.get_queue_url(QueueName=HOOK_QUEUE)["QueueUrl"]
    with span(
        "sqs.publish",
        span_kind=SpanKind.PRODUCER,
        queue=HOOK_QUEUE,
        source_id="product",
        event_type=notice.event_type,
    ):
        kwargs: dict[str, object] = {
            "QueueUrl": url,
            "MessageBody": notice.to_json(),
            "MessageGroupId": fit_sqs_id(f"product:{notice.object_id}"),
            "MessageDeduplicationId": uuid.uuid4().hex,
        }
        attributes = sqs_message_attributes()
        if attributes:
            kwargs["MessageAttributes"] = attributes
        client.send_message(**kwargs)


def _sqs_client():
    kwargs = {
        "region_name": os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        "aws_access_key_id": os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        "aws_secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    }
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client("sqs", **kwargs)


app = create_app()


def main() -> None:
    configure("webhook-receiver", "sources")
    init_telemetry("webhook-receiver")
    uvicorn.run(app, host="0.0.0.0", port=8090, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
