"""JSON log lines drop excluded fields and keep the audit decision in the body."""

import json
import logging
from pathlib import Path

from shared.logging import LABEL_KEYS, audit_decision, configure, log_event

ROOT = Path(__file__).resolve().parents[2]
ALLOY_CONFIG = ROOT / "observability" / "alloy" / "config.alloy"
LOKI_CONFIG = ROOT / "observability" / "loki" / "loki.yaml"


def test_audit_line_keeps_decision_in_the_body(capsys) -> None:
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    try:
        configure("gateway-api", "access")
        audit_decision(
            logging.getLogger("test-audit"),
            request_id="req-1",
            source_id="hr",
            principal="ada",
            decision="deny",
            attributes=["department"],
        )
    finally:
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)

    line = json.loads(capsys.readouterr().out.strip())
    assert line["decision"] == "deny"
    assert line["request_id"] == "req-1"
    assert line["principal"] == "ada"
    assert line["source_id"] == "hr"
    assert line["service"] == "gateway-api"
    assert line["trust_area"] == "access"
    assert line["trace_id"] is None
    assert line["span_id"] is None
    assert "request_id" not in LABEL_KEYS
    assert "principal" not in LABEL_KEYS
    assert "chunk_id" not in LABEL_KEYS
    assert "trace_id" not in LABEL_KEYS
    assert "span_id" not in LABEL_KEYS
    assert LABEL_KEYS == ("service", "trust_area", "level", "source_id")


def test_formatter_drops_excluded_fields(capsys) -> None:
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    try:
        configure("gateway-api", "access")
        log_event(
            logging.getLogger("test-audit"),
            "access decision",
            request_id="req-2",
            principal="ada",
            decision="deny",
            source_id="hr",
            chunk_id="chunk-1",
            salary=100,
            compensation=200,
            ssn="111",
            national_id="222",
            api_key="secret-key",
            authorization="Bearer secret",
            openrouter_api_key="sk-test",
            aws_secret_access_key="aws-secret",
            password="hunter2",
            retrieved_text="parental leave prose",
            chunk_text="more prose",
            snippet="wrapped prose",
            content="raw prose",
            nested={"ssn": "333", "Salary": 1, "region": "de"},
        )
    finally:
        root.handlers[:] = previous_handlers
        root.setLevel(previous_level)

    raw = capsys.readouterr().out.strip()
    line = json.loads(raw)
    assert line["decision"] == "deny"
    assert line["chunk_id"] == "chunk-1"
    assert line["nested"] == {"region": "de"}
    for forbidden in (
        "salary",
        "compensation",
        "ssn",
        "national_id",
        "api_key",
        "authorization",
        "openrouter_api_key",
        "aws_secret_access_key",
        "password",
        "retrieved_text",
        "chunk_text",
        "snippet",
        "content",
        "wrapped prose",
        "raw prose",
        "100",
        "200",
        "parental leave prose",
        "secret-key",
        "Bearer secret",
        "sk-test",
        "aws-secret",
        "hunter2",
        "111",
        "222",
        "333",
    ):
        assert forbidden not in raw


def test_alloy_does_not_label_principal_or_request() -> None:
    text = ALLOY_CONFIG.read_text()
    labeled = text.split("discovery.relabel", 1)[1].split("loki.write", 1)[0]
    labeled += text.split("stage.labels", 1)[1].split("}", 1)[0]
    for forbidden in ("principal", "request_id", "chunk_id", "user_id", "trace_id", "span_id"):
        assert forbidden not in labeled
    for required in ("service", "trust_area", "level", "source_id"):
        assert required in labeled


def test_loki_copies_service_for_logs_drilldown() -> None:
    text = LOKI_CONFIG.read_text()
    assert "discover_log_levels: true" in text
    assert "discover_service_name:" in text
    assert "- service" in text.split("discover_service_name:", 1)[1].split("analytics:", 1)[0]
    assert "discover_service_name: []" not in text
    alloy = ALLOY_CONFIG.read_text()
    labeled = alloy.split("stage.labels", 1)[1].split("}", 1)[0]
    for forbidden in ("request_id", "trace_id"):
        assert forbidden not in labeled


def test_metric_relabel_copies_service_and_trust_area() -> None:
    text = ALLOY_CONFIG.read_text()
    block = text.split('prometheus.relabel "kbs"', 1)[1].split("otelcol.receiver", 1)[0]
    assert 'target_label  = "service"' in block
    assert 'replacement   = "access"' in block
    assert 'replacement   = "sources"' in block
    assert 'replacement   = "ingestion"' in block
    for forbidden in ("trace_id", "span_id", "principal", "request_id"):
        assert forbidden not in block
