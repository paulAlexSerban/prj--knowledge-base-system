"""JSON log lines drop excluded fields and keep the audit decision in the body."""

import json
import logging
import re
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


def _hcl_string(value: str) -> str:
    inner = value.strip()
    assert inner[0] == '"' and inner[-1] == '"'
    decoded: list[str] = []
    index = 1
    while index < len(inner) - 1:
        if inner[index] == "\\" and index + 1 < len(inner) - 1:
            decoded.append(inner[index + 1])
            index += 2
            continue
        decoded.append(inner[index])
        index += 1
    return "".join(decoded)


def test_alloy_keeps_feature_logs_and_drops_probes() -> None:
    text = ALLOY_CONFIG.read_text()
    rule = text.split("Feature logs only", 1)[1].split("loki.source.docker", 1)[0]
    assert 'action        = "keep"' in rule
    for kept in (
        "gateway-api",
        "gateway-mcp",
        "webhook-receiver",
        "ingestion-worker",
        "connector-hr",
        "harness-hr",
        "chat-ui",
    ):
        assert kept in rule
    for dropped in ("loki", "grafana", "postgres", "neo4j", "floci", "jupyter", "alloy"):
        assert dropped not in rule
    gate = text.split('loki.relabel "features"', 1)[1].split('loki.process "kbs"', 1)[0]
    assert 'action        = "keep"' in gate
    assert "loki.relabel.features.receiver" in text
    expression = text.split("expression", 1)[1].split("\n", 1)[0].split("=", 1)[1]
    pattern = re.compile(_hcl_string(expression))
    assert pattern.search('INFO: 127.0.0.1 - "GET /health HTTP/1.1" 200 OK')
    assert pattern.search('INFO: 127.0.0.1 - "GET /metrics/ HTTP/1.1" 200 OK')
    assert pattern.search('{"path": "/metrics", "status": 200}')
    assert pattern.search('{"path":"/_stcore/health"}')
    assert pattern.search('INFO: 10.0.0.8 - "GET /changes?limit=200 HTTP/1.1" 200 OK') is None
    assert pattern.search('{"path": "/v1/search", "status": 200}') is None
    assert pattern.search('{"event": "connector.sync", "source_id": "sales"}') is None
    receiver = text.split('otelcol.receiver.otlp "kbs"', 1)[1].split(
        'otelcol.processor.filter "features"', 1
    )[0]
    assert "otelcol.processor.filter.features.input" in receiver
    filt = text.split('otelcol.processor.filter "features"', 1)[1].split("otelcol.processor.batch", 1)[0]
    assert "/(health|metrics)" in filt
    assert "^fastapi" in filt
    assert "connector.sync" not in filt


def test_traces_board_hides_probe_roots() -> None:
    dashboard = json.loads(
        (ROOT / "observability/grafana/dashboards/traces/kbs-traces.json").read_text()
    )
    elements = dashboard["spec"]["elements"]
    for key in ("panel-70", "panel-72"):
        query = elements[key]["spec"]["data"]["spec"]["queries"][0]["spec"]["query"]["spec"]["query"]
        assert "rootName !~" in query
        assert "/(health|metrics).*" in query
        assert "_stcore/health" in query
    slow = elements["panel-71"]["spec"]["data"]["spec"]["queries"][0]["spec"]["query"]["spec"]["query"]
    assert 'name = "retrieval"' in slow


def test_metric_relabel_copies_service_and_trust_area() -> None:
    text = ALLOY_CONFIG.read_text()
    block = text.split('prometheus.relabel "kbs"', 1)[1].split("otelcol.receiver", 1)[0]
    assert 'target_label  = "service"' in block
    assert 'replacement   = "access"' in block
    assert 'replacement   = "sources"' in block
    assert 'replacement   = "ingestion"' in block
    for forbidden in ("trace_id", "span_id", "principal", "request_id"):
        assert forbidden not in block
