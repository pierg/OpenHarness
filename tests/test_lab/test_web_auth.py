"""Tests for the lab web UI auth module.

Covers the two operating modes the webui actually supports:

- ``open``         — no env vars; all requests are admin (loopback trust).
- ``proxy:cf``     — ``LAB_TRUST_PROXY_AUTH=cloudflare-access`` + email lists.
- ``proxy:iap``    — ``LAB_TRUST_PROXY_AUTH=iap`` strips Google's prefix.

These tests don't spin up FastAPI; they construct minimal ``Request``
stand-ins from Starlette's primitives. That keeps them fast and
isolates the auth module from routing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.requests import Request

from openharness.lab.web import auth as labauth


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(headers: dict[str, str] | None = None) -> Request:
    """Build the smallest valid Starlette ``Request`` for header-only tests."""
    raw_headers = []
    for name, value in (headers or {}).items():
        raw_headers.append((name.lower().encode("latin-1"), value.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/cmd",
        "raw_path": b"/api/cmd",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 8765),
        "scheme": "http",
        "http_version": "1.1",
        "root_path": "",
        "app": None,
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip every env var the auth module looks at before each test."""
    for var in (
        "LAB_TRUST_PROXY_AUTH",
        "LAB_ADMIN_EMAILS",
        "LAB_VIEWER_EMAILS",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


# ---------------------------------------------------------------------------
# open mode
# ---------------------------------------------------------------------------


def test_open_mode_is_default_admin() -> None:
    assert labauth.configured_mode() == "open"
    identity = labauth.identify(_make_request())
    assert identity.role == "admin"
    assert identity.mode == "open"
    assert identity.email is None
    assert labauth.check_write(identity) is None


def test_open_mode_ignores_proxy_headers() -> None:
    # In open mode we MUST NOT trust trusted-proxy headers — otherwise
    # any local process could spoof admin status by talking to the
    # webui directly while the operator thinks they're protected.
    identity = labauth.identify(
        _make_request({"Cf-Access-Authenticated-User-Email": "stranger@evil.com"}),
    )
    assert identity.email is None
    assert identity.mode == "open"
    assert identity.role == "admin"  # localhost trust still applies


# ---------------------------------------------------------------------------
# proxy mode (Cloudflare Access)
# ---------------------------------------------------------------------------


def test_cf_proxy_admin_email_grants_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_TRUST_PROXY_AUTH", "cloudflare-access")
    monkeypatch.setenv("LAB_ADMIN_EMAILS", "owner@example.com,helper@example.com")
    identity = labauth.identify(
        _make_request({"Cf-Access-Authenticated-User-Email": "owner@example.com"}),
    )
    assert identity.role == "admin"
    assert identity.email == "owner@example.com"
    assert identity.mode == "proxy"
    assert labauth.check_write(identity) is None


def test_cf_proxy_viewer_email_grants_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_TRUST_PROXY_AUTH", "cloudflare-access")
    monkeypatch.setenv("LAB_ADMIN_EMAILS", "owner@example.com")
    monkeypatch.setenv("LAB_VIEWER_EMAILS", "prof@berkeley.edu")
    identity = labauth.identify(
        _make_request({"Cf-Access-Authenticated-User-Email": "prof@berkeley.edu"}),
    )
    assert identity.role == "viewer"
    err = labauth.check_write(identity)
    assert err is not None and "Read-only" in err


def test_cf_proxy_unknown_email_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_TRUST_PROXY_AUTH", "cloudflare-access")
    monkeypatch.setenv("LAB_ADMIN_EMAILS", "owner@example.com")
    identity = labauth.identify(
        _make_request({"Cf-Access-Authenticated-User-Email": "stranger@example.com"}),
    )
    assert identity.role == "anonymous"
    assert identity.email == "stranger@example.com"
    err = labauth.check_write(identity)
    assert err is not None and "stranger@example.com" in err


def test_cf_proxy_missing_header_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_TRUST_PROXY_AUTH", "cloudflare-access")
    monkeypatch.setenv("LAB_ADMIN_EMAILS", "owner@example.com")
    identity = labauth.identify(_make_request())
    assert identity.role == "anonymous"
    assert identity.email is None
    err = labauth.check_write(identity)
    assert err is not None and "did not carry" in err


def test_cf_proxy_email_lookup_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_TRUST_PROXY_AUTH", "cloudflare-access")
    monkeypatch.setenv("LAB_ADMIN_EMAILS", "Owner@Example.COM")
    identity = labauth.identify(
        _make_request({"Cf-Access-Authenticated-User-Email": "OWNER@example.com"}),
    )
    assert identity.role == "admin"


# ---------------------------------------------------------------------------
# proxy mode (Google IAP)
# ---------------------------------------------------------------------------


def test_iap_strips_accounts_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAB_TRUST_PROXY_AUTH", "iap")
    monkeypatch.setenv("LAB_ADMIN_EMAILS", "owner@example.com")
    identity = labauth.identify(
        _make_request(
            {"X-Goog-Authenticated-User-Email": "accounts.google.com:owner@example.com"},
        ),
    )
    assert identity.email == "owner@example.com"
    assert identity.role == "admin"


# ---------------------------------------------------------------------------
# Misconfiguration safety: unknown proxy kind silently disables proxy mode
# ---------------------------------------------------------------------------


def test_unknown_proxy_kind_falls_back_to_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # Misspelled values must NOT silently grant access via spoofable
    # headers. Falling back to open mode is OK because open mode does
    # not consult any header.
    monkeypatch.setenv("LAB_TRUST_PROXY_AUTH", "azure-ad")  # unsupported
    monkeypatch.setenv("LAB_ADMIN_EMAILS", "owner@example.com")
    assert labauth.configured_mode() == "open"
    identity = labauth.identify(
        _make_request({"Cf-Access-Authenticated-User-Email": "stranger@evil.com"}),
    )
    assert identity.email is None
    assert identity.mode == "open"
