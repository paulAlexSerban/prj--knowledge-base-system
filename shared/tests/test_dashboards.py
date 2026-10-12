"""Dashboards stay describable, and every PromQL name is written down."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DASHBOARDS = ROOT / "observability" / "grafana" / "dashboards"
METRICS = (ROOT / "observability" / "metrics.md").read_text()
DATASOURCES = {"prometheus", "loki", "tempo"}
METRIC = re.compile(r"\b(kbs_[a-zA-Z0-9_:]+|up)\b")


def dashboard_problems(dashboard: dict, filename: str) -> list[str]:
    """Return convention breaks. An empty list means the file is acceptable."""
    found: list[str] = []
    name = dashboard.get("metadata", {}).get("name", "")
    stem = Path(filename).stem
    if not re.fullmatch(r"kbs-[a-z0-9-]+", name):
        found.append(f"{filename}: uid {name!r} is not kbs-<area>")
    if name != stem:
        found.append(f"{filename}: uid {name!r} does not match the file name")
    spec = dashboard.get("spec", {})
    title = spec.get("title", "")
    if not title.startswith("KBS / "):
        found.append(f"{filename}: title {title!r} does not start with 'KBS / '")
    tags = spec.get("tags") or []
    if "kbs" not in tags:
        found.append(f"{filename}: missing the kbs tag")
    for element_name, element in (spec.get("elements") or {}).items():
        if element.get("kind") != "Panel":
            continue
        panel = element.get("spec") or {}
        if not str(panel.get("title") or "").strip():
            found.append(f"{filename}: {element_name} has no title")
        if not str(panel.get("description") or "").strip():
            found.append(f"{filename}: {element_name} has no description")
        found.extend(_query_problems(filename, element_name, panel))
    return found


def _query_problems(filename: str, element_name: str, panel: dict) -> list[str]:
    found: list[str] = []
    queries = panel.get("data", {}).get("spec", {}).get("queries") or []
    for query in queries:
        body = query.get("spec", {}).get("query", {})
        datasource = body.get("datasource") or {}
        uid = datasource.get("uid") or datasource.get("name")
        if uid not in DATASOURCES:
            found.append(f"{filename}: {element_name} datasource {uid!r} is not known")
        expr = body.get("spec", {}).get("expr") or ""
        for metric in METRIC.findall(expr):
            canonical = _canonical(metric)
            if canonical not in METRICS:
                found.append(f"{filename}: {element_name} uses {canonical}, missing from metrics.md")
    return found


def _canonical(metric: str) -> str:
    for suffix in ("_bucket", "_sum", "_count"):
        if metric.startswith("kbs_") and metric.endswith(suffix):
            return metric[: -len(suffix)]
    return metric


def test_committed_dashboards_follow_the_convention() -> None:
    files = sorted(DASHBOARDS.glob("*.json"))
    assert files, "no dashboards to check"
    found: list[str] = []
    for path in files:
        found.extend(dashboard_problems(json.loads(path.read_text()), path.name))
    assert found == []


def test_a_panel_without_a_description_fails() -> None:
    dashboard = {
        "apiVersion": "dashboard.grafana.app/v2",
        "metadata": {"name": "kbs-fixture"},
        "spec": {
            "title": "KBS / Fixture",
            "tags": ["kbs"],
            "elements": {
                "panel-1": {
                    "kind": "Panel",
                    "spec": {
                        "title": "Bare panel",
                        "description": "",
                        "data": {"kind": "QueryGroup", "spec": {"queries": []}},
                    },
                }
            },
        },
    }
    found = dashboard_problems(dashboard, "kbs-fixture.json")
    assert any("description" in item for item in found)
