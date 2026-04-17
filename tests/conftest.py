"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_langfuse_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests hermetic even when the developer shell exports Langfuse credentials."""
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "0")
