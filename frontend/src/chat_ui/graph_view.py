"""Draw a gateway path. Unknown node kinds are drawn, not rejected.

The relationship sits on the destination step. Hop 1 is the first edge.
A step without a type and an id is skipped.
"""

import streamlit as st

KNOWN_TYPES = frozenset({"Deal", "Epic", "Team", "Person", "Policy"})


def path_dot(paths: list[dict]) -> str | None:
    """Graphviz DOT, or None when no step survived."""
    nodes: dict[tuple[str, str], str] = {}
    edges: list[tuple[str, str, str]] = []
    for path in paths:
        steps = clean_steps(path)
        if not steps:
            continue
        names = [_node(nodes, step) for step in steps]
        for index in range(1, len(steps)):
            edges.append((names[index - 1], names[index], f"{index} {_relation(steps[index])}"))
    if not nodes:
        return None
    lines = [
        "digraph paths {",
        '  bgcolor="#000000";',
        "  rankdir=LR;",
        (
            '  node [style=filled, fontcolor=white, fontname="Courier New", '
            'color="#1061B0", fillcolor="#1061B0"];'
        ),
        '  edge [color=white, fontcolor=white, fontname="Courier New"];',
    ]
    for (kind, ident), name in nodes.items():
        shape = "box" if kind in KNOWN_TYPES else "ellipse"
        lines.append(f"  {name} [shape={shape}, label={quote(kind + chr(10) + ident)}];")
    for source, dest, label in edges:
        lines.append(f"  {source} -> {dest} [label={quote(label)}];")
    lines.append("}")
    return "\n".join(lines)


def hop_rows(paths: list[dict]) -> list[dict]:
    """One row per edge: hop, from, relationship, to, source_id."""
    rows: list[dict] = []
    for path in paths:
        if not isinstance(path, dict):
            continue
        steps = clean_steps(path)
        path_source = path.get("source_id") if isinstance(path.get("source_id"), str) else ""
        for index in range(1, len(steps)):
            step = steps[index]
            source = step.get("source_id") if isinstance(step.get("source_id"), str) else path_source
            rows.append(
                {
                    "hop": index,
                    "from": _label(steps[index - 1]),
                    "relationship": _relation(step),
                    "to": _label(step),
                    "source_id": source,
                }
            )
    return rows


def show_graph(route: dict | None, paths: list | None, kind: str | None = None) -> None:
    """Expander for a graph or hybrid route. A semantic route draws nothing."""
    chosen = route or {}
    if chosen.get("class") not in {"relational", "hybrid"}:
        return
    drawn = list(paths or [])
    dot = path_dot(drawn)
    rows = hop_rows(drawn)
    template = chosen.get("graph") or kind or "none"
    with st.expander("Graph"):
        st.caption(f"template {template}, hops {len(rows)}")
        if dot:
            st.graphviz_chart(dot, width="stretch")
        if rows:
            st.dataframe(rows, hide_index=True)
        elif not dot:
            st.caption("No path was returned.")


def clean_steps(path: object) -> list[dict]:
    if not isinstance(path, dict):
        return []
    raw = path.get("steps")
    if not isinstance(raw, list):
        return []
    kept: list[dict] = []
    for step in raw:
        if not isinstance(step, dict):
            continue
        kind = step.get("type")
        ident = step.get("id")
        if not isinstance(kind, str) or not kind.strip():
            continue
        if not isinstance(ident, str) or not ident.strip():
            continue
        kept.append(step)
    return kept


def quote(value: object) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", "\\n").replace("\r", "")
    return '"' + text + '"'


def _node(nodes: dict[tuple[str, str], str], step: dict) -> str:
    key = (str(step["type"]), str(step["id"]))
    if key not in nodes:
        nodes[key] = f"n{len(nodes)}"
    return nodes[key]


def _relation(step: dict) -> str:
    relation = step.get("relationship")
    if isinstance(relation, str) and relation.strip():
        return relation.strip()
    return "RELATED"


def _label(step: dict) -> str:
    return f"{step['type']} {step['id']}"
