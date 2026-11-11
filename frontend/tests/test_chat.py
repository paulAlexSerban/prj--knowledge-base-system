"""Persona switch and the hit picker. No Streamlit server."""

from chat_ui.answer import paths_as_hits
from chat_ui.app import _hits, _prepare, switch_persona


def test_switch_persona_clears_a_different_chat() -> None:
    state = {"persona": "employee_eng", "history": [{"role": "user", "text": "hi"}]}
    switch_persona(state, "outsider")
    assert state["history"] == []
    assert state["persona"] == "outsider"
    state["history"] = [{"role": "user", "text": "again"}]
    switch_persona(state, "outsider")
    assert state["history"] == [{"role": "user", "text": "again"}]


def test_prepare_clears_history_when_the_persona_changes() -> None:
    state = {
        "history": [{"role": "user", "text": "hi"}],
        "persona": "outsider",
        "applied_persona": "employee_eng",
    }
    _prepare(state)
    assert state["history"] == []
    assert state["applied_persona"] == "outsider"


def test_hits_prefer_chunks_and_fall_back_to_paths() -> None:
    chunk = {"chunk_id": 1, "snippet": "x"}
    path = {
        "steps": [
            {"type": "Deal", "id": "d"},
            {"type": "Epic", "id": "e"},
            {"type": "Team", "id": "t"},
        ]
    }
    assert _hits({"results": [chunk], "paths": [path]}) == [chunk]
    assert _hits({"paths": [path]}) == paths_as_hits([path])
    assert _hits({}) == []


def test_tempo_link_accepts_only_a_trace_id() -> None:
    from chat_ui.app import tempo_url

    link = tempo_url("a" * 32)
    assert link.startswith("http://127.0.0.1:3000/explore?")
    assert "a" * 32 in link
    assert tempo_url("not-a-trace") == ""
    assert tempo_url("a" * 31) == ""
