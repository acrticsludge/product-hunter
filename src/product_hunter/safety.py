from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from product_hunter.sources import SourceConfig

# Carrier-grade NAT 100.64.0.0/10: not "private" to ipaddress, not globally routable.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_ALLOWED_PORTS = (80, 443)


class UrlBlocked(Exception):
    """Raised when a URL may not be fetched for the given source."""

    reason_code = "unsafe_url"


@dataclass(frozen=True)
class Admission:
    url: str
    scheme: str
    host: str
    port: int
    resolved_ips: list[str]


def is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    if ip.version == 4 and ip in _CGNAT:
        return False
    return ip.is_global


def resolve_public_ips(host: str, resolver=socket.getaddrinfo) -> list[str]:
    """Resolve a host and return its addresses, failing closed on any non-public one."""
    ips: list[str] = []
    for entry in resolver(host, None):
        ip = entry if isinstance(entry, str) else entry[4][0]
        if ip not in ips:
            ips.append(ip)
    for ip in ips:
        if not is_public_ip(ip):
            raise UrlBlocked(f"host {host!r} resolves to a non-public IP: {ip}")
    return ips


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def admit_url(url: str, source: SourceConfig, resolver=resolve_public_ips) -> Admission:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {s.lower() for s in source.schemes}:
        raise UrlBlocked(f"scheme not allowed for {source.source_id!r}: {parsed.scheme!r}")
    if parsed.username or parsed.password:
        raise UrlBlocked("URL userinfo is not allowed")
    host = parsed.hostname
    if not host:
        raise UrlBlocked(f"URL has no host: {url!r}")
    if _is_ip_literal(host):
        raise UrlBlocked(f"IP-literal host is not allowed: {host!r}")
    if host not in source.domains:
        raise UrlBlocked(f"host {host!r} is not in the allowlist for {source.source_id!r}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UrlBlocked(f"invalid port in URL: {url!r}") from exc

    default_port = 443 if scheme == "https" else 80
    if port is not None and port not in _ALLOWED_PORTS:
        raise UrlBlocked(f"nonstandard port is not allowed: {port}")
    if port is not None and port != default_port:
        raise UrlBlocked(f"scheme {scheme!r} does not allow port {port}")
    netloc = host if port in (None, default_port) else f"{host}:{port}"
    cleaned = urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
    resolved = resolver(host)
    return Admission(
        url=cleaned,
        scheme=scheme,
        host=host,
        port=port if port is not None else default_port,
        resolved_ips=resolved,
    )


def admit_redirect(
    current: str, location: str, source: SourceConfig, resolver=resolve_public_ips
) -> Admission:
    admitted = admit_url(urljoin(current, location), source, resolver=resolver)
    if urlsplit(current).scheme.lower() == "https" and admitted.scheme == "http":
        raise UrlBlocked("redirect from HTTPS to HTTP is not allowed")
    return admitted


def parse_robots(text: str, url: str, user_agent: str) -> bool:
    """Return True when robots.txt allows user_agent to fetch url."""
    parser = RobotFileParser()
    parser.set_url(url)
    parser.parse(text.splitlines())
    return parser.can_fetch(user_agent, url)
