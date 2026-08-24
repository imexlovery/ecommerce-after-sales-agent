"""SQLAlchemy engine, session, and transaction helpers."""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base

SessionFactory = sessionmaker[Session]


@dataclass(frozen=True, slots=True)
class Database:
    """The two objects needed by the application composition root."""

    engine: Engine
    session_factory: SessionFactory

    def __iter__(self) -> Iterator[Engine | SessionFactory]:
        """Allow convenient ``engine, sessions = database`` unpacking."""

        yield self.engine
        yield self.session_factory


def _prepare_sqlite_path(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        return
    if url.database in {":memory:", ""} or url.database.startswith("file:"):
        return
    Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def create_engine_and_session(
    database_url: str,
    *,
    echo: bool = False,
) -> Database:
    """Create the business database engine and its session factory.

    SQLite foreign-key enforcement is enabled on every connection. In-memory
    databases use one shared connection so component tests can open multiple
    sessions without losing their schema.
    """

    _prepare_sqlite_path(database_url)
    url = make_url(database_url)
    engine_options: dict[str, object] = {"echo": echo, "future": True}
    if url.get_backend_name() == "sqlite":
        engine_options["connect_args"] = {"check_same_thread": False}
        if url.database in {None, "", ":memory:"}:
            engine_options["poolclass"] = StaticPool

    engine = create_engine(database_url, **engine_options)

    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    return Database(engine=engine, session_factory=factory)


def init_database(engine: Engine) -> None:
    """Create tables for tests and local bootstrap; releases use Alembic."""

    Base.metadata.create_all(engine)


@contextmanager
def session_scope(session_factory: SessionFactory) -> Generator[Session, None, None]:
    """Commit one business transaction or roll it back atomically."""

    session = session_factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
