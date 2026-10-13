"""Classify a question before retrieval.

Rules decide. A model may break a tie only when the rules are unsure, a key is
set, and the model name is set. A model error or a label outside the allow-list
keeps the rules result. The label picks a template. It is not an authorization
decision and it is never turned into Cypher.
"""

import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from shared.logging import log_event

LOG = logging.getLogger("gateway")

RouteClass = Literal["semantic", "relational", "hybrid"]
Confidence = Literal["high", "low"]
Source = Literal["rules", "llm"]
GraphKind = Literal["roadmap_deals", "teammates", "team", "policies"]

# Bare "Q3" means the demo year. graph_read.DEFAULT_QUARTER is 2026-Q3.
_DEMO_YEAR = "2026"
_GRAPHS = {"roadmap_deals", "teammates", "team", "policies"}
_ENTITY = re.compile(r"^[A-Za-z0-9_.:@-]{1,200}$")
_QUARTER = re.compile(r"^\d{4}-Q[1-4]$")
_YEAR_QUARTER = re.compile(r"\b(20\d{2})\s*[- ]?\s*q([1-4])\b", re.IGNORECASE)
_SHORT_QUARTER = re.compile(r"\bq([1-4])\b", re.IGNORECASE)
_ARR_BEFORE = re.compile(r"\b(\d[\d,]*)\s*([km])?\s*arr\b", re.IGNORECASE)
# The number has to sit next to the word. A year in "2026 Q3" is not an ARR floor.
_ARR_AFTER = re.compile(
    r"\barr\b(?:\s+(?:above|over|of|at|least|minimum))+?\s+(\d[\d,]*)\s*([km])?\b",
    re.IGNORECASE,
)
_ARR_STATED = re.compile(
    r"\b(?:above|over|at least|minimum)\s+(\d[\d,]*)\s*([km])?\b",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b", re.IGNORECASE)
_SKIP_TOKENS = {"high-arr"}
_DEAL = re.compile(r"\bdeals?\b|\barr\b|\broadmap\b", re.IGNORECASE)
_LINK = re.compile(r"\bepics?\b|\bdepends?\b|\bunreleased\b|\bnot released\b", re.IGNORECASE)
_PARTIAL = re.compile(r"\b(?:deals?|epics?|arr|unreleased|roadmap)\b", re.IGNORECASE)
_EXPLAIN = re.compile(
    r"\b(?:explain|describe|why|wording|specs?|what does|what do)\b",
    re.IGNORECASE,
)
_TEAMMATES = re.compile(r"\bteammates?\b", re.IGNORECASE)
_TEAM = re.compile(
    r"\bteam members\b|\bmembers of (?:the )?team\b|\bwho is on (?:the )?team\b",
    re.IGNORECASE,
)
_POLICY_LIST = re.compile(r"\b(?:list|which) policies\b", re.IGNORECASE)
_POLICY_ACCESS = re.compile(
    r"\b(?:can i see|am i allowed|may i (?:see|read)|i am allowed)\b",
    re.IGNORECASE,
)

_PROMPT = (
    "Classify one company question. Reply with JSON only. "
    "route is semantic, relational, or hybrid. "
    "graph is roadmap_deals, teammates, team, policies, or null. "
    "hybrid is allowed only with roadmap_deals. "
    "teammates and team need entity_id. "
    "quarter is YYYY-Q[1-4] or null. min_arr is an integer or null. "
    "Do not write Cypher, SQL, or a permission decision."
)
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "route": {"type": "string", "enum": ["semantic", "relational", "hybrid"]},
        "graph": {"type": ["string", "null"], "enum": [*sorted(_GRAPHS), None]},
        "entity_id": {"type": ["string", "null"]},
        "quarter": {"type": ["string", "null"]},
        "min_arr": {"type": ["integer", "null"]},
    },
    "required": ["route", "graph", "entity_id", "quarter", "min_arr"],
}

Complete = Callable[[str], str]


class RouterError(ValueError):
    """The model reply is not one of the allowed labels."""


@dataclass(frozen=True)
class Route:
    kind: RouteClass
    graph: str | None = None
    entity_id: str | None = None
    quarter: str | None = None
    min_arr: int | None = None
    confidence: Confidence = "high"
    source: Source = "rules"

    def public(self) -> dict[str, object]:
        return {
            "class": self.kind,
            "source": self.source,
            "graph": self.graph,
            "quarter": self.quarter,
            "min_arr": self.min_arr,
        }


class Router:
    def __init__(self, complete: Complete | None = None) -> None:
        self.complete = complete

    @classmethod
    def from_env(cls) -> "Router":
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        model = os.environ.get("OPENROUTER_ROUTER_MODEL", "").strip()
        if not key or not model:
            return cls(None)
        return cls(lambda query: _openrouter(query, key, model))

    def classify(self, query: str) -> Route:
        ruled = rules(query)
        if ruled.confidence == "high" or self.complete is None:
            return ruled
        try:
            chosen = parse_model_json(self.complete(query))
        except (RouterError, OSError, TimeoutError, ValueError, TypeError, urllib.error.URLError):
            log_event(LOG, "router fell back to rules")
            return ruled
        return Route(
            chosen.kind,
            chosen.graph,
            chosen.entity_id,
            chosen.quarter,
            chosen.min_arr,
            "high",
            "llm",
        )


def rules(query: str) -> Route:
    """Best route from the text. Low confidence is a tie the model may break."""
    text = query.strip()
    if _DEAL.search(text) and _LINK.search(text):
        kind: RouteClass = "hybrid" if _EXPLAIN.search(text) else "relational"
        return Route(kind, "roadmap_deals", None, _quarter(text), _min_arr(text), "high", "rules")
    entity = _entity_id(text)
    if _TEAMMATES.search(text):
        if entity:
            return Route("relational", "teammates", entity, confidence="high")
        return Route("semantic", confidence="low")
    if _TEAM.search(text):
        if entity:
            return Route("relational", "team", entity, confidence="high")
        return Route("semantic", confidence="low")
    if _POLICY_LIST.search(text) and _POLICY_ACCESS.search(text):
        return Route("relational", "policies", confidence="high")
    if _PARTIAL.search(text):
        return Route("semantic", confidence="low")
    return Route("semantic", confidence="high")


def parse_model_json(raw: str) -> Route:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RouterError("not an object")
    kind = data.get("route")
    if kind not in {"semantic", "relational", "hybrid"}:
        raise RouterError("route")
    if kind == "semantic":
        return Route("semantic")
    graph = data.get("graph")
    if graph not in _GRAPHS:
        raise RouterError("graph")
    if kind == "hybrid" and graph != "roadmap_deals":
        raise RouterError("hybrid")
    entity_id = _optional_entity(data.get("entity_id"))
    if graph in {"teammates", "team"} and entity_id is None:
        raise RouterError("entity_id")
    return Route(
        kind,
        str(graph),
        entity_id,
        _optional_quarter(data.get("quarter")),
        _optional_arr(data.get("min_arr")),
    )


def _quarter(text: str) -> str | None:
    found = _YEAR_QUARTER.search(text)
    if found:
        return f"{found.group(1)}-Q{found.group(2)}"
    short = _SHORT_QUARTER.search(text)
    if short:
        return f"{_DEMO_YEAR}-Q{short.group(1)}"
    return None


def _min_arr(text: str) -> int | None:
    found = _ARR_BEFORE.search(text) or _ARR_AFTER.search(text) or _ARR_STATED.search(text)
    if found is None:
        return None
    return _scale(str(found.group(1)), found.group(2))


def _scale(number: str, suffix: str | None) -> int:
    value = int(number.replace(",", ""))
    if suffix is None:
        return value
    factor = 1_000 if suffix.lower() == "k" else 1_000_000
    return value * factor


def _entity_id(text: str) -> str | None:
    for match in _TOKEN.finditer(text):
        token = match.group(1)
        if token.lower() not in _SKIP_TOKENS:
            return token
    return None


def _optional_entity(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _ENTITY.fullmatch(value) is None:
        raise RouterError("entity_id")
    return value


def _optional_quarter(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _QUARTER.fullmatch(value) is None:
        raise RouterError("quarter")
    return value


def _optional_arr(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RouterError("min_arr")
    return value


def _openrouter(query: str, key: str, model: str) -> str:
    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": query[:500]},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "route", "strict": True, "schema": _SCHEMA},
        },
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        payload = json.loads(response.read().decode())
    content = payload["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise RouterError("content")
    return content
