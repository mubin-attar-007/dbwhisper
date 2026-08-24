"""Smoke tests for the HTTP surface that doesn't require a database."""

from __future__ import annotations


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_root_points_at_the_generated_documentation(client):
    """The root used to carry a hand-written endpoint list; it named 4 of 27 and had drifted.

    Pointing at the generated schema is the only version that cannot go stale, so the test now
    asserts the signposts rather than a list that a new route would silently invalidate.
    """
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "DBWhisper API"
    assert body["version"] == "0.1.0"
    for signpost in ("docs", "openapi", "health", "ready"):
        assert body[signpost].startswith("/"), signpost
    assert "endpoints" not in body, "a hand-maintained list is what drifted last time"


def test_the_signposts_the_root_advertises_actually_resolve(client):
    body = client.get("/").json()
    assert client.get(body["openapi"]).status_code == 200
    assert client.get(body["health"]).status_code == 200
    assert client.get(body["ready"]).status_code in (200, 503)


def test_ready_reports_checks(client):
    # The test DB is a dummy URL, so this is typically 503; assert the shape either way.
    response = client.get("/ready")
    assert response.status_code in (200, 503)
    body = response.json()
    assert "ready" in body
    assert set(body["checks"]) == {"postgres", "pgvector"}
