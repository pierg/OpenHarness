"""Tests for openharness.api.gemini_client."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from openharness.api.client import ApiMessageCompleteEvent, ApiMessageRequest, ApiTextDeltaEvent
from openharness.api.errors import AuthenticationFailure, RateLimitFailure, RequestFailure
from openharness.api.gemini_client import (
    GeminiApiClient,
    _backoff_delay,
    _build_gemini_contents,
    _build_gemini_tools,
    _is_retryable,
    _translate_gemini_error,
)
from openharness.engine.messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock


# Inject a minimal google.genai stub so the optional SDK is not required.
@pytest.fixture(autouse=True)
def _stub_genai(monkeypatch):
    genai = MagicMock(name="google.genai")
    genai.types = MagicMock(name="google.genai.types")
    google = MagicMock(name="google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai.types)
    return genai


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction_vertex(monkeypatch, _stub_genai):
    mock_cls = MagicMock()
    _stub_genai.Client = mock_cls
    GeminiApiClient(project="proj", location="us-central1")
    mock_cls.assert_called_once_with(vertexai=True, project="proj", location="us-central1")


def test_construction_api_key(monkeypatch, _stub_genai):
    mock_cls = MagicMock()
    _stub_genai.Client = mock_cls
    GeminiApiClient(api_key="gm-key")
    mock_cls.assert_called_once_with(api_key="gm-key")


def test_construction_env_fallback(_stub_genai):
    mock_cls = MagicMock()
    _stub_genai.Client = mock_cls
    GeminiApiClient()
    mock_cls.assert_called_once_with()


def test_construction_project_beats_api_key(_stub_genai):
    mock_cls = MagicMock()
    _stub_genai.Client = mock_cls
    GeminiApiClient(api_key="ignored", project="proj")
    assert mock_cls.call_args.kwargs.get("vertexai") is True
    assert "api_key" not in mock_cls.call_args.kwargs


# ---------------------------------------------------------------------------
# stream_message
# ---------------------------------------------------------------------------


def _chunk(
    text: str | None = None,
    func_name: str | None = None,
    func_args: dict | None = None,
    finish_reason: str | None = None,
):
    part = MagicMock()
    part.text = text
    part.function_call = None
    if func_name:
        fc = MagicMock()
        fc.name = func_name
        fc.args = func_args or {}
        part.function_call = fc
    candidate = MagicMock()
    candidate.content.parts = [part]
    candidate.finish_reason = finish_reason
    chunk = MagicMock()
    chunk.candidates = [candidate]
    chunk.usage_metadata = None
    return chunk


class _Aiter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _make_client():
    return GeminiApiClient()


def _setup_stream(client, *chunks):
    client._client.aio.models.generate_content_stream = AsyncMock(
        return_value=_Aiter(chunks)
    )


async def test_stream_text_yields_deltas_and_complete():
    client = _make_client()
    _setup_stream(client, _chunk("Hello"), _chunk(", world"))

    events = [ev async for ev in client.stream_message(
        ApiMessageRequest(model="gemini-2.0-flash",
                          messages=[ConversationMessage.from_user_text("hi")])
    )]

    deltas = [e for e in events if isinstance(e, ApiTextDeltaEvent)]
    complete = next(e for e in events if isinstance(e, ApiMessageCompleteEvent))
    assert [d.text for d in deltas] == ["Hello", ", world"]
    assert complete.message.text == "Hello, world"
    assert complete.stop_reason == "end_turn"


async def test_stream_tool_call_in_complete():
    client = _make_client()
    _setup_stream(client, _chunk(func_name="bash", func_args={"command": "ls"}))

    events = [ev async for ev in client.stream_message(
        ApiMessageRequest(model="gemini-2.0-flash",
                          messages=[ConversationMessage.from_user_text("hi")])
    )]

    complete = next(e for e in events if isinstance(e, ApiMessageCompleteEvent))
    assert complete.stop_reason == "tool_use"
    assert len(complete.message.tool_uses) == 1
    assert complete.message.tool_uses[0].name == "bash"


# ---------------------------------------------------------------------------
# _build_gemini_tools
# ---------------------------------------------------------------------------


def test_build_tools_empty():
    assert _build_gemini_tools([], MagicMock()) == []


def test_build_tools_injects_properties():
    types = MagicMock()
    _build_gemini_tools([{"name": "noop", "description": "", "input_schema": {}}], types)
    schema = types.FunctionDeclaration.call_args.kwargs["parameters"]
    assert "properties" in schema


def test_build_tools_preserves_properties():
    types = MagicMock()
    _build_gemini_tools([{"name": "bash", "description": "", "input_schema": {"properties": {"cmd": {}}}}], types)
    schema = types.FunctionDeclaration.call_args.kwargs["parameters"]
    assert "cmd" in schema["properties"]


# ---------------------------------------------------------------------------
# _build_gemini_contents — tool-name resolution
# ---------------------------------------------------------------------------


def test_build_contents_resolves_tool_name():
    types = MagicMock()
    messages = [
        ConversationMessage(role="assistant", content=[ToolUseBlock(id="c1", name="bash", input={})]),
        ConversationMessage(role="user", content=[ToolResultBlock(tool_use_id="c1", content="ok")]),
    ]
    _build_gemini_contents(messages, types)
    assert types.Part.from_function_response.call_args.kwargs["name"] == "bash"


def test_build_contents_assistant_role_is_model():
    types = MagicMock()
    captured = []
    types.Content.side_effect = lambda **kw: captured.append(kw)
    _build_gemini_contents([ConversationMessage(role="assistant", content=[TextBlock(text="hi")])], types)
    assert captured[0]["role"] == "model"


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("msg,retryable", [
    ("429 quota exceeded", True),
    ("503 unavailable", True),
    ("timeout", True),
    ("not found", False),
])
def test_is_retryable(msg, retryable):
    assert _is_retryable(RuntimeError(msg)) is retryable


def test_translate_error_auth():
    assert isinstance(_translate_gemini_error(RuntimeError("invalid api_key")), AuthenticationFailure)


def test_translate_error_rate_limit():
    assert isinstance(_translate_gemini_error(RuntimeError("429 quota")), RateLimitFailure)


def test_translate_error_generic():
    assert isinstance(_translate_gemini_error(RuntimeError("something else")), RequestFailure)


def test_backoff_delay_grows_with_attempt():
    assert _backoff_delay(1) > _backoff_delay(0) * 0.9


def test_backoff_delay_capped():
    assert _backoff_delay(100) <= 30.0 * 1.25
