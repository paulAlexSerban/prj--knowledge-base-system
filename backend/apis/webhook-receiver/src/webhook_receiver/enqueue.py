import os
import uuid

import boto3

from opentelemetry.trace import SpanKind
from ingest_contract.events import fit_sqs_id
from ingest_contract.hooks import HOOK_QUEUE, HookNotice

from shared.telemetry import span, sqs_message_attributes

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
            # Random, because content-based dedup is off. Two edits of one spec both enqueue.
            "MessageDeduplicationId": uuid.uuid4().hex,
        }
        attributes = sqs_message_attributes()
        if attributes:
            kwargs["MessageAttributes"] = attributes
        client.send_message(**kwargs)