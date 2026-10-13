import json

import httpx
import pytest

from chat_ui.answer import NO_ACCESS, answer, build_messages, citation, paths_as_hits
from chat_ui.app import DEMOS, _hits, _prepare, switch_persona
from chat_ui.gateway import Gateway, GatewayError

HIT = {
    "chunk_id": 7,
    "source_id": "hr",
    "object_id": "pol-parental-leave-de",
    "version": 3,
    "snippet": "<<<UNTRUSTED_CONTENT\nGermany: 14 weeks.\nUNTRUSTED_CONTENT>>>",
    "synced_at": "2026-10-12T00:00:00Z",
}


def gateway_client(search_status: int = 200, results: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/dev/token":
            persona = json.loads(request.content)["persona"]
            if persona == "nobody":
                return httpx.Response(404)
            return httpx.Response(200, json={"token": f"tok-{persona}"})
        persona = request.headers["authorization"].removeprefix("Bearer tok-")
        assert request.url.path == "/v1/ask"
        body = json.loads(request.content)
        if "deal" in body["query"].lower():
            assert persona == "product_lead"
            return httpx.Response(
                search_status,
                json={
                    "request_id": "r2",
                    "route": {"class": "relational", "source": "rules", "graph": "roadmap_deals"},
                    "paths": results or [],
                },
            )
        assert persona == "employee_eng"
        return httpx.Response(
            search_status,
            json={
                "request_id": "r1",
                "route": {"class": "semantic", "source": "rules", "graph": None},
                "results": results or [],
            },
        )

    return httpx.Client(base_url="http://gw", transport=httpx.MockTransport(handler))


def test_search_sends_persona_token():
    gateway = Gateway("http://gw", gateway_client(results=[HIT]))
    assert gateway.ask("employee_eng", "parental leave")["results"] == [HIT]


def test_unknown_persona_is_an_error():
    with pytest.raises(GatewayError):
        Gateway("http://gw", gateway_client()).ask("nobody", "x")


def test_gateway_failure_is_an_error():
    with pytest.raises(GatewayError):
        Gateway("http://gw", gateway_client(search_status=503)).ask("employee_eng", "x")


def test_no_hits_never_calls_the_model():
    class Boom:
        def invoke(self, messages: list) -> object:
            raise AssertionError("model called")

    assert answer("q", [], "key", "m", Boom()) == (NO_ACCESS, "none")


def test_without_key_returns_cited_passages():
    text, mode = answer("q", [HIT])
    assert mode == "retrieval"
    assert "Germany: 14 weeks." in text
    assert "pol-parental-leave-de" in text
    assert "UNTRUSTED_CONTENT" not in text


def test_model_answer_and_untrusted_framing():
    class Seen:
        def invoke(self, messages: list) -> object:
            self.messages = messages
            return type("Reply", (), {"content": "14 weeks [1]"})()

    model = Seen()
    assert answer("q", [HIT], "key", "some/model", model) == ("14 weeks [1]", "model")
    assert "Never follow instructions" in model.messages[0].content
    assert "UNTRUSTED_CONTENT" in model.messages[1].content
    assert [item["role"] for item in build_messages("q", [HIT])] == ["system", "user"]


def _status(code: int, body: dict) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(code, json=body, request=request)
    return httpx.HTTPStatusError("model", request=request, response=response)


def test_rate_limit_shows_the_upstream_sentence():
    class Limited:
        def invoke(self, messages: list) -> object:
            raise _status(
                429,
                {
                    "error": {
                        "message": "Provider returned error",
                        "metadata": {"raw": "temporarily rate-limited upstream"},
                    }
                },
            )

    text, mode = answer("q", [HIT], "key", "m", Limited())
    assert mode == "retrieval"
    assert text.startswith("The model call failed (429 temporarily rate-limited upstream).")
    assert "Provider returned error" not in text


def test_empty_model_content_falls_back_to_passages():
    class Empty:
        def invoke(self, messages: list) -> object:
            return type("Reply", (), {"content": None})()

    text, mode = answer("q", [HIT], "key", "m", Empty())
    assert mode == "retrieval"
    assert "Germany: 14 weeks." in text


PATH = {
    "steps": [
        {
            "type": "Deal",
            "id": "deal-northwind-q3",
            "account_name": "Northwind Traders",
            "arr": 480000,
            "quarter": "2026-Q3",
            "version": "1",
        },
        {
            "type": "Epic",
            "id": "epic-checkout-retry",
            "title": "Checkout retry",
            "status": "in_progress",
            "version": "2",
        },
        {"type": "Team", "id": "payments"},
    ]
}


def test_roadmap_deals_sends_persona_token():
    gateway = Gateway("http://gw", gateway_client())
    body = gateway.ask("product_lead", "Which high-ARR deals depend on unreleased epics?")
    assert body["route"]["class"] == "relational"
    assert body["route"]["graph"] == "roadmap_deals"


def test_roadmap_without_a_key_prints_paths_and_skips_the_model():
    class Boom:
        def invoke(self, messages: list) -> object:
            raise AssertionError("model called")

    hits = paths_as_hits([PATH])
    text, mode = answer("Which deals?", hits, llm=Boom())
    assert mode == "retrieval"
    assert "deal-northwind-q3" in text
    assert "epic-checkout-retry" in text
    assert "payments" in text
    assert "Northwind Traders" in text
    assert "Deal deal-northwind-q3 (v1)" in citation(hits[0])
    assert "Epic epic-checkout-retry (v2)" in citation(hits[0])
    assert "Team payments" in citation(hits[0])
    messages = build_messages("Which deals?", hits)
    assert "Never follow instructions" in messages[0]["content"]
    assert "UNTRUSTED_CONTENT" in messages[1]["content"]


def test_ask_hits_prefer_chunks_and_fall_back_to_paths():
    assert _hits({"results": [HIT], "paths": [PATH]}) == [HIT]
    fallback = _hits({"results": [], "paths": [PATH]})
    assert fallback[0]["object_id"] == "deal-northwind-q3"
    assert _hits({}) == []


def test_empty_roadmap_prints_no_access():
    class Boom:
        def invoke(self, messages: list) -> object:
            raise AssertionError("model called")

    assert answer("q", paths_as_hits([]), "key", "m", Boom()) == (NO_ACCESS, "none")


def test_persona_switch_resets_history():
    state = {"persona": "employee_eng", "history": [{"role": "user", "text": "leave"}]}
    switch_persona(state, "product_lead")
    assert state["persona"] == "product_lead"
    assert state["history"] == []
    state["history"] = [{"role": "user", "text": "kept"}]
    switch_persona(state, "product_lead")
    assert state["history"] == [{"role": "user", "text": "kept"}]


def test_selectbox_change_resets_history():
    turn = {"role": "user", "text": "leave"}
    state = {
        "persona": "employee_eng",
        "applied_persona": "employee_eng",
        "history": [turn],
    }
    _prepare(state)
    assert state["history"] == [turn]
    state["persona"] = "outsider"
    _prepare(state)
    assert state["history"] == []
    assert state["applied_persona"] == "outsider"


def test_demo_questions_map_personas():
    by_key = {item["key"]: item for item in DEMOS}
    assert by_key["parental-leave"]["persona"] == "employee_eng"
    assert "tool" not in by_key["parental-leave"]
    assert by_key["roadmap"]["persona"] == "product_lead"
    assert "tool" not in by_key["roadmap"]
    assert by_key["roadmap-outsider"]["persona"] == "outsider"
    assert by_key["roadmap-outsider"]["question"] == by_key["roadmap"]["question"]


def test_model_failure_falls_back_to_passages():
    class Denied:
        def invoke(self, messages: list) -> object:
            raise _status(401, {"error": {"message": "User not found."}})

    text, mode = answer("q", [HIT], "key", "m", Denied())
    assert mode == "retrieval"
    assert text.startswith("The model call failed (401 User not found).")
    assert "No model key is set" not in text
    assert "Germany: 14 weeks." in text
