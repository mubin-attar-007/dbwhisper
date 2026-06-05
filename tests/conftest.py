"""Shared pytest fixtures and test-time configuration.

Tests run network-free: the hosted embedding provider is selected by default and any
embedding/LLM construction is monkeypatched in the relevant tests. A dummy Postgres URL
keeps import-time config happy without a live database (no test here connects to one).
"""

from __future__ import annotations

import os

os.environ.setdefault("EMBEDDING_PROVIDER", "google")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "POSTGRES_CONNECTION_STRING",
    "postgresql+psycopg://test:test@localhost:5432/test",
)
os.environ.setdefault("LOG_SANITIZE", "1")

import pytest


@pytest.fixture(scope="session")
def client():
    """A FastAPI TestClient over the real app (no DB access for the routes tested)."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
