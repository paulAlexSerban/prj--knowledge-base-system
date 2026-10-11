"""SQLite and change-feed helpers shared by Sales, Product, and Engineering.

HR keeps its own SQLAlchemy store. Timestamps are stored with microseconds and
a Z suffix so string order matches time order. Page tokens are `updated_at|id`.
The bound is inclusive, and the token is exclusive, so a shared timestamp is
not skipped and is not repeated.
"""

import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Invalid(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_ts(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TS_FORMAT)


def parse_ts(value: str) -> str:
    """Normalize an ISO 8601 timestamp to the stored format. Naive input is UTC."""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as error:
        raise Invalid(f"not an ISO 8601 timestamp: {value!r}") from error
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return format_ts(moment)


def split_acl(value: str) -> list[str]:
    return [item for item in value.split(",") if item]


def check_acl(native_acl: list[str], allowed: frozenset[str]) -> str:
    if not native_acl:
        raise Invalid("native_acl needs at least one entry")
    unknown = set(native_acl) - allowed
    if unknown:
        raise Invalid(f"unknown native acl: {sorted(unknown)}")
    return ",".join(sorted(set(native_acl)))


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"not an identifier: {name}")
    return name


def split_page_token(page_token: str) -> tuple[str, str]:
    stamp, sep, object_id = page_token.partition("|")
    if not sep or not stamp or not object_id:
        raise Invalid("page_token must be updated_at|id")
    return parse_ts(stamp), object_id


def fetch_keyset(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: Sequence[str],
    updated_column: str = "updated_at",
    id_column: str = "id",
    updated_since: str | None = None,
    page_token: str | None = None,
    limit: int = 100,
    where: str = "",
    params: Sequence[object] = (),
) -> tuple[list[sqlite3.Row], str | None]:
    """One page ordered by (updated_at, id). Ask for limit+1 to know if another page exists."""
    if limit < 1:
        raise Invalid("limit must be at least 1")
    table_name = _ident(table)
    updated = _ident(updated_column)
    identity = _ident(id_column)
    selected = ", ".join(_ident(column) for column in columns)
    if page_token:
        stamp, object_id = split_page_token(page_token)
        clause = f"({updated} > ? OR ({updated} = ? AND {identity} > ?))"
        bound: list[object] = [stamp, stamp, object_id]
    elif updated_since:
        clause = f"{updated} >= ?"
        bound = [parse_ts(updated_since)]
    else:
        clause = "1 = 1"
        bound = []
    if where:
        clause = f"({clause}) AND ({where})"
        bound.extend(params)
    sql = (
        f"SELECT {selected} FROM {table_name} WHERE {clause} "
        f"ORDER BY {updated}, {identity} LIMIT ?"
    )
    rows = list(connection.execute(sql, [*bound, limit + 1]))
    more = len(rows) > limit
    page = rows[:limit]
    token = f"{page[-1][updated]}|{page[-1][identity]}" if more else None
    return page, token
