"""Tests for openharness.api.factory."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from openharness.api.client import AnthropicApiClient
from openharness.api.factory import create_api_client
from openharness.config.settings import Settings


@pytest.fixture(autouse=True)
def _stub_genai(monkeypatch):
    genai = MagicMock(name="google.genai")
    genai.types = MagicMock()
    google = MagicMock(name="google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai.types)
    return genai


def test_anthropic_model_returns_anthropic_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    client = create_api_client(Settings(model="claude-sonnet-4-20250514"))
    assert isinstance(client, AnthropicApiClient)


def test_gemini_model_returns_gemini_client(monkeypatch):
    from openharness.api.gemini_client import GeminiApiClient

    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    client = create_api_client(Settings(model="gemini-2.0-flash"))
    assert isinstance(client, GeminiApiClient)


def test_gemini_with_vertex_project_skips_api_key(monkeypatch):
    mock_cls = MagicMock()
    monkeypatch.setattr("openharness.api.gemini_client.GeminiApiClient", mock_cls)

    create_api_client(
        Settings(model="gemini-2.0-flash", vertex_project="my-proj", vertex_location="us-east1")
    )
    mock_cls.assert_called_once_with(project="my-proj", location="us-east1")


def test_anthropic_base_url_forwarded(monkeypatch):
    mock_cls = MagicMock()
    monkeypatch.setattr("openharness.api.factory.AnthropicApiClient", mock_cls)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    create_api_client(Settings(model="claude-sonnet-4-20250514", base_url="https://proxy/"))

    assert mock_cls.call_args.kwargs.get("base_url") == "https://proxy/"
