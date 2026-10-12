"""Unit tests for the setup, seed, demo, and eval helpers. No stack needed."""

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import demo
import kbs_client as kbs
import seed


def test_deal_ids_reads_only_deal_steps() -> None:
    paths = [{"steps": [{"type": "Deal", "id": "d1"}, {"type": "Epic", "id": "e1"}]}]
    assert kbs.deal_ids(paths) == {"d1"}


def test_wait_until_times_out_with_label() -> None:
    with pytest.raises(kbs.Failure, match="never true"):
        kbs.wait_until(lambda: False, 0.05, "never true", every=0.01)


def test_wait_until_swallows_transient_http_errors() -> None:
    calls = {"n": 0}

    def flaky() -> bool:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("down")
        return True

    kbs.wait_until(flaky, 2, "flaky", every=0.01)
    assert calls["n"] == 3


def test_queue_backlog_sums_tier_queues_and_ignores_dlq(monkeypatch) -> None:
    text = (
        'kbs_queue_depth{queue="kbs-p0.fifo"} 2.0\n'
        'kbs_queue_depth{queue="kbs-p1.fifo"} 1.0\n'
        'kbs_dlq_depth{queue="kbs-p1-dlq.fifo"} 9.0\n'
    )
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(200, text=text))
    assert kbs.queue_backlog() == 3.0


def test_queue_backlog_is_none_when_metrics_unreachable(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", boom)
    assert kbs.queue_backlog() is None


def test_demo_step2_fails_when_outsider_gets_a_result(monkeypatch) -> None:
    monkeypatch.setattr(kbs, "search", lambda persona, query, limit=5: [{"object_id": "x"}])
    with pytest.raises(kbs.Failure, match="outsider received"):
        demo.step2()


def test_demo_step3_fails_when_a_hidden_deal_leaks(monkeypatch) -> None:
    leaked = [
        {"steps": [{"type": "Deal", "id": d}]}
        for d in sorted(demo.ROADMAP_VISIBLE | {"deal-contoso-q3"})
    ]
    monkeypatch.setattr(kbs, "related", lambda persona, kind, entity_id=None, limit=25: leaked)
    with pytest.raises(kbs.Failure, match="product_lead sees"):
        demo.step3()


def test_demo_exit_code_is_nonzero_when_stack_is_down(monkeypatch, capsys) -> None:
    def down(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", down)
    assert demo.main(["--quick"]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_seed_exit_code_is_nonzero_when_harness_is_down(monkeypatch, capsys) -> None:
    def down(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "request", down)
    assert seed.main() == 1
    assert "seed failed" in capsys.readouterr().err
