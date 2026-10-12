import pytest
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from harness_common.db import (
    Database,
    Invalid,
    check_acl,
    fetch_keyset,
    format_ts,
    parse_ts,
    utc_now,
)


class Base(DeclarativeBase):
    pass


class ObjectRow(Base):
    __tablename__ = "objects"

    id: Mapped[str] = mapped_column(primary_key=True)
    updated_at: Mapped[str]


def _load(tmp_path, rows: list[tuple[str, str]]) -> Database:
    database = Database(tmp_path / "objects.db")
    Base.metadata.create_all(database.engine)
    with database.session() as db:
        db.add_all(ObjectRow(id=object_id, updated_at=stamp) for object_id, stamp in rows)
    return database


def _pages(database: Database, updated_since: str | None = None) -> list[str]:
    found: list[str] = []
    token = None
    with database.session() as db:
        while True:
            page, token = fetch_keyset(
                db,
                ObjectRow,
                id_column=ObjectRow.id,
                updated_column=ObjectRow.updated_at,
                updated_since=updated_since,
                page_token=token,
                limit=1,
            )
            found.extend(row.id for row in page)
            if token is None:
                return found


def test_pages_share_a_timestamp_without_gaps_or_duplicates(tmp_path):
    stamp = "2026-06-01T00:00:00.000000Z"
    later = "2026-06-01T00:00:01.000000Z"
    database = _load(
        tmp_path,
        [("c", stamp), ("a", stamp), ("b", stamp), ("e", later), ("d", later)],
    )
    assert _pages(database) == ["a", "b", "c", "d", "e"]


def test_updated_since_is_inclusive_and_does_not_repeat_the_token(tmp_path):
    early = "2026-06-01T00:00:00.000000Z"
    bound = "2026-06-01T00:00:01.000000Z"
    later = "2026-06-01T00:00:02.000000Z"
    database = _load(tmp_path, [("a", early), ("b", bound), ("c", bound), ("d", later)])
    assert _pages(database, updated_since=bound) == ["b", "c", "d"]
    with database.session() as db:
        page, token = fetch_keyset(
            db,
            ObjectRow,
            id_column=ObjectRow.id,
            updated_column=ObjectRow.updated_at,
            updated_since=bound,
            limit=1,
        )
        assert page[0].id == "b"
        assert token == f"{bound}|b"
        rest, done = fetch_keyset(
            db,
            ObjectRow,
            id_column=ObjectRow.id,
            updated_column=ObjectRow.updated_at,
            page_token=token,
            limit=10,
        )
        assert [row.id for row in rest] == ["c", "d"]
        assert done is None


def test_bad_timestamp_and_page_token_are_rejected(tmp_path):
    database = _load(tmp_path, [])
    with database.session() as db:
        with pytest.raises(Invalid):
            fetch_keyset(
                db,
                ObjectRow,
                id_column=ObjectRow.id,
                updated_column=ObjectRow.updated_at,
                updated_since="yesterday",
            )
        with pytest.raises(Invalid):
            fetch_keyset(
                db,
                ObjectRow,
                id_column=ObjectRow.id,
                updated_column=ObjectRow.updated_at,
                page_token="not-a-token",
            )
        with pytest.raises(Invalid):
            fetch_keyset(
                db,
                ObjectRow,
                id_column=ObjectRow.id,
                updated_column=ObjectRow.updated_at,
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
