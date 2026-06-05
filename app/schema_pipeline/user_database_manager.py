"""Database connection helpers."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from db.connection import normalize_connection_string


def _normalize_jdbc_connection_string(connection_string: str) -> str:
    return normalize_connection_string(connection_string)


@lru_cache(maxsize=8)
def _engine_cache(connection_string: str) -> Engine:
    normalized = _normalize_jdbc_connection_string(connection_string)
    return create_engine(normalized, pool_pre_ping=True, pool_recycle=1800)


def get_engine(connection_string: str) -> Engine:
    """Retrieve a cached SQLAlchemy engine for the connection string."""

    return _engine_cache(connection_string)


def get_connection(connection_string: str):
    """Return a context manager for a SQLAlchemy connection."""

    engine = get_engine(connection_string)
    return engine.connect()
