"""Gemini API client for OpenHarness.

Implements the same ``SupportsStreamingMessages`` protocol as
``AnthropicApiClient``, allowing the query engine to use Gemini models
without any provider-specific logic at the engine layer.

Supports both Google AI Studio (API key) and Vertex AI (project + location).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import uuid
from typing import Any, AsyncIterator

from openharness.api.client import (
    MAX_RETRIES,
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiTextDeltaEvent,
)
from openharness.api.errors import (
    AuthenticationFailure,
    OpenHarnessApiError,
    RateLimitFailure,
    RequestFailure,
)
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

log = logging.getLogger(__name__)

# Backoff constants (shared shape with AnthropicApiClient, but Gemini has no
# Retry-After header so we always use pure exponential backoff).
_BASE_DELAY = 1.0
_MAX_DELAY = 30.0
_RETRYABLE_STATUS_FRAGMENTS = frozenset({"429", "quota", "503", "500", "timeout", "unavailable"})


class GeminiApiClient:
    """Streaming client for Gemini models via Google AI Studio or Vertex AI.

    Construction priority:
    1. ``project`` set → Vertex AI (``vertexai=True``).
    2. ``api_key`` set → Google AI Studio with an explicit key.
    3. Neither set → Google AI Studio, key resolved from ``GOOGLE_API_KEY``
       or ``GEMINI_API_KEY`` environment variables by the SDK.
    """

    def __init__(
        self,
        api_key: str | None = None,
        project: str | None = None,
        location: str | None = None,
    ) -> None:
        from google import genai  # noqa: PLC0415

        if project:
            kwargs: dict[str, Any] = {"vertexai": True, "project": project}
            if location:
                kwargs["location"] = location
            self._client = genai.Client(**kwargs)
        elif api_key:
            self._client = genai.Client(api_key=api_key)
        else:
            self._client = genai.Client()

    async def stream_message(
        self, request: ApiMessageRequest
    ) -> AsyncIterator[ApiTextDeltaEvent | ApiMessageCompleteEvent]:
        """Yield text deltas then a final complete event, with retry on transient errors."""
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                async for event in self._stream_once(request):
                    yield event
                return
            except OpenHarnessApiError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= MAX_RETRIES or not _is_retryable(exc):
                    raise _translate_gemini_error(exc) from exc
                delay = _backoff_delay(attempt)
                log.warning(
                    "Gemini request failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        if last_error is not None:
            raise _translate_gemini_error(last_error) from last_error

    async def _stream_once(
        self, request: ApiMessageRequest
    ) -> AsyncIterator[ApiTextDeltaEvent | ApiMessageCompleteEvent]:
        from google.genai import types  # noqa: PLC0415

        tools = _build_gemini_tools(request.tools, types)
        contents = _build_gemini_contents(request.messages, types)

        config_kwargs: dict[str, Any] = {
            "max_output_tokens": request.max_tokens,
            "temperature": 0.0,
        }
        if request.system_prompt:
            config_kwargs["system_instruction"] = request.system_prompt
        if tools:
            config_kwargs["tools"] = tools
        config = types.GenerateContentConfig(**config_kwargs)

        response_stream = await self._client.aio.models.generate_content_stream(
            model=request.model,
            contents=contents,
            config=config,
        )

        full_text = ""
        tool_calls: list[dict[str, Any]] = []
        input_tokens = 0
        output_tokens = 0

        async for chunk in response_stream:
            if chunk.usage_metadata:
                input_tokens = int(chunk.usage_metadata.prompt_token_count or 0)
                output_tokens = int(chunk.usage_metadata.candidates_token_count or 0)

            if not chunk.candidates:
                continue
            candidate = chunk.candidates[0]
            if not candidate.content or not candidate.content.parts:
                continue

            for part in candidate.content.parts:
                if part.text:
                    full_text += part.text
                    yield ApiTextDeltaEvent(text=part.text)
                elif part.function_call:
                    args = dict(part.function_call.args) if part.function_call.args else {}
                    tool_calls.append({"name": part.function_call.name, "args": args})

        final_content: list[TextBlock | ToolUseBlock] = []
        if full_text:
            final_content.append(TextBlock(text=full_text))
        for tc in tool_calls:
            final_content.append(
                ToolUseBlock(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=tc["name"],
                    input=tc["args"],
                )
            )

        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=final_content),
            usage=UsageSnapshot(input_tokens=input_tokens, output_tokens=output_tokens),
            stop_reason="tool_use" if tool_calls else "end_turn",
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_gemini_tools(
    tools: list[dict[str, Any]], types: Any
) -> list[Any]:
    """Convert OpenHarness / Anthropic tool schemas to Gemini FunctionDeclarations."""
    if not tools:
        return []
    declarations = []
    for tool in tools:
        schema = dict(tool.get("input_schema", {}))
        # Gemini requires `properties` to be present even for parameterless tools.
        schema.setdefault("properties", {})
        declarations.append(
            types.FunctionDeclaration(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                parameters=schema,
            )
        )
    return [types.Tool(function_declarations=declarations)]


def _build_gemini_contents(
    messages: list[ConversationMessage], types: Any
) -> list[Any]:
    """Convert ConversationMessages to Gemini Content objects.

    Tool result blocks require the original function name for Gemini's
    function-response format, which is not stored in ``ToolResultBlock`` itself.
    We build a lookup from call-id → function-name by scanning prior assistant
    messages in the conversation.
    """
    tool_name_by_id: dict[str, str] = {}
    for msg in messages:
        for block in msg.content:
            if isinstance(block, ToolUseBlock):
                tool_name_by_id[block.id] = block.name

    contents = []
    for msg in messages:
        parts = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                parts.append(types.Part.from_text(text=block.text))
            elif isinstance(block, ToolUseBlock):
                parts.append(
                    types.Part.from_function_call(name=block.name, args=block.input)
                )
            elif isinstance(block, ToolResultBlock):
                func_name = tool_name_by_id.get(block.tool_use_id, block.tool_use_id)
                parts.append(
                    types.Part.from_function_response(
                        name=func_name,
                        response={"result": block.content},
                    )
                )
        role = "user" if msg.role == "user" else "model"
        contents.append(types.Content(role=role, parts=parts))
    return contents


def _is_retryable(exc: Exception) -> bool:
    err = str(exc).lower()
    return any(fragment in err for fragment in _RETRYABLE_STATUS_FRAGMENTS)


def _backoff_delay(attempt: int) -> float:
    delay = min(_BASE_DELAY * math.pow(2, attempt), _MAX_DELAY)
    return delay + random.uniform(0, delay * 0.25)


def _translate_gemini_error(exc: Exception) -> OpenHarnessApiError:
    err = str(exc).lower()
    if any(token in err for token in ("api_key", "unauthorized", "401", "unauthenticated")):
        return AuthenticationFailure(str(exc))
    if "429" in err or "quota" in err:
        return RateLimitFailure(str(exc))
    return RequestFailure(str(exc))
