"""Chat metrics stay on a fixed label set. The HTTP server is not started."""

from chat_ui.metrics import HOPS, TURNS, record_hops, record_tokens, record_turn


def _turns(persona: str, route: str, mode: str, outcome: str) -> float:
    return TURNS.labels(persona=persona, route=route, mode=mode, outcome=outcome)._value.get()


def test_unknown_persona_is_other() -> None:
    before = _turns("other", "semantic", "none", "ok")
    record_turn(persona="alice", route="semantic", mode="none", outcome="ok", seconds=0.1)
    assert _turns("other", "semantic", "none", "ok") == before + 1


def test_known_labels_pass_through() -> None:
    before = _turns("employee_eng", "relational", "model", "ok")
    record_turn(
        persona="employee_eng",
        route="relational",
        mode="model",
        outcome="ok",
        seconds=0.2,
    )
    assert _turns("employee_eng", "relational", "model", "ok") == before + 1


def test_unknown_route_is_relabelled_and_a_foreign_token_kind_is_ignored() -> None:
    before = _turns("outsider", "unknown", "error", "error")
    record_turn(persona="outsider", route="free-text", mode="error", outcome="error", seconds=0)
    assert _turns("outsider", "unknown", "error", "error") == before + 1
    before_hops = HOPS._sum.get()
    record_hops(2)
    assert HOPS._sum.get() == before_hops + 2
    record_tokens("prompt", 0)
    record_tokens("secret", 5)


def test_a_taken_metrics_port_does_not_raise(monkeypatch) -> None:
    from chat_ui import metrics

    previous = metrics._STARTED
    metrics._STARTED = False

    def taken(_port: int) -> None:
        raise OSError("address already in use")

    monkeypatch.setattr(metrics, "start_http_server", taken)
    metrics.serve_metrics()
    metrics.serve_metrics()
    assert metrics._STARTED is True
    metrics._STARTED = previous
