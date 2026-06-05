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
