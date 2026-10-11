import sqlite3

import pytest

from harness_common.db import (
    Database,
    Invalid,
    check_acl,
    fetch_keyset,
    format_ts,
    parse_ts,
    utc_now,
)


def _load(tmp_path, rows: list[tuple[str, str]]) -> sqlite3.Connection:
    database = Database(tmp_path / "objects.db")
    with database.session() as connection:
        connection.execute(
            "CREATE TABLE objects (id TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
        )
        connection.executemany("INSERT INTO objects (id, updated_at) VALUES (?, ?)", rows)
    # fetch_keyset does not commit; hand it a read connection.
    opened = sqlite3.connect(database.path)
    opened.row_factory = sqlite3.Row
    return opened


def _pages(connection: sqlite3.Connection, updated_since: str | None = None) -> list[str]:
    found: list[str] = []
    token = None
    while True:
        page, token = fetch_keyset(
            connection,
            table="objects",
            columns=["id", "updated_at"],
            updated_since=updated_since,
            page_token=token,
            limit=1,
        )
        found.extend(row["id"] for row in page)
        if token is None:
            return found


def test_pages_share_a_timestamp_without_gaps_or_duplicates(tmp_path):
    stamp = "2026-06-01T00:00:00.000000Z"
    later = "2026-06-01T00:00:01.000000Z"
    connection = _load(
        tmp_path,
        [("c", stamp), ("a", stamp), ("b", stamp), ("e", later), ("d", later)],
    )
    assert _pages(connection) == ["a", "b", "c", "d", "e"]


def test_updated_since_is_inclusive_and_does_not_repeat_the_token(tmp_path):
    early = "2026-06-01T00:00:00.000000Z"
    bound = "2026-06-01T00:00:01.000000Z"
    later = "2026-06-01T00:00:02.000000Z"
    connection = _load(tmp_path, [("a", early), ("b", bound), ("c", bound), ("d", later)])
    assert _pages(connection, updated_since=bound) == ["b", "c", "d"]
    page, token = fetch_keyset(
        connection,
        table="objects",
        columns=["id", "updated_at"],
        updated_since=bound,
        limit=1,
    )
    assert page[0]["id"] == "b"
    assert token == f"{bound}|b"
    rest, done = fetch_keyset(
        connection,
        table="objects",
        columns=["id", "updated_at"],
        page_token=token,
        limit=10,
    )
    assert [row["id"] for row in rest] == ["c", "d"]
    assert done is None


def test_bad_timestamp_and_page_token_are_rejected(tmp_path):
    connection = _load(tmp_path, [])
    with pytest.raises(Invalid):
        fetch_keyset(
            connection,
            table="objects",
            columns=["id", "updated_at"],
            updated_since="yesterday",
        )
    with pytest.raises(Invalid):
        fetch_keyset(
            connection,
            table="objects",
            columns=["id", "updated_at"],
            page_token="not-a-token",
        )
    with pytest.raises(Invalid):
        fetch_keyset(
            connection,
            table="objects",
            columns=["id", "updated_at"],
            limit=0,
        )


def test_acl_rejects_empty_and_unknown():
    allowed = frozenset({"project_member"})
    assert check_acl(["project_member", "project_member"], allowed) == "project_member"
    with pytest.raises(Invalid):
        check_acl([], allowed)
    with pytest.raises(Invalid):
        check_acl(["nope"], allowed)


def test_parse_ts_normalizes_and_rejects():
    assert parse_ts("2026-06-01T00:00:00Z") == "2026-06-01T00:00:00.000000Z"
    assert parse_ts("2026-06-01T00:00:00+00:00") == "2026-06-01T00:00:00.000000Z"
    with pytest.raises(Invalid):
        parse_ts("June")
    assert format_ts(utc_now()).endswith("Z")
