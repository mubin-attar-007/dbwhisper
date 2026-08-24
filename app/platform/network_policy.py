"""Where DBWhisper is allowed to open a database connection.

A product that accepts arbitrary connection strings is a server-side request forgery (SSRF) primitive
unless the target is checked first: a "database host" can just as easily be the cloud metadata
endpoint, a loopback admin port, or an internal service. This module resolves the host and decides,
before SQLAlchemy ever dials out.

The decision depends on the application mode (see :mod:`app.platform.modes`):

* ``BUNDLED_ONLY`` (demo) - nothing may be connected to except the bundled sample data sources.
* ``PRIVATE_ALLOWED`` (self-hosted) - loopback and RFC1918 are fine; that is the whole point of
  running it on your own network. Cloud metadata endpoints are still refused.
* ``PUBLIC_STRICT`` (production) - public addresses only, plus an optional explicit allowlist for
  private hosts the operator has deliberately permitted.

Every host in the DNS answer is checked, not just the first, and the resolved addresses are returned
so the caller can pin them - a name that resolves to a public address now and to 127.0.0.1 on the
next lookup (DNS rebinding) is the attack this defends against.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from app.platform.modes import NetworkPolicyLevel

#: Cloud instance-metadata services. Refused in every mode, including self-hosted: there is no
#: legitimate reason for a *database* connection to target one, and reaching them yields credentials.
METADATA_ADDRESSES: frozenset[str] = frozenset(
    {
        "169.254.169.254",  # AWS / GCP / Azure / DigitalOcean IMDS
        "169.254.170.2",  # AWS ECS task metadata
        "100.100.100.200",  # Alibaba Cloud
        "192.0.0.192",  # Oracle Cloud
        "fd00:ec2::254",  # AWS IMDSv6
    }
)

METADATA_HOSTNAMES: frozenset[str] = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "metadata",
    }
)

#: Ports that are never a database. Blocking them turns "point it at a database" into something that
#: cannot be repurposed into a generic port scanner or SMTP relay.
BLOCKED_PORTS: frozenset[int] = frozenset({22, 23, 25, 53, 111, 135, 139, 445, 465, 587, 2049})


class NetworkPolicyError(ValueError):
    """A connection target is not permitted by the active network policy."""


@dataclass(frozen=True, slots=True)
class HostTarget:
    host: str
    port: int | None = None

    def __str__(self) -> str:
        return f"{self.host}:{self.port}" if self.port else self.host


@dataclass(slots=True)
class NetworkDecision:
    allowed: bool
    target: HostTarget | None
    reason: str
    resolved_addresses: list[str] = field(default_factory=list)
    level: NetworkPolicyLevel = NetworkPolicyLevel.PUBLIC_STRICT

    def raise_if_denied(self) -> None:
        if not self.allowed:
            raise NetworkPolicyError(self.reason)


# ---------------------------------------------------------------------------------------------
# Parsing connection strings
# ---------------------------------------------------------------------------------------------


def extract_host(connection_string: str) -> HostTarget | None:
    """Best-effort host/port extraction from a SQLAlchemy URL or an ODBC connection string.

    Returns ``None`` for targets that have no network host at all (SQLite files, in-memory DuckDB),
    which callers treat as "no network policy applies".
    """
    raw = (connection_string or "").strip()
    if not raw:
        return None

    lowered = raw.lower()
    if lowered.startswith(("sqlite", "duckdb")):
        return None

    # ODBC style: mssql+pyodbc:///?odbc_connect=DRIVER%3D...%3BSERVER%3Dhost%2C1433%3B...
    if "odbc_connect=" in lowered:
        odbc = unquote(raw.split("odbc_connect=", 1)[1])
        for part in odbc.split(";"):
            key, _, value = part.partition("=")
            if key.strip().lower() in {"server", "address", "addr", "data source"}:
                return _split_mssql_server(value.strip())
        return None

    # JDBC style, normalised elsewhere but accepted here too.
    if lowered.startswith("jdbc:sqlserver://"):
        rest = raw[len("jdbc:sqlserver://") :]
        return _split_mssql_server(rest.split(";", 1)[0])

    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.hostname:
        return HostTarget(host=parts.hostname, port=parts.port)

    # Key/value DSN: "host=db.internal port=5432 dbname=app"
    kv = {}
    for token in raw.replace(";", " ").split():
        key, sep, value = token.partition("=")
        if sep:
            kv[key.strip().lower()] = value.strip()
    host = kv.get("host") or kv.get("server") or kv.get("hostaddr")
    if host:
        port = kv.get("port")
        return HostTarget(host=host, port=int(port) if port and port.isdigit() else None)
    return None


def _split_mssql_server(value: str) -> HostTarget | None:
    """T-SQL writes ``host,port`` (and ``host\\instance``); everything else uses ``host:port``."""
    text = value.strip()
    if not text:
        return None
    text = text.split("\\", 1)[0]  # named instance
    for sep in (",", ":"):
        if sep in text:
            host, _, port = text.partition(sep)
            return HostTarget(host=host.strip(), port=int(port) if port.strip().isdigit() else None)
    return HostTarget(host=text)


# ---------------------------------------------------------------------------------------------
# Address classification
# ---------------------------------------------------------------------------------------------


def _resolve(host: str) -> list[str]:
    """Every address the name currently resolves to. Literal IPs are returned unchanged."""
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise NetworkPolicyError(f"Could not resolve host '{host}': {type(exc).__name__}") from exc
    return sorted({info[4][0] for info in infos})


def classify(address: str) -> str:
    """A short label for why an address is special: metadata, loopback, private, link_local, ... ."""
    if address in METADATA_ADDRESSES:
        return "metadata"
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return "unknown"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_reserved:
        return "reserved"
    if getattr(ip, "is_site_local", False):
        return "private"
    if ip.is_private:
        return "private"
    return "public"


def _matches_allowlist(host: str, addresses: list[str], allowlist: list[str]) -> bool:
    for entry in allowlist:
        candidate = entry.strip().lower()
        if not candidate:
            continue
        if candidate == host.lower():
            return True
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            continue
        for address in addresses:
            try:
                if ipaddress.ip_address(address) in network:
                    return True
            except ValueError:
                continue
    return False


# ---------------------------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------------------------


def check_target(
    connection_string: str,
    *,
    level: NetworkPolicyLevel,
    allowlist: list[str] | None = None,
    bundled_hosts: list[str] | None = None,
) -> NetworkDecision:
    """Decide whether ``connection_string`` may be dialled under the active policy."""
    allowlist = allowlist or []
    target = extract_host(connection_string)

    if target is None:
        # A local file database (SQLite/DuckDB). No network egress, so no network policy - the
        # filesystem is governed elsewhere (only enrolled, app-owned paths are used).
        return NetworkDecision(True, None, "Local file database; no network target", [], level)

    if level is NetworkPolicyLevel.BUNDLED_ONLY:
        permitted = {h.strip().lower() for h in (bundled_hosts or []) if h.strip()}
        if target.host.lower() in permitted:
            return NetworkDecision(True, target, "Bundled data source", [], level)
        return NetworkDecision(
            False,
            target,
            f"Demo mode connects only to bundled data sources; '{target.host}' is not one of them.",
            [],
            level,
        )

    if target.port is not None and target.port in BLOCKED_PORTS:
        return NetworkDecision(
            False, target, f"Port {target.port} is not a database port and is refused.", [], level
        )

    if target.host.lower().rstrip(".") in METADATA_HOSTNAMES:
        return NetworkDecision(
            False, target, "Cloud metadata endpoints are never valid database targets.", [], level
        )

    addresses = _resolve(target.host)
    kinds = {address: classify(address) for address in addresses}

    metadata_hits = [a for a, k in kinds.items() if k == "metadata"]
    if metadata_hits:
        return NetworkDecision(
            False,
            target,
            f"'{target.host}' resolves to a cloud metadata address ({metadata_hits[0]}).",
            addresses,
            level,
        )

    if level is NetworkPolicyLevel.PRIVATE_ALLOWED:
        return NetworkDecision(
            True,
            target,
            "Private and loopback targets are permitted in self-hosted mode",
            addresses,
            level,
        )

    # PUBLIC_STRICT
    if _matches_allowlist(target.host, addresses, allowlist):
        return NetworkDecision(
            True, target, "Host matches the configured network allowlist", addresses, level
        )
    blocked = {a: k for a, k in kinds.items() if k not in {"public", "unknown"}}
    if blocked:
        address, kind = next(iter(blocked.items()))
        return NetworkDecision(
            False,
            target,
            (
                f"'{target.host}' resolves to a {kind} address ({address}), which is refused in "
                "production mode. Add it to NETWORK_ALLOWLIST to permit it deliberately."
            ),
            addresses,
            level,
        )
    return NetworkDecision(True, target, "Public address", addresses, level)


__all__ = [
    "BLOCKED_PORTS",
    "METADATA_ADDRESSES",
    "METADATA_HOSTNAMES",
    "HostTarget",
    "NetworkDecision",
    "NetworkPolicyError",
    "check_target",
    "classify",
    "extract_host",
]
