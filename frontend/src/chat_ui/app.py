"""Streamlit chat page. Run with `streamlit run`."""

import os

import streamlit as st

from chat_ui.answer import answer, citation, paths_as_hits, strip_markers
from chat_ui.gateway import Gateway, GatewayError
from shared.telemetry import init_telemetry, span

DEFAULT_PERSONAS = "employee_eng,hr_admin,outsider,product_lead"
ROADMAP_QUESTION = (
    "Which high-ARR Sales deals in Q3 depend on unreleased Engineering epics?"
)
DEMOS = (
    {
        "key": "parental-leave",
        "label": "What is our parental leave policy in Germany?",
        "question": "What is our parental leave policy in Germany?",
        "persona": "employee_eng",
        "tool": "search",
    },
    {
        "key": "roadmap",
        "label": ROADMAP_QUESTION,
        "question": ROADMAP_QUESTION,
        "persona": "product_lead",
        "tool": "roadmap_deals",
    },
    {
        "key": "roadmap-outsider",
        "label": "Same roadmap question as an outsider",
        "question": ROADMAP_QUESTION,
        "persona": "outsider",
        "tool": "roadmap_deals",
    },
)


def personas() -> list[str]:
    raw = os.environ.get("CHAT_PERSONAS", DEFAULT_PERSONAS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def switch_persona(state: dict, persona: str) -> None:
    """A different persona starts a new chat. The same persona keeps the history."""
    if state.get("persona") != persona:
        state["history"] = []
    state["persona"] = persona
    state["applied_persona"] = persona


@st.cache_resource
def gateway() -> Gateway:
    return Gateway(os.environ.get("GATEWAY_API_URL", "http://gateway-api:8000"))


def render_sources(hits: list[dict]) -> None:
    with st.expander(f"Sources ({len(hits)})"):
        for i, hit in enumerate(hits, start=1):
            if hit.get("steps"):
                label = citation(hit)
            else:
                label = f"chunk {hit['chunk_id']}, {citation(hit)}"
            st.markdown(f"**[{i}]** {label}")
            st.text(strip_markers(hit["snippet"]))


def _prepare(state: dict) -> None:
    if "history" not in state:
        state["history"] = []
    names = personas()
    if state.get("persona") not in names:
        state["persona"] = names[0]
    if state.get("applied_persona") != state.get("persona"):
        if "applied_persona" in state:
            state["history"] = []
        state["applied_persona"] = state["persona"]


def main() -> None:
    init_telemetry("chat-ui")
    st.set_page_config(page_title="Knowledge chat", page_icon=None)
    st.title("Knowledge chat")
    _prepare(st.session_state)
    names = personas()

    st.sidebar.header("Demo questions")
    st.sidebar.caption(
        "A question sets the persona, then asks. Changing persona clears the chat."
    )
    for demo in DEMOS:
        if st.sidebar.button(demo["label"], key=demo["key"]):
            switch_persona(st.session_state, demo["persona"])
            st.session_state["pending"] = demo["key"]
            st.rerun()

    persona = st.sidebar.selectbox("Act as", names, key="persona")
    st.sidebar.caption(
        "Dev personas. The gateway filters results by this persona's groups, "
        "clearance, and department. Try outsider to see an empty result."
    )

    for turn in st.session_state["history"]:
        with st.chat_message(turn["role"]):
            st.markdown(turn["text"])
            if turn.get("hits"):
                render_sources(turn["hits"])
            if turn.get("caption"):
                st.caption(turn["caption"])

    pending_key = st.session_state.pop("pending", None)
    typed = st.chat_input("Ask about HR policy, for example parental leave in Germany")
    question = None
    tool = "search"
    if pending_key:
        demo = next(item for item in DEMOS if item["key"] == pending_key)
        question = demo["question"]
        tool = demo["tool"]
    elif typed:
        question = typed
    if not question:
        return

    st.session_state["history"].append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        try:
            with span("chat.turn", surface="rest"):
                with st.spinner("Searching"):
                    if tool == "roadmap_deals":
                        result = gateway().roadmap_deals(persona)
                        hits = paths_as_hits(result["paths"])
                    else:
                        result = gateway().search(persona, question)
                        hits = result["results"]
                text, mode = answer(
                    question,
                    hits,
                    os.environ.get("OPENROUTER_API_KEY", ""),
                    os.environ.get("OPENROUTER_MODEL", ""),
                )
                caption = f"mode: {mode}, request id: {result['request_id']}"
        except GatewayError as exc:
            hits, text, caption = [], f"The gateway could not answer ({exc}).", ""
        st.markdown(text)
        if hits:
            render_sources(hits)
        if caption:
            st.caption(caption)
    st.session_state["history"].append(
        {"role": "assistant", "text": text, "hits": hits, "caption": caption}
    )


if __name__ == "__main__":
    main()
