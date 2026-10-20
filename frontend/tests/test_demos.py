"""Demo questions stay runnable, and their route hint matches the rules."""

from chat_ui.demos import DEMOS, categories, in_category
from gateway_core.router import rules

PERSONAS = {"employee_eng", "hr_admin", "outsider", "product_lead"}
KEPT = {"parental-leave", "roadmap", "roadmap-outsider"}


def test_every_demo_has_a_known_persona_and_a_question() -> None:
    assert len(DEMOS) >= 10
    keys = [demo["key"] for demo in DEMOS]
    assert len(keys) == len(set(keys))
    assert KEPT <= set(keys)
    for demo in DEMOS:
        assert demo["persona"] in PERSONAS
        assert demo["question"].strip()
        assert demo["category"] in categories()
        assert demo["label"].strip()


def test_a_blank_question_would_fail_the_same_check() -> None:
    blank = {"persona": "employee_eng", "question": "  ", "key": "x", "category": "Policy"}
    assert not blank["question"].strip()


def test_route_hints_match_high_confidence_rules() -> None:
    for demo in DEMOS:
        route = rules(demo["question"])
        assert route.confidence == "high", demo["key"]
        assert route.kind == demo["route"], demo["key"]


def test_categories_group_the_selectbox() -> None:
    assert categories()[0] == "Policy"
    assert {item["key"] for item in in_category("Access")} == {
        "roadmap-outsider",
        "roadmap-employee",
    }
    assert in_category("missing") == []
