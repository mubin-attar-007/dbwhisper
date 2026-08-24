"""SSRF guard for target-database connection strings."""

from __future__ import annotations

import pytest

from app.platform.modes import NetworkPolicyLevel
from app.platform.network_policy import (
    HostTarget,
    NetworkPolicyError,
    check_target,
    classify,
    extract_host,
)

STRICT = NetworkPolicyLevel.PUBLIC_STRICT
PRIVATE = NetworkPolicyLevel.PRIVATE_ALLOWED
BUNDLED = NetworkPolicyLevel.BUNDLED_ONLY


class TestExtractHost:
    @pytest.mark.parametrize(
        ("dsn", "expected"),
        [
            (
                "postgresql+psycopg://u:p@db.example.com:5432/app",
                HostTarget("db.example.com", 5432),
            ),
            ("postgresql://u:p@10.0.0.5/app", HostTarget("10.0.0.5", None)),
            ("mysql+pymysql://u:p@mysql.internal:3306/shop", HostTarget("mysql.internal", 3306)),
            ("host=db.internal port=5432 dbname=app", HostTarget("db.internal", 5432)),
            (
                "jdbc:sqlserver://sql.example.com:1433;databaseName=x",
                HostTarget("sql.example.com", 1433),
            ),
            ("sqlite:///./local.db", None),
            ("duckdb:///:memory:", None),
            ("", None),
        ],
    )
    def test_parses(self, dsn: str, expected: HostTarget | None):
        assert extract_host(dsn) == expected

    def test_odbc_connect_form(self):
        dsn = (
            "mssql+pyodbc:///?odbc_connect=DRIVER%3D%7BODBC+Driver+18%7D%3B"
            "SERVER%3Dsql.example.com%2C1433%3BDATABASE%3Dapp"
        )
        assert extract_host(dsn) == HostTarget("sql.example.com", 1433)

    def test_ipv6_literal(self):
        assert extract_host("postgresql://u:p@[::1]:5432/app") == HostTarget("::1", 5432)


class TestClassify:
    @pytest.mark.parametrize(
        ("address", "kind"),
        [
            ("127.0.0.1", "loopback"),
            ("::1", "loopback"),
            ("10.1.2.3", "private"),
            ("192.168.1.10", "private"),
            ("172.16.0.1", "private"),
            ("169.254.169.254", "metadata"),
            ("169.254.1.1", "link_local"),
            ("100.100.100.200", "metadata"),
            ("8.8.8.8", "public"),
            ("0.0.0.0", "unspecified"),
        ],
    )
    def test_kinds(self, address: str, kind: str):
        assert classify(address) == kind


class TestStrictMode:
    def test_public_allowed(self):
        assert check_target("postgresql://u:p@8.8.8.8:5432/app", level=STRICT).allowed

    @pytest.mark.parametrize(
        "dsn",
        [
            "postgresql://u:p@127.0.0.1:5432/app",
            "postgresql://u:p@[::1]:5432/app",
            "postgresql://u:p@10.0.0.5:5432/app",
            "postgresql://u:p@192.168.0.9:5432/app",
            "postgresql://u:p@169.254.1.1:5432/app",
            "postgresql://u:p@0.0.0.0:5432/app",
        ],
    )
    def test_private_and_loopback_refused(self, dsn: str):
        decision = check_target(dsn, level=STRICT)
        assert not decision.allowed
        with pytest.raises(NetworkPolicyError):
            decision.raise_if_denied()

    def test_allowlist_permits_private_host(self):
        assert check_target(
            "postgresql://u:p@10.0.0.5:5432/app", level=STRICT, allowlist=["10.0.0.0/8"]
        ).allowed
        assert check_target(
            "postgresql://u:p@10.0.0.5:5432/app", level=STRICT, allowlist=["10.0.0.5"]
        ).allowed
        assert not check_target(
            "postgresql://u:p@10.0.0.5:5432/app", level=STRICT, allowlist=["10.9.0.0/16"]
        ).allowed


class TestMetadataAlwaysRefused:
    @pytest.mark.parametrize("level", [STRICT, PRIVATE])
    @pytest.mark.parametrize(
        "dsn",
        [
            "postgresql://u:p@169.254.169.254:5432/app",
            "postgresql://u:p@100.100.100.200:5432/app",
            "postgresql://u:p@metadata.google.internal:5432/app",
        ],
    )
    def test_refused(self, dsn: str, level: NetworkPolicyLevel):
        assert not check_target(dsn, level=level).allowed


class TestSelfHostedMode:
    @pytest.mark.parametrize(
        "dsn",
        [
            "postgresql://u:p@127.0.0.1:5432/app",
            "postgresql://u:p@10.0.0.5:5432/app",
            "postgresql://u:p@8.8.8.8:5432/app",
        ],
    )
    def test_private_allowed(self, dsn: str):
        assert check_target(dsn, level=PRIVATE).allowed


class TestBundledOnly:
    def test_only_bundled_hosts(self):
        assert check_target(
            "postgresql://u:p@demo-db:5432/app", level=BUNDLED, bundled_hosts=["demo-db"]
        ).allowed
        assert not check_target(
            "postgresql://u:p@evil.example.com:5432/app", level=BUNDLED, bundled_hosts=["demo-db"]
        ).allowed

    def test_sqlite_is_not_a_network_target(self):
        assert check_target("sqlite:///./demo.db", level=BUNDLED).allowed


class TestPorts:
    @pytest.mark.parametrize("port", [22, 25, 445, 2049])
    def test_non_database_ports_refused(self, port: int):
        decision = check_target(f"postgresql://u:p@8.8.8.8:{port}/app", level=STRICT)
        assert not decision.allowed and str(port) in decision.reason

    def test_database_ports_allowed(self):
        for port in (5432, 3306, 1433, 5433, 26257):
            assert check_target(f"postgresql://u:p@8.8.8.8:{port}/app", level=STRICT).allowed


def test_unresolvable_host_raises():
    with pytest.raises(NetworkPolicyError, match="Could not resolve"):
        check_target("postgresql://u:p@this-host-does-not-exist.invalid:5432/app", level=STRICT)
