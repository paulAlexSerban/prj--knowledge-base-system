"""Streamlit chat page. Run with `streamlit run`."""

import logging
import os
import time
import urllib.parse

import streamlit as st

from chat_ui.answer import answer, citation, paths_as_hits, strip_markers
from chat_ui.demos import DEFAULT_PERSONAS, DEMOS, categories, in_category
from chat_ui.gateway import Gateway, GatewayError
from chat_ui.graph_view import hop_rows, show_graph
from chat_ui.metrics import PERSONAS, record_hops, record_turn, serve_metrics
from shared.logging import configure, log_event
from shared.telemetry import current_ids, init_telemetry, span

LOG = logging.getLogger("chat")
_CONFIGURED = False


def personas() -> list[str]:
    raw = os.environ.get("CHAT_PERSONAS", DEFAULT_PERSONAS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def switch_persona(state: dict, persona: str) -> None:
    """A different persona starts a new chat. The same persona keeps the history."""
    if state.get("persona") != persona:
        state["history"] = []
    state["persona"] = persona
    state["applied_persona"] = persona


def _hits(result: dict) -> list[dict]:
    """Chunks when the router searched. A path is the answer when it did not."""
    results = result.get("results") or []
    paths = result.get("paths") or []
    if results:
        return list(results)
    if paths:
        return paths_as_hits(paths)
    return []


@st.cache_resource
def gateway() -> Gateway:
    return Gateway(os.environ.get("GATEWAY_API_URL", "http://gateway-api:8000"))


@st.cache_resource
def _metrics_server() -> int:
    serve_metrics()
    return 9105


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


def _startup() -> None:
    global _CONFIGURED
    init_telemetry("chat-ui")
    if not _CONFIGURED:
        configure("chat-ui", "frontend")
        _CONFIGURED = True
    _metrics_server()


def tempo_url(trace_id: str) -> str:
    """Grafana Explore link. The browser opens it, so the host is not the container."""
    if len(trace_id) != 32 or any(char not in "0123456789abcdef" for char in trace_id):
        return ""
    base = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000").rstrip("/")
    panes = (
        '{"trace":{"datasource":"tempo","queries":[{"refId":"A","query":"'
        + trace_id
        + '","queryType":"traceql"}]}}'
    )
    return f"{base}/explore?schemaVersion=1&orgId=1&panes={urllib.parse.quote(panes)}"


def _caption(route: dict, mode: str, request_id: str, trace_id: str | None) -> str:
    text = (
        f"route: {route.get('class', 'unknown')} via {route.get('source', 'unknown')}, "
        f"mode: {mode}, request id: {request_id}"
    )
    if not trace_id:
        return text
    url = tempo_url(trace_id)
    if not url:
        return f"{text}, trace: {trace_id}"
    return f"{text}, trace: [{trace_id}]({url})"


def _render_turn(turn: dict) -> None:
    st.markdown(turn["text"])
    if turn.get("hits"):
        render_sources(turn["hits"])
    if turn.get("route"):
        show_graph(turn["route"], turn.get("paths") or [], turn.get("kind"))
    if turn.get("caption"):
        st.caption(turn["caption"])


def main() -> None:
    _startup()
    st.set_page_config(page_title="Knowledge chat", page_icon=None)
    st.title("Knowledge chat")
    _prepare(st.session_state)
    names = personas()

    st.sidebar.header("Demo questions")
    st.sidebar.caption(
        "A question sets the persona, then asks. Changing persona clears the chat."
    )
    category = st.sidebar.selectbox("Category", categories())
    choices = in_category(category)
    choice = st.sidebar.selectbox(
        "Question",
        choices,
        format_func=lambda item: item["label"],
        key=f"demo-{category}",
    )
    st.sidebar.caption(f"Persona {choice['persona']}. Expected route: {choice['route']}.")
    if st.sidebar.button("Ask this question"):
        switch_persona(st.session_state, choice["persona"])
        st.session_state["pending"] = choice["key"]
        st.rerun()

    persona = st.sidebar.selectbox("Act as", names, key="persona")
    st.sidebar.caption(
        "Dev personas. The gateway filters results by this persona's groups, "
        "clearance, and department. Try outsider to see an empty result."
    )

    for turn in st.session_state["history"]:
        with st.chat_message(turn["role"]):
            _render_turn(turn)

    pending_key = st.session_state.pop("pending", None)
    typed = st.chat_input("Ask about HR policy, for example parental leave in Germany")
    question = None
    if pending_key:
        demo = next(item for item in DEMOS if item["key"] == pending_key)
        question = demo["question"]
    elif typed:
        question = typed
    if not question:
        return

    st.session_state["history"].append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        started = time.perf_counter()
        hits: list[dict] = []
        paths: list = []
        route: dict = {}
        kind = None
        text = ""
        caption = ""
        mode = "error"
        outcome = "error"
        request_id = ""
        try:
            with span("chat.turn", surface="rest") as current:
                try:
                    with st.spinner("Searching"):
                        result = gateway().ask(persona, question)
                        hits = _hits(result)
                    with span("chat.answer") as answer_span:
                        text, mode = answer(
                            question,
                            hits,
                            os.environ.get("OPENROUTER_API_KEY", ""),
                            os.environ.get("OPENROUTER_MODEL", ""),
                        )
                        answer_span.set_attribute("mode", mode)
                    route = result.get("route") or {}
                    paths = list(result.get("paths") or [])
                    kind = result.get("kind") if isinstance(result.get("kind"), str) else None
                    outcome = "empty" if mode == "none" else "ok"
                    request_id = str(result.get("request_id") or "")
                    trace_id, _span_id = current_ids()
                    caption = _caption(route, mode, request_id, trace_id)
                    current.set_attribute("route", str(route.get("class") or "unknown"))
                    current.set_attribute("mode", mode)
                    log_event(
                        LOG,
                        "chat turn",
                        request_id=request_id,
                        persona=persona,
                        route=route.get("class"),
                        mode=mode,
                        outcome=outcome,
                        hops=len(hop_rows(paths)),
                    )
                except GatewayError as exc:
                    hits, paths, route, kind = [], [], {}, None
                    text = f"The gateway could not answer ({exc})."
                    mode = "error"
                    outcome = "error"
                    caption = ""
                    current.set_attribute("outcome", "error")
                    log_event(LOG, "chat turn failed", persona=persona, outcome="error")
                hops = len(hop_rows(paths))
                current.set_attribute("persona", persona if persona in PERSONAS else "other")
                current.set_attribute("outcome", outcome)
                current.set_attribute("hops", hops)
                current.set_attribute("request_id", request_id)
                st.markdown(text)
                if hits:
                    render_sources(hits)
                if route.get("class") in {"relational", "hybrid"}:
                    with span("chat.render_graph", hops=hops):
                        record_hops(hops)
                        show_graph(route, paths, kind)
                if caption:
                    st.caption(caption)
        finally:
            record_turn(
                persona=persona,
                route=str(route.get("class") or "unknown"),
                mode=mode,
                outcome=outcome,
                seconds=time.perf_counter() - started,
            )
    st.session_state["history"].append(
        {
            "role": "assistant",
            "text": text,
            "hits": hits,
            "caption": caption,
            "paths": paths,
            "route": route,
            "kind": kind,
        }
    )


if __name__ == "__main__":
    main()
