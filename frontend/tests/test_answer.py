"""The answer chain. No test calls OpenRouter."""

import httpx
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableLambda

from chat_ui.answer import (
    NO_ACCESS,
    AnswerCallback,
    answer,
    paths_as_hits,
    retrieval_only,
)
from chat_ui.metrics import TOKENS

SNIPPET = "<<<UNTRUSTED_CONTENT\nGermany grants leave.\nUNTRUSTED_CONTENT>>>"
HITS = [
    {
        "chunk_id": 1,
        "source_id": "hr",
        "object_id": "pol-1",
        "version": "1",
        "synced_at": "t",
        "snippet": SNIPPET,
    }
]
ROADMAP = [
    {
        "steps": [
            {
                "type": "Deal",
                "id": "deal-1",
                "account_name": "North",
                "arr": 200000,
                "quarter": "2026-Q3",
                "version": "1",
            },
            {
                "type": "Epic",
                "id": "epic-1",
                "title": "Retry",
                "status": "in_progress",
                "version": "2",
                "relationship": "DEPENDS_ON",
            },
            {"type": "Team", "id": "payments", "relationship": "OWNED_BY"},
        ]
    }
]
TEAMMATES = [
    {
        "steps": [
            {"type": "Person", "id": "emp-000001"},
            {"type": "Team", "id": "platform", "relationship": "BELONGS_TO"},
            {
                "type": "Person",
                "id": "emp-000002",
                "name": "Grace",
                "relationship": "BELONGS_TO",
            },
        ]
    }
]


def test_no_hits_is_the_access_sentence() -> None:
    text, mode = answer("parental leave", [])
    assert text == NO_ACCESS
    assert mode == "none"


def test_no_key_falls_back_to_passages() -> None:
    text, mode = answer("parental leave", HITS, api_key="", model="")
    assert mode == "retrieval"
    assert text == retrieval_only(HITS)
    assert "Germany grants leave." in text
    assert "<<<UNTRUSTED_CONTENT" not in text.split("passages:", 1)[-1]


def test_fake_model_writes_the_answer() -> None:
    llm = FakeListChatModel(responses=["Germany grants 14 weeks [1]."])
    text, mode = answer("parental leave", HITS, llm=llm)
    assert mode == "model"
    assert text == "Germany grants 14 weeks [1]."


def test_empty_model_text_falls_back() -> None:
    llm = FakeListChatModel(responses=["   "])
    text, mode = answer("parental leave", HITS, llm=llm)
    assert mode == "retrieval"
    assert text.startswith("The model call failed.")
    assert "Germany grants leave." in text


def test_a_raised_model_falls_back_to_passages() -> None:
    def _boom(_value: object) -> str:
        raise RuntimeError("down")

    text, mode = answer("parental leave", HITS, llm=RunnableLambda(_boom))
    assert mode == "retrieval"
    assert text.startswith("The model call failed.")
    assert "Germany grants leave." in text


def test_provider_status_is_named() -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(429, json={"error": {"message": "slow down"}}, request=request)

    def _boom(_value: object) -> str:
        raise httpx.HTTPStatusError("nope", request=request, response=response)

    text, mode = answer("parental leave", HITS, llm=RunnableLambda(_boom))
    assert mode == "retrieval"
    assert "429" in text
    assert "slow down" in text


def test_roadmap_without_a_key_is_a_table() -> None:
    hits = paths_as_hits(ROADMAP)
    text, mode = answer("deals", hits)
    assert mode == "retrieval"
    assert "deal-1" in text
    assert "epic-1" in text
    assert text.startswith("No model key is set, so these are the paths:")


def test_teammates_path_does_not_require_a_deal() -> None:
    hits = paths_as_hits(TEAMMATES)
    assert hits[0]["source_id"] == "graph"
    text, mode = answer("teammates", hits)
    assert mode == "retrieval"
    assert "emp-000001" in text
    assert "Grace" in text


def test_callback_records_token_counts() -> None:
    before_prompt = TOKENS.labels(kind="prompt")._value.get()
    before_completion = TOKENS.labels(kind="completion")._value.get()

    message = type("Message", (), {})()
    message.usage_metadata = {"input_tokens": 3, "output_tokens": 4}
    generation = type("Generation", (), {})()
    generation.message = message
    response = type("Response", (), {})()
    response.generations = [[generation]]

    AnswerCallback().on_llm_end(response)
    assert TOKENS.labels(kind="prompt")._value.get() == before_prompt + 3
    assert TOKENS.labels(kind="completion")._value.get() == before_completion + 4
