"""demo-harness reaches SQLite through SQLAlchemy, not the sqlite3 module."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_demo_harness_does_not_import_sqlite3():
    offenders: list[str] = []
    for path in ROOT.rglob("*.py"):
        if "egg-info" in path.parts or "__pycache__" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("import sqlite3", "from sqlite3")):
                offenders.append(str(path.relative_to(ROOT)))
                break
    assert offenders == []
