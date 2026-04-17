"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_langfuse_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests hermetic regardless of the developer shell or ``uv run``'s ``.env``."""
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "0")
    # ``uv run`` auto-loads the project ``.env`` so unit tests can pick up
    # real Langfuse credentials, an alternate trace host, or a session ID
    # belonging to the developer's running experiment. Strip them so each
    # test sees a clean slate and can opt back in via ``monkeypatch.setenv``.
    for key in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "LANGFUSE_BASE_URL",
        "LANGFUSE_PUBLIC_HOST",
        "LANGFUSE_ENVIRONMENT",
        "LANGFUSE_RELEASE",
        "LANGFUSE_SAMPLE_RATE",
        "OPENHARNESS_LANGFUSE_SESSION_ID",
        "OPENHARNESS_LANGFUSE_FLUSH_MODE",
        "OPENHARNESS_LANGFUSE_REQUIRED",
        "OPENHARNESS_LANGFUSE_VERIFY",
    ):
        monkeypatch.delenv(key, raising=False)
