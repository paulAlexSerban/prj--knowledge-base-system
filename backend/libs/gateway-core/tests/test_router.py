"""Rules classify first. A model may only break a low-confidence tie, and only with an allow-listed label."""

import json

import pytest

from gateway_core.graph_read import GraphAccess
from gateway_core.router import Router, RouterError, parse_model_json, rules
from gateway_core.service import GatewayService
from knowledge_store.acl import Principal

ROADMAP = "Which high-ARR Sales deals in Q3 depend on unreleased Engineering epics?"
POLICY = "What is our parental leave policy in Germany?"
LOOKALIKE = "Does the parental leave policy depend on manager approval in Germany?"


def test_parental_leave_stays_semantic() -> None:
    route = rules(POLICY)
    assert route.kind == "semantic"
    assert route.graph is None
    assert route.confidence == "high"
    assert route.source == "rules"


def test_a_policy_that_mentions_depend_stays_semantic() -> None:
    route = rules(LOOKALIKE)
    assert route.kind == "semantic"
    assert route.confidence == "high"


def test_roadmap_question_extracts_quarter_and_uses_the_template() -> None:
    route = rules(ROADMAP)
    assert route.kind == "relational"
    assert route.graph == "roadmap_deals"
    assert route.quarter == "2026-Q3"
    assert route.min_arr is None
    assert route.confidence == "high"


def test_year_quarter_and_arr_are_extracted() -> None:
    route = rules("deals in 2025 Q1 with ARR above 500,000 that depend on unreleased epics")
    assert route.graph == "roadmap_deals"
    assert route.quarter == "2025-Q1"
    assert route.min_arr == 500_000


def test_a_year_next_to_a_quarter_is_not_an_arr_floor() -> None:
    route = rules("What unreleased epics do our high ARR deals in 2026 Q3 depend on?")
    assert route.kind == "relational"
    assert route.graph == "roadmap_deals"
    assert route.quarter == "2026-Q3"
    assert route.min_arr is None


def test_a_k_suffix_scales_the_arr_floor() -> None:
    route = rules("500k ARR deals that depend on unreleased epics")
    assert route.min_arr == 500_000


def test_an_unknown_question_falls_back_to_semantic() -> None:
    route = rules("xylophone quantum payroll")
    assert route.kind == "semantic"
    assert route.confidence == "high"


def test_a_deal_without_an_epic_is_low_confidence_semantic() -> None:
    route = rules("which deals are open")
    assert route.kind == "semantic"
    assert route.confidence == "low"


def test_teammates_need_an_id_and_a_bare_mention_does_not_call_the_graph() -> None:
    named = rules("who are the teammates of emp-1")
    assert named.kind == "relational"
    assert named.graph == "teammates"
    assert named.entity_id == "emp-1"
    bare = rules("who are my teammates")
    assert bare.kind == "semantic"
    assert bare.confidence == "low"


def test_team_members_without_an_id_do_not_call_the_graph() -> None:
    route = rules("list the team members")
    assert route.kind == "semantic"
    assert route.graph is None


def test_an_explanatory_roadmap_question_is_hybrid() -> None:
    route = rules("Explain the specs for Q3 deals that depend on unreleased epics")
    assert route.kind == "hybrid"
    assert route.graph == "roadmap_deals"
    assert route.quarter == "2026-Q3"


def test_listing_policies_the_caller_may_see_uses_the_template() -> None:
    route = rules("which policies am I allowed to read")
    assert route.kind == "relational"
    assert route.graph == "policies"


def test_a_high_confidence_question_does_not_call_the_model() -> None:
    def complete(_query: str) -> str:
        raise AssertionError("model called")

    route = Router(complete).classify(POLICY)
    assert route.source == "rules"
    assert route.kind == "semantic"


def test_a_model_failure_falls_back_to_the_rules() -> None:
    def complete(_query: str) -> str:
        raise TimeoutError("slow")

    route = Router(complete).classify("which deals are open")
    assert route.source == "rules"
    assert route.kind == "semantic"
    assert route.confidence == "low"


def test_an_invalid_model_label_is_rejected() -> None:
    def complete(_query: str) -> str:
        return json.dumps({"route": "cypher", "graph": "MATCH (n) RETURN n"})

    route = Router(complete).classify("which deals are open")
    assert route.source == "rules"
    assert route.kind == "semantic"


def test_hybrid_is_rejected_for_anything_but_roadmap_deals() -> None:
    raw = json.dumps({"route": "hybrid", "graph": "teammates", "entity_id": "emp-1"})
    with pytest.raises(RouterError):
        parse_model_json(raw)
    route = Router(lambda _query: raw).classify("which deals are open")
    assert route.source == "rules"


def test_a_negative_arr_and_a_bare_quarter_are_rejected() -> None:
    with pytest.raises(RouterError):
        parse_model_json(
            json.dumps({"route": "relational", "graph": "roadmap_deals", "min_arr": -1})
        )
    with pytest.raises(RouterError):
        parse_model_json(
            json.dumps({"route": "relational", "graph": "roadmap_deals", "quarter": "Q3"})
        )
    with pytest.raises(RouterError):
        parse_model_json(
            json.dumps(
                {
                    "route": "relational",
                    "graph": "team",
                    "entity_id": None,
                    "quarter": None,
                    "min_arr": None,
                }
            )
        )


def test_a_valid_low_confidence_model_label_is_used() -> None:
    def complete(_query: str) -> str:
        return json.dumps(
            {
                "route": "relational",
                "graph": "roadmap_deals",
                "entity_id": None,
                "quarter": "2024-Q2",
                "min_arr": 10,
            }
        )

    route = Router(complete).classify("which deals are open")
    assert route.source == "llm"
    assert route.kind == "relational"
    assert route.graph == "roadmap_deals"
    assert route.quarter == "2024-Q2"
    assert route.min_arr == 10


def test_from_env_without_a_key_stays_on_the_rules(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_ROUTER_MODEL", raising=False)
    route = Router.from_env().classify("which deals are open")
    assert route.source == "rules"


def test_hybrid_searches_only_the_objects_on_the_path() -> None:
    class Graph:
        def related(self, kind, principal, entity_id, limit, quarter=None, min_arr=None):
            assert kind == "roadmap_deals"
            assert quarter == "2026-Q3"
            return [
                GraphAccess(
                    path={
                        "steps": [
                            {"type": "Deal", "id": "deal-1"},
                            {"type": "Epic", "id": "epic-1"},
                            {"type": "Team", "id": "payments"},
                        ]
                    },
                    source_id="sales",
                    object_id="deal-1",
                    allowed=True,
                    rule={"group": "roadmap", "department": "*", "clearance": "internal"},
                )
            ]

    class Chunks:
        def record_decisions(self, principal, request_id, decisions) -> None:
            return None

    class Store:
        def __init__(self) -> None:
            self.chunks = Chunks()
            self.contracts: dict = {}
            self.object_ids: list[str] | None = None

        def search(self, query, principal, limit=5, request_id="", object_ids=None):
            self.object_ids = object_ids
            return []

    store = Store()
    service = GatewayService(store, Graph(), router=Router())  # type: ignore[arg-type]
    principal = Principal("lead", ("roadmap",), "internal", "product")
    body = service.ask(
        principal,
        "Explain the specs for Q3 deals that depend on unreleased epics",
        5,
        "hybrid-1",
    )
    assert body["route"]["class"] == "hybrid"
    assert body["route"]["source"] == "rules"
    assert store.object_ids == ["deal-1", "epic-1"]
    assert body["results"] == []


def test_hybrid_does_not_search_the_corpus_when_the_graph_is_empty() -> None:
    class Graph:
        def related(self, kind, principal, entity_id, limit, quarter=None, min_arr=None):
            return []

    class Chunks:
        def record_decisions(self, principal, request_id, decisions) -> None:
            return None

    class Store:
        def __init__(self) -> None:
            self.chunks = Chunks()
            self.contracts: dict = {}
            self.called = False

        def search(self, query, principal, limit=5, request_id="", object_ids=None):
            self.called = True
            return []

    store = Store()
    service = GatewayService(store, Graph(), router=Router())  # type: ignore[arg-type]
    principal = Principal("out", (), "internal", "engineering")
    body = service.ask(
        principal,
        "Explain the specs for Q3 deals that depend on unreleased epics",
        5,
        "hybrid-empty",
    )
    assert body["paths"] == []
    assert body["results"] == []
    assert store.called is False
