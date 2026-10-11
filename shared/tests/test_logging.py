"""JSON log lines drop excluded fields and keep the audit decision in the body."""

import json
import logging
from pathlib import Path

from shared.logging import LABEL_KEYS, audit_decision, configure, log_event

ALLOY_CONFIG = Path(__file__).resolve().parents[2] / "observability" / "alloy" / "config.alloy"


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
    assert "request_id" not in LABEL_KEYS
    assert "principal" not in LABEL_KEYS
    assert "chunk_id" not in LABEL_KEYS
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
            retrieved_text="parental leave prose",
            chunk_text="more prose",
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
        "retrieved_text",
        "chunk_text",
        "100",
        "200",
        "parental leave prose",
        "secret-key",
        "Bearer secret",
        "sk-test",
        "aws-secret",
        "111",
        "222",
        "333",
    ):
        assert forbidden not in raw


def test_alloy_does_not_label_principal_or_request() -> None:
    text = ALLOY_CONFIG.read_text()
    labeled = text.split("discovery.relabel", 1)[1].split("loki.write", 1)[0]
    labeled += text.split("stage.labels", 1)[1].split("}", 1)[0]
    for forbidden in ("principal", "request_id", "chunk_id", "user_id"):
        assert forbidden not in labeled
    for required in ("service", "trust_area", "level", "source_id"):
        assert required in labeled
