"""The eval runner's scoring, without a stack."""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import kbs_client as kbs
import run


def _search_returns(monkeypatch, ids: list[str]) -> None:
    monkeypatch.setattr(
        kbs, "search", lambda persona, query, limit=5: [{"object_id": i} for i in ids]
    )


def test_golden_set_covers_the_four_required_categories() -> None:
    spec = yaml.safe_load(run.GOLDEN.read_text())
    categories = {case["category"] for case in spec["cases"]}
    assert {"known", "no_answer", "conflicting", "access"} <= categories
    assert len({case["id"] for case in spec["cases"]}) == len(spec["cases"])
    assert all(name in spec["thresholds"] for name in ("recall_at_5", "access", "no_answer"))


def test_known_case_records_rank(monkeypatch) -> None:
    _search_returns(monkeypatch, ["a", "b", "c"])
    result = run.run_case({"category": "known", "persona": "p", "query": "q", "expect": ["c"]})
    assert result["passed"] and result["rank"] == 3


def test_known_case_fails_when_the_chunk_is_missing(monkeypatch) -> None:
    _search_returns(monkeypatch, ["a"])
    assert not run.run_case({"category": "known", "persona": "p", "query": "q", "expect": ["z"]})[
        "passed"
    ]


def test_no_answer_case_fails_when_anything_is_returned(monkeypatch) -> None:
    _search_returns(monkeypatch, ["a"])
    assert not run.run_case({"category": "no_answer", "persona": "p", "query": "q"})["passed"]


def test_access_case_fails_when_a_forbidden_object_leaks(monkeypatch) -> None:
    _search_returns(monkeypatch, ["secret", "ok"])
    case = {"category": "access", "persona": "p", "query": "q", "expect_none_of": ["secret"]}
    assert not run.run_case(case)["passed"]


def test_conflicting_case_needs_every_source(monkeypatch) -> None:
    _search_returns(monkeypatch, ["de"])
    case = {"category": "conflicting", "persona": "p", "query": "q", "expect_all": ["de", "fr"]}
    assert not run.run_case(case)["passed"]


def test_thresholds_fail_below_and_above_limits() -> None:
    metrics = {"recall_at_5": 0.5, "latency_p95_ms": 900.0, "access": 1.0}
    bad = run.failed_thresholds(metrics, {"recall_at_5": 0.9, "latency_p95_ms": 500, "access": 1.0})
    assert len(bad) == 2


def test_eval_exits_2_when_the_gateway_is_down(monkeypatch) -> None:
    def down(*args, **kwargs):
        raise kbs.Failure("down")

    monkeypatch.setattr(kbs, "search", down)
    assert run.main([]) == 2


@pytest.mark.parametrize("ranks,expected", [([1, 2], 0.75), ([None], 0.0)])
def test_mrr(ranks, expected) -> None:
    cases = [{"category": "known"}] * len(ranks)
    results = [{"passed": r is not None, "rank": r, "ms": 1.0} for r in ranks]
    assert run.summarize(cases, results)["mrr"] == pytest.approx(expected)
