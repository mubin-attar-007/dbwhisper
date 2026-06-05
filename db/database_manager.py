"""Centralized database engine and session helpers."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.model import Base

logger = logging.getLogger(__name__)

PROJECT_DB_CONNECTION_STRING = os.getenv("PROJECT_DB_CONNECTION_STRING") or os.getenv(
    "POSTGRES_CONNECTION_STRING"
)


def get_project_db_connection_string() -> str:
    if not PROJECT_DB_CONNECTION_STRING:
        raise RuntimeError("PROJECT_DB_CONNECTION_STRING environment variable is required.")
    return PROJECT_DB_CONNECTION_STRING


def _normalize_connection_string(connection_string: str) -> str:
    if connection_string.startswith("jdbc:sqlserver://"):
        rest = connection_string[len("jdbc:sqlserver://") :]
        host_port, _, params = rest.partition(";")
        host, _, port = host_port.partition(":")
        database = ""
        user = ""
        password = ""
        driver = "ODBC Driver 18 for SQL Server"
        for part in params.split(";"):
            if not part:
                continue
            key, _, value = part.partition("=")
            key = key.lower()
            if key == "databasename":
                database = value
            elif key == "user":
                user = value
            elif key == "password":
                password = value
            elif key == "driver":
                driver = value
        server_part = f"{host},{port}" if port else host
        odbc_parts = [
            f"DRIVER={driver}",
            f"SERVER={server_part}",
            f"DATABASE={database}",
            f"UID={user}",
            f"PWD={password}",
            "Encrypt=yes",
            "TrustServerCertificate=yes",
        ]
        return f"mssql+pyodbc:///?odbc_connect={quote_plus(';'.join(odbc_parts))}"
    return connection_string


@lru_cache(maxsize=8)
def get_engine(connection_string: str) -> Engine:
    """Retrieve a cached SQLAlchemy engine for the connection string and create all tables if needed."""
    normalized = _normalize_connection_string(connection_string)
    engine = create_engine(normalized, pool_pre_ping=True, pool_recycle=1800)
    return engine


def create_metadata_tables(connection_string: str) -> None:
    """Create the project metadata tables (idempotent)."""
    engine = get_engine(connection_string)
    try:
        Base.metadata.create_all(engine)
    except Exception as exc:
        from app.utils.logger import sanitize_for_log

        logger.warning(
            "Could not create metadata tables: %s", sanitize_for_log(str(exc), max_len=500)
        )


@lru_cache(maxsize=16)
def get_sessionmaker(connection_string: str):
    """Return a cached sessionmaker for the connection string."""
    engine = get_engine(connection_string)
    return sessionmaker(bind=engine)


def get_session(connection_string: str) -> Session:
    """Get a new SQLAlchemy session for the connection string."""
    SessionLocal = get_sessionmaker(connection_string)
    return SessionLocal()


def get_connection(connection_string: str):
    """Get a raw connection from the engine."""
    engine = get_engine(connection_string)
    return engine.connect()
