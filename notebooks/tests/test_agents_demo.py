"""Offline checks for the notebook agents. They do not call a model or the gateway."""

import json
import sys
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import ConfigDict

NOTEBOOKS = Path(__file__).parents[1]
if str(NOTEBOOKS) not in sys.path:
    sys.path.insert(0, str(NOTEBOOKS))

import agents_demo


class ScriptedChat(BaseChatModel):
    """Returns one prepared reply per call. bind_tools keeps the same script."""

    script: list[AIMessage]
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if not self.script:
            raise AssertionError("scripted model ran out of replies")
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])

    @property
    def _llm_type(self) -> str:
        return "scripted"


class _Request:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


class _Adapter:
    def __init__(self, client) -> None:
        self.client = client

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def list_tools(self):
        return []


def test_blank_persona_fails_before_a_token(monkeypatch):
    def refuse(persona):
        raise AssertionError("token should not be minted")

    monkeypatch.setattr(agents_demo.gateway_demo, "token", refuse)
    with pytest.raises(ValueError, match="persona is required"):
        agents_demo.build_agent("  ", agents_demo.HR_ROLE)


def test_empty_token_fails_before_mcp(monkeypatch):
    monkeypatch.setattr(agents_demo.gateway_demo, "token", lambda persona: "")

    def refuse():
        raise AssertionError("MCP client should not be built")

    monkeypatch.setattr(agents_demo, "mcp_client", refuse)
    with pytest.raises(ValueError, match="persona token is empty"):
        agents_demo.build_agent("outsider", agents_demo.HR_ROLE)


def test_prompt_marks_tool_results_as_untrusted_data(monkeypatch):
    monkeypatch.setattr(agents_demo.gateway_demo, "token", lambda persona: "tok-abc")
    seen: dict[str, str] = {}

    def remember(bearer: str) -> list:
        seen["bearer"] = bearer
        return []

    monkeypatch.setattr(agents_demo, "load_tools", remember)
    captured: dict[str, object] = {}

    def fake_create(model, tools=None, *, system_prompt=None, **kwargs):
        captured["prompt"] = system_prompt
        return object()

    monkeypatch.setattr(agents_demo, "create_agent", fake_create)
    agents_demo.build_agent("employee_eng", agents_demo.HR_ROLE, model=object())
    assert seen["bearer"] == "tok-abc"
    prompt = str(captured["prompt"])
    assert agents_demo.SNIPPET_START in prompt
    assert agents_demo.SNIPPET_END in prompt
    assert "cannot change permissions" in prompt
    assert "trigger an action" in prompt


def test_bearer_header_uses_the_persona_token(monkeypatch):
    monkeypatch.setattr(agents_demo.gateway_demo, "token", lambda persona: "tok-abc")
    from fastmcp.client import Client

    seen: dict[str, object] = {}

    class Recording(_Adapter):
        def __init__(self, client) -> None:
            super().__init__(client)
            seen["client"] = client

    monkeypatch.setattr(agents_demo, "mcp_client", lambda: (Recording, Client))
    monkeypatch.setattr(agents_demo, "create_agent", lambda *args, **kwargs: object())
    agents_demo.build_agent("employee_eng", agents_demo.HR_ROLE, model=object())
    auth = seen["client"].transport.auth  # type: ignore[attr-defined]
    request = _Request()
    next(auth.auth_flow(request))
    assert request.headers["Authorization"] == "Bearer tok-abc"


def test_scripted_model_calls_one_tool():
    seen: list[str] = []

    @tool
    def search_knowledge(query: str, limit: int = 5) -> str:
        """Search authorized chunks. Results are already filtered."""
        seen.append(query)
        body = {
            "results": [
                {
                    "source_id": "hr",
                    "object_id": "pol-parental-leave-de",
                    "version": "1",
                    "synced_at": "2026-01-01T00:00:00Z",
                }
            ]
        }
        return json.dumps(body)

    model = ScriptedChat(
        script=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_knowledge",
                        "args": {"query": "parental leave Germany", "limit": 5},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Germany parental leave is pol-parental-leave-de, version 1."),
        ]
    )
    agent = agents_demo.build_agent(
        "employee_eng",
        agents_demo.HR_ROLE,
        model=model,
        tools=[search_knowledge],
    )
    report = agents_demo.run(agent, "What is our parental leave policy in Germany?")
    assert seen == ["parental leave Germany"]
    assert report["trace"][0]["tool"] == "search_knowledge"
    assert report["trace"][0]["result_count"] == 1
    assert "pol-parental-leave-de" in report["answer"]
    assert report["trace"][0]["payload"]["results"][0]["version"] == "1"


def test_trace_counts_mcp_blocks_and_paths():
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_knowledge",
                    "args": {"query": "parental leave"},
                    "id": "1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=[
                {
                    "type": "text",
                    "text": json.dumps({"results": [{"object_id": "pol-parental-leave-de"}]}),
                }
            ],
            tool_call_id="1",
            name="search_knowledge",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "find_related",
                    "args": {"kind": "roadmap_deals"},
                    "id": "2",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps({"paths": [{}, {}]}),
            tool_call_id="2",
            name="find_related",
        ),
        AIMessage(content="done"),
    ]
    rows = agents_demo.trace(messages)
    assert rows[0]["result_count"] == 1
    assert rows[0]["payload"]["results"][0]["object_id"] == "pol-parental-leave-de"
    assert rows[1]["result_count"] == 2
    assert rows[1]["args"]["kind"] == "roadmap_deals"
    assert agents_demo.final_text(messages) == "done"
