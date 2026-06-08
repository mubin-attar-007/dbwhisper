"""Smoke tests for the HTTP surface that doesn't require a database."""

from __future__ import annotations


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_root_lists_endpoints(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert "endpoints" in body
    assert any("/health" in key for key in body["endpoints"])


def test_ready_reports_checks(client):
    # The test DB is a dummy URL, so this is typically 503; assert the shape either way.
    response = client.get("/ready")
    assert response.status_code in (200, 503)
    body = response.json()
    assert "ready" in body
    assert set(body["checks"]) == {"postgres", "pgvector"}
