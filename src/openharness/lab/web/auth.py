"""Authentication for the lab web UI.

Two operating modes — picked entirely by env vars at process start:

1. **open** (default — no env vars set)
    Read & write are unrestricted. The deployment assumption is that
    uvicorn binds to ``127.0.0.1`` and is reached either directly on
    the host or via an SSH tunnel from a single trusted operator.
    Localhost is the trust boundary.

2. **proxy** (``LAB_TRUST_PROXY_AUTH=cloudflare-access`` or ``=iap``)
    Identity is extracted from a header injected by an authenticating
    reverse proxy (Cloudflare Access or Google IAP). The lab itself
    does not authenticate; it trusts the proxy's claim and authorises
    against role allow-lists from env:

    - ``LAB_ADMIN_EMAILS`` — comma-separated. Members may POST
      ``/api/cmd`` and click destructive buttons.
    - ``LAB_VIEWER_EMAILS`` — comma-separated, optional. Members may
      read every page but receive a 403 from any write attempt and
      see destructive buttons hidden in the UI.

    Anyone whose email is not in either list gets a clear "you're
    signed in but not authorised" page rather than a confusing 403.

**Threat-model assumption for proxy mode**: requests arrive on
``127.0.0.1:8765`` from a trusted local proxy (cloudflared / IAP
sidecar). Anything else with shell on the VM could spoof the trusted
header, so don't bind the webui to a non-loopback interface in proxy
mode unless you also verify the proxy's JWT assertion (out of scope
here; ``Cf-Access-Jwt-Assertion`` is what you'd verify, against the
JWKS at ``https://<team>.cloudflareaccess.com/cdn-cgi/access/certs``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from fastapi import Request

__all__ = [
    "Identity",
    "Mode",
    "configured_mode",
    "identify",
    "check_write",
    "is_enabled",
]


Mode = Literal["open", "proxy"]
Role = Literal["admin", "viewer", "anonymous"]
ProxyKind = Literal["cloudflare-access", "iap"]


# ---------------------------------------------------------------------------
# Env config helpers
# ---------------------------------------------------------------------------


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _email_list(name: str) -> set[str]:
    raw = _env(name)
    if not raw:
        return set()
    # Lowercase for case-insensitive matching — Google emails are
    # commonly typed in mixed case.
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _proxy_kind() -> ProxyKind | None:
    raw = _env("LAB_TRUST_PROXY_AUTH").lower()
    if raw == "cloudflare-access":
        return "cloudflare-access"
    if raw == "iap":
        return "iap"
    # Misspellings or unknown values fall through to None and the
    # webui boots in ``open`` mode. This is the safe failure: an
    # unknown value MUST NOT cause us to honour spoofable headers.
    return None


def configured_mode() -> Mode:
    if _proxy_kind() is not None:
        return "proxy"
    return "open"


def is_enabled() -> bool:
    """True if any auth gate is in front of writes."""
    return configured_mode() != "open"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Identity:
    email: str | None
    role: Role
    mode: Mode
    # Optional human-readable reason a request was rejected; ``None``
    # when the request is authorised (or anonymous-but-allowed).
    reject_reason: str | None = None

    @property
    def can_write(self) -> bool:
        return self.role == "admin"

    @property
    def display_name(self) -> str:
        if self.email:
            return self.email
        return "(local)"

    @property
    def role_color(self) -> str:
        # CSS classes used by the badge in base.html.
        if self.role == "admin":
            return "bg-emerald-100 text-emerald-800"
        if self.role == "viewer":
            return "bg-sky-100 text-sky-800"
        return "bg-rose-100 text-rose-800"


# ---------------------------------------------------------------------------
# Proxy header parsing
# ---------------------------------------------------------------------------


def _proxy_email(request: Request, kind: ProxyKind) -> str | None:
    if kind == "cloudflare-access":
        raw = request.headers.get("Cf-Access-Authenticated-User-Email")
    else:  # iap
        raw = request.headers.get("X-Goog-Authenticated-User-Email")
        if raw and ":" in raw:
            # IAP prepends ``accounts.google.com:`` to the email.
            raw = raw.split(":", 1)[1]
    if not raw:
        return None
    return raw.strip().lower() or None


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


def identify(request: Request) -> Identity:
    mode = configured_mode()

    if mode == "open":
        return Identity(email=None, role="admin", mode="open")

    # proxy mode
    kind = _proxy_kind()
    assert kind is not None  # configured_mode() guarantees it
    email = _proxy_email(request, kind)
    if email is None:
        return Identity(
            email=None,
            role="anonymous",
            mode="proxy",
            reject_reason=(
                f"Proxy auth ({kind}) is enabled but the request did not "
                "carry an authenticated-user header. Either the request "
                "bypassed the proxy or the proxy is misconfigured."
            ),
        )
    admins = _email_list("LAB_ADMIN_EMAILS")
    viewers = _email_list("LAB_VIEWER_EMAILS")
    if email in admins:
        return Identity(email=email, role="admin", mode="proxy")
    if email in viewers:
        return Identity(email=email, role="viewer", mode="proxy")
    return Identity(
        email=email,
        role="anonymous",
        mode="proxy",
        reject_reason=(
            f"{email} is signed in but not in LAB_ADMIN_EMAILS or "
            "LAB_VIEWER_EMAILS. Ask the lab owner to add you."
        ),
    )


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


def check_write(identity: Identity) -> str | None:
    """Return ``None`` if the identity may POST /api/cmd, else a message."""
    if identity.role == "admin":
        return None
    if identity.role == "viewer":
        return (
            f"Read-only role: {identity.email or '(viewer)'} cannot run "
            "lab commands. Ask the lab owner for admin access."
        )
    # anonymous
    return identity.reject_reason or "Not authenticated."
