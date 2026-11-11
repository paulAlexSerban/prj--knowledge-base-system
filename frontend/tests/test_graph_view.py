"""Graphviz output. The widget itself is not started."""

from chat_ui.graph_view import hop_rows, path_dot

ROADMAP = {
    "steps": [
        {"type": "Deal", "id": "deal-1"},
        {"type": "Epic", "id": "epic-1", "relationship": "DEPENDS_ON"},
        {"type": "Team", "id": "payments", "relationship": "OWNED_BY"},
    ]
}


def test_deal_epic_team_path() -> None:
    dot = path_dot([ROADMAP])
    assert dot is not None
    assert 'bgcolor="#000000"' in dot
    assert 'fillcolor="#1061B0"' in dot
    assert "shape=box" in dot
    assert "Deal" in dot and "deal-1" in dot
    assert "Epic" in dot and "Team" in dot
    rows = hop_rows([ROADMAP])
    assert rows[0] == {
        "hop": 1,
        "from": "Deal deal-1",
        "relationship": "DEPENDS_ON",
        "to": "Epic epic-1",
        "source_id": "",
    }
    assert rows[1]["hop"] == 2
    assert rows[1]["relationship"] == "OWNED_BY"
    assert 'label="1 DEPENDS_ON"' in dot
    assert 'label="2 OWNED_BY"' in dot


def test_empty_path_returns_nothing() -> None:
    assert path_dot([]) is None
    assert path_dot([{"steps": []}]) is None
    assert path_dot([{"steps": "nope"}]) is None
    assert hop_rows([]) == []


def test_a_malformed_step_is_skipped() -> None:
    paths = [
        {
            "steps": [
                {"type": "Deal", "id": "d1"},
                "nope",
                {"type": "Team"},
                {"id": "missing-type"},
                {"type": "Epic", "id": "e1", "relationship": "DEPENDS_ON"},
            ]
        }
    ]
    dot = path_dot(paths)
    assert dot is not None
    assert "e1" in dot
    assert "nope" not in dot
    assert "missing-type" not in dot
    rows = hop_rows(paths)
    assert len(rows) == 1
    assert rows[0]["from"] == "Deal d1"
    assert rows[0]["to"] == "Epic e1"


def test_labels_are_escaped() -> None:
    paths = [
        {
            "steps": [
                {"type": "Deal", "id": 'a"b\\c'},
                {"type": "Epic", "id": "e1", "relationship": 'OWNS "X"'},
            ]
        }
    ]
    dot = path_dot(paths)
    assert dot is not None
    assert 'a\\"b\\\\c' in dot
    assert '1 OWNS \\"X\\""' in dot


def test_an_unknown_kind_uses_the_generic_shape() -> None:
    paths = [
        {
            "source_id": "sales",
            "steps": [
                {"type": "Widget", "id": "w1"},
                {"type": "Gadget", "id": "g1", "relationship": "LINKS"},
            ],
        }
    ]
    dot = path_dot(paths)
    assert dot is not None
    assert "shape=ellipse" in dot
    assert "Widget" in dot
    rows = hop_rows(paths)
    assert rows[0]["relationship"] == "LINKS"
    assert rows[0]["source_id"] == "sales"
