"""Small transaction boundary used by application services."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session

from .database import SessionFactory
from .repositories import Repository


class UnitOfWork:
    """Own one Session and expose one authoritative Repository."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None
        self.repository: Repository | None = None

    def __enter__(self) -> UnitOfWork:
        self.session = self._session_factory()
        self.repository = Repository(self.session)
        return self

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("UnitOfWork is not active")
        self.session.commit()

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return
        try:
            if exc_type is not None:
                self.session.rollback()
        finally:
            self.session.close()
            self.session = None
            self.repository = None
