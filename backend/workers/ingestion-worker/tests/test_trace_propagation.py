"""traceparent on the queue joins the connector span to the worker span."""

from datetime import UTC, datetime

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from connector_core.queue import SqsPublisher
from ingest_contract.events import ChangeEvent
from ingestion_worker.consumer import Consumer
from ingestion_worker.store import MemoryStore
from shared.telemetry import current_ids, span


class _Queue:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def get_queue_url(self, QueueName: str) -> dict[str, str]:
        return {"QueueUrl": f"https://example/{QueueName}"}

    def send_message(self, **kwargs: object) -> None:
        self.sent.append(kwargs)

    def delete_message(self, **kwargs: object) -> None:
        return None


class _Remember(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.trace_id: str | None = None

    def upsert_object(self, event: ChangeEvent) -> None:
        self.trace_id = current_ids()[0]
        super().upsert_object(event)


def _event() -> ChangeEvent:
    return ChangeEvent(
        source_id="hr",
        object_id="emp-1",
        event_type="create",
        version="1",
        occurred_at=datetime.now(UTC),
        s3_pointer="raw/hr/person/emp-1.json",
    )


def _install(monkeypatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "shared.telemetry.trace.get_tracer",
        lambda *_args, **_kwargs: provider.get_tracer("kbs"),
    )
    return exporter


def _trace(finished, name: str) -> str:
    matched = [item for item in finished if item.name == name]
    assert len(matched) == 1
    assert "emp-1" not in str(matched[0].attributes)
    return format(matched[0].context.trace_id, "032x")


def test_traceparent_survives_publish_and_consume(monkeypatch) -> None:
    exporter = _install(monkeypatch)
    client = _Queue()
    publisher = SqsPublisher(client, "P0")
    with span("connector.sync", source_id="hr"):
        publisher.publish(_event())
    sent = client.sent[0]
    message = {
        "Body": sent["MessageBody"],
        "MessageAttributes": sent["MessageAttributes"],
        "ReceiptHandle": "rh",
        "MessageId": "mid",
        "Attributes": {"ApproximateReceiveCount": "1", "MessageGroupId": "hr"},
    }
    store = _Remember()
    assert Consumer(client, store)._handle_message("kbs-p0.fifo", message)
    finished = exporter.get_finished_spans()
    assert _trace(finished, "sqs.publish") == _trace(finished, "sqs.consume")
    assert store.trace_id == _trace(finished, "sqs.consume")


def test_missing_traceparent_starts_a_new_trace(monkeypatch) -> None:
    exporter = _install(monkeypatch)
    with span("other"):
        other = current_ids()[0]
    message = {
        "Body": _event().to_json(),
        "ReceiptHandle": "rh",
        "MessageId": "mid",
        "Attributes": {"ApproximateReceiveCount": "1"},
    }
    assert Consumer(_Queue(), MemoryStore())._handle_message("kbs-p0.fifo", message)
    assert _trace(exporter.get_finished_spans(), "sqs.consume") != other
