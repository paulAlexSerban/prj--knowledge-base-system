"""The seed notebook is the way an engineer loads a large source. CI checks the file, not a run."""

import json
from pathlib import Path

NOTEBOOKS = Path(__file__).parents[1]


def sources(name: str) -> str:
    notebook = json.loads((NOTEBOOKS / name).read_text(encoding="utf-8"))
    chunks = []
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            chunks.append("".join(cell["source"]))
    return "\n".join(chunks)


def test_seed_notebook_has_the_parameter_cell():
    text = sources("00-seed-source.ipynb")
    assert 'os.environ.get("SEED_SOURCE", "hr")' in text
    assert 'os.environ.get("SEED_KIND", "person")' in text
    assert 'os.environ.get("SEED_COUNT", "10000")' in text
    assert "SEED_VALUE" in text and "SEED_RESET" in text
    assert "/admin/seed" in text
    assert "next_page_token" in text


def test_snapshot_notebook_compares_counts():
    text = sources("02-inspect-snapshots.ipynb")
    assert "kbs-raw-snapshots" in text
    assert "pol-parental-leave-de" in text
    assert "drift" in text


def test_inspect_notebook_checks_forbidden_fields():
    text = sources("01-inspect-source.ipynb")
    assert "salary" in text and "national_id" in text
    assert "pol-parental-leave-de" in text
