"""Authoritative business persistence for the local prototype."""

from .database import Database, create_engine_and_session, init_database, session_scope
from .locks import CaseMutationCoordinator
from .repositories import (
    ConcurrentMutationError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    Repository,
    StorageNotFoundError,
)
from .unit_of_work import UnitOfWork

__all__ = [
    "CaseMutationCoordinator",
    "ConcurrentMutationError",
    "Database",
    "IdempotencyConflictError",
    "InvalidStateTransitionError",
    "Repository",
    "StorageNotFoundError",
    "UnitOfWork",
    "create_engine_and_session",
    "init_database",
    "session_scope",
]
