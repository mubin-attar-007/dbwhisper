"""Per-IP rate limiting must not be steerable by a header the client controls.

Regression test for a defect the v2 audit found: ``X-Forwarded-For`` was trusted unconditionally,
so any caller could evade the limiter by sending a different value each request. The header is now
honoured only when ``TRUST_PROXY_HEADERS`` says a proxy you operate is in front.
"""

from __future__ import annotations

from starlette.requests import Request

from app.security.ratelimit import _client_ip


def _request(headers: dict[str, str], peer: str = "203.0.113.9") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 12345),
        }
    )


class TestUntrustedByDefault:
    def test_forwarded_for_is_ignored(self):
        request = _request({"x-forwarded-for": "1.2.3.4"})
        assert _client_ip(request, trust_proxy_headers=False) == "203.0.113.9"

    def test_real_ip_is_ignored(self):
        request = _request({"x-real-ip": "1.2.3.4"})
        assert _client_ip(request, trust_proxy_headers=False) == "203.0.113.9"

    def test_a_spoofing_client_cannot_change_its_bucket(self):
        buckets = {
            _client_ip(_request({"x-forwarded-for": f"10.0.0.{n}"}), trust_proxy_headers=False)
            for n in range(20)
        }
        assert buckets == {"203.0.113.9"}, "every request must land in the same bucket"


class TestTrustedProxy:
    def test_forwarded_for_is_honoured(self):
        request = _request({"x-forwarded-for": "1.2.3.4"})
        assert _client_ip(request, trust_proxy_headers=True) == "1.2.3.4"

    def test_the_first_hop_wins(self):
        request = _request({"x-forwarded-for": "1.2.3.4, 70.41.3.18, 150.172.238.178"})
        assert _client_ip(request, trust_proxy_headers=True) == "1.2.3.4"

    def test_real_ip_is_the_fallback(self):
        request = _request({"x-real-ip": "5.6.7.8"})
        assert _client_ip(request, trust_proxy_headers=True) == "5.6.7.8"

    def test_socket_peer_when_no_header_is_present(self):
        assert _client_ip(_request({}), trust_proxy_headers=True) == "203.0.113.9"


def test_a_request_with_no_client_is_not_a_crash():
    request = Request({"type": "http", "headers": []})
    assert _client_ip(request, trust_proxy_headers=False) == "unknown"
