from __future__ import annotations

from datetime import date
from functools import partial

import pytest

from product_hunter.safety import (
    UrlBlocked,
    admit_redirect,
    admit_url,
    is_public_ip,
    parse_robots,
    resolve_public_ips,
)
from product_hunter.sources import (
    AllowedUse,
    Collector,
    RateLimit,
    RobotsPolicy,
    SourceConfig,
    TermsStatus,
)

PUBLIC = "93.184.216.34"
PUBLIC6 = "2606:2800:220:1:248:1893:25c8:1946"


def _dns(*ips):
    def lookup(host, port):
        return [(None, None, None, None, (ip, 0)) for ip in ips]

    return lookup


def _resolver(*ips):
    """A fake resolve_public_ips that returns the given addresses without network."""
    return partial(resolve_public_ips, resolver=_dns(*ips))


def make_source(**overrides) -> SourceConfig:
    data = {
        "source_id": "hn_algolia",
        "terms_status": TermsStatus.permitted,
        "allowed_use": [
            AllowedUse.discovery,
            AllowedUse.retrieval,
            AllowedUse.analysis,
            AllowedUse.storage,
        ],
        "reviewed_at": date(2026, 10, 20),
        "collector": Collector.api,
        "rate_limit": RateLimit(
            min_interval_seconds=1.0,
            max_requests_per_run=10,
            max_retries=2,
            backoff_base_seconds=1.0,
            backoff_max_seconds=30.0,
        ),
        "domains": ["hn.algolia.com", "news.ycombinator.com"],
        "schemes": ["https"],
        "robots": RobotsPolicy.skip,
        "enabled": True,
    }
    data.update(overrides)
    return SourceConfig(**data)


def test_allowed_url_passes_and_strips_fragment():
    admitted = admit_url(
        "https://hn.algolia.com/api/v1/search?query=x#frag",
        make_source(),
        resolver=_resolver(PUBLIC),
    )
    assert admitted.scheme == "https"
    assert admitted.host == "hn.algolia.com"
    assert admitted.port == 443
    assert admitted.url == "https://hn.algolia.com/api/v1/search?query=x"
    assert admitted.resolved_ips == [PUBLIC]


def test_empty_path_defaults_to_root():
    admitted = admit_url("https://hn.algolia.com", make_source(), resolver=_resolver(PUBLIC))
    assert admitted.url == "https://hn.algolia.com/"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:text/plain,hi",
        "javascript:alert(1)",
        "ftp://hn.algolia.com/x",
    ],
)
def test_non_configured_scheme_blocks(url):
    with pytest.raises(UrlBlocked):
        admit_url(url, make_source(), resolver=_resolver(PUBLIC))


def test_userinfo_blocks():
    with pytest.raises(UrlBlocked):
        admit_url(
            "https://user:pass@hn.algolia.com/x", make_source(), resolver=_resolver(PUBLIC)
        )


def test_nonstandard_port_blocks():
    with pytest.raises(UrlBlocked):
        admit_url("https://hn.algolia.com:8443/x", make_source(), resolver=_resolver(PUBLIC))


def test_off_allowlist_host_blocks():
    with pytest.raises(UrlBlocked):
        admit_url("https://evil.example.com/x", make_source(), resolver=_resolver(PUBLIC))


def test_ip_literal_host_blocks():
    with pytest.raises(UrlBlocked):
        admit_url("https://93.184.216.34/x", make_source(), resolver=_resolver(PUBLIC))


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",  # private
        "127.0.0.1",  # loopback
        "169.254.1.1",  # link-local
        "100.64.0.1",  # CGNAT
        "240.0.0.1",  # reserved
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "::ffff:10.0.0.1",  # IPv4-mapped private
    ],
)
def test_non_public_ip_blocks(ip):
    assert is_public_ip(ip) is False
    with pytest.raises(UrlBlocked):
        admit_url("https://hn.algolia.com/x", make_source(), resolver=_resolver(ip))


@pytest.mark.parametrize("ip", [PUBLIC, PUBLIC6, "8.8.8.8"])
def test_public_ip_passes(ip):
    assert is_public_ip(ip) is True


def test_mixed_public_and_private_resolution_blocks():
    with pytest.raises(UrlBlocked):
        admit_url(
            "https://hn.algolia.com/x",
            make_source(),
            resolver=_resolver(PUBLIC, "10.0.0.1"),
        )


def test_public_ipv4_and_ipv6_pass():
    source = make_source()
    assert admit_url("https://hn.algolia.com/x", source, resolver=_resolver(PUBLIC)).resolved_ips == [PUBLIC]
    assert admit_url("https://hn.algolia.com/x", source, resolver=_resolver(PUBLIC6)).resolved_ips == [PUBLIC6]


def test_resolve_public_ips_dedupes():
    assert resolve_public_ips("hn.algolia.com", resolver=_dns(PUBLIC, PUBLIC)) == [PUBLIC]


def test_redirect_off_host_blocks():
    with pytest.raises(UrlBlocked):
        admit_redirect(
            "https://hn.algolia.com/a",
            "https://evil.example.com/b",
            make_source(),
            resolver=_resolver(PUBLIC),
        )


def test_https_to_http_downgrade_blocks():
    source = make_source(schemes=["http", "https"])
    with pytest.raises(UrlBlocked):
        admit_redirect(
            "https://hn.algolia.com/a",
            "http://hn.algolia.com/b",
            source,
            resolver=_resolver(PUBLIC),
        )


def test_relative_and_same_host_redirect_pass():
    source = make_source()
    relative = admit_redirect(
        "https://hn.algolia.com/a", "/b", source, resolver=_resolver(PUBLIC)
    )
    assert relative.url == "https://hn.algolia.com/b"
    same_host = admit_redirect(
        "https://hn.algolia.com/a",
        "https://hn.algolia.com/c",
        source,
        resolver=_resolver(PUBLIC),
    )
    assert same_host.host == "hn.algolia.com"
    assert same_host.url == "https://hn.algolia.com/c"


def test_robots_allows_and_denies():
    text = "User-agent: *\nDisallow: /private\n"
    assert parse_robots(text, "https://hn.algolia.com/public", "*") is True
    assert parse_robots(text, "https://hn.algolia.com/private/x", "*") is False
