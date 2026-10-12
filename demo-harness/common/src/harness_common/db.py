"""SQLAlchemy helpers shared by Sales, Product, and Engineering.

HR keeps its own store. The engine is SQLite. Timestamps are stored with
microseconds and a Z suffix so string order matches time order. Page tokens
are `updated_at|id`. The bound is inclusive, and the token is exclusive, so
a shared timestamp is not skipped and is not repeated.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import and_, create_engine, event, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import InstrumentedAttribute, Session

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


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


def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(
            f"sqlite:///{self.path}",
            connect_args={"timeout": 10},
        )
        event.listen(self.engine, "connect", _enable_foreign_keys)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with Session(self.engine) as db, db.begin():
            yield db


def split_page_token(page_token: str) -> tuple[str, str]:
    stamp, sep, object_id = page_token.partition("|")
    if not sep or not stamp or not object_id:
        raise Invalid("page_token must be updated_at|id")
    return parse_ts(stamp), object_id


def fetch_keyset[T](
    session: Session,
    model: type[T],
    *,
    id_column: InstrumentedAttribute[str],
    updated_column: InstrumentedAttribute[str],
    updated_since: str | None = None,
    page_token: str | None = None,
    limit: int = 100,
) -> tuple[list[T], str | None]:
    """One page of `model` ordered by (updated_at, id). The caller owns `session`.

    Ask for limit+1 to know if another page exists. `updated_since` is inclusive.
    A page token is exclusive and takes priority over `updated_since`.
    """
    if limit < 1:
        raise Invalid("limit must be at least 1")
    if page_token:
        stamp, object_id = split_page_token(page_token)
        window = or_(
            updated_column > stamp,
            and_(updated_column == stamp, id_column > object_id),
        )
    elif updated_since:
        window = updated_column >= parse_ts(updated_since)
    else:
        window = None
    statement = select(model).order_by(updated_column, id_column).limit(limit + 1)
    if window is not None:
        statement = statement.where(window)
    rows = list(session.scalars(statement))
    more = len(rows) > limit
    page = rows[:limit]
    if not more:
        return page, None
    last = page[-1]
    token = f"{getattr(last, updated_column.key)}|{getattr(last, id_column.key)}"
    return page, token
