"""Streamlit chat page. Run with `streamlit run`."""

import os

import streamlit as st

from chat_ui.answer import answer, citation, strip_markers
from chat_ui.gateway import Gateway, GatewayError

PERSONAS = os.environ.get("CHAT_PERSONAS", "employee_eng,hr_admin,outsider").split(",")


@st.cache_resource
def gateway() -> Gateway:
    return Gateway(os.environ.get("GATEWAY_API_URL", "http://gateway-api:8000"))


def render_sources(hits: list[dict]) -> None:
    with st.expander(f"Sources ({len(hits)})"):
        for i, hit in enumerate(hits, start=1):
            st.markdown(f"**[{i}]** chunk {hit['chunk_id']}, {citation(hit)}")
            st.text(strip_markers(hit["snippet"]))


def main() -> None:
    st.set_page_config(page_title="Knowledge chat", page_icon=None)
    st.title("Knowledge chat")

    persona = st.sidebar.selectbox("Act as", PERSONAS)
    st.sidebar.caption(
        "Dev personas. The gateway filters results by this persona's groups, "
        "clearance, and department. Try outsider to see an empty result."
    )
    if st.session_state.get("persona") != persona:
        st.session_state["persona"] = persona
        st.session_state["history"] = []

    for turn in st.session_state["history"]:
        with st.chat_message(turn["role"]):
            st.markdown(turn["text"])
            if turn.get("hits"):
                render_sources(turn["hits"])
            if turn.get("caption"):
                st.caption(turn["caption"])

    question = st.chat_input("Ask about HR policy, for example parental leave in Germany")
    if not question:
        return

    st.session_state["history"].append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Searching"):
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


main()
