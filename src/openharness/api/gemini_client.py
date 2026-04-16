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

        log.debug("Sending Gemini request: model=%s contents_len=%d", request.model, len(contents))
        for i, c in enumerate(contents):
            log.debug("Content %d: role=%s parts_len=%d", i, c.role, len(c.parts))

        response_stream = await self._client.aio.models.generate_content_stream(
            model=request.model,
            contents=contents,
            config=config,
        )

        full_text = ""
        full_text_signature: bytes | None = None
        tool_calls: list[dict[str, Any]] = []
        # Gemini may emit a standalone "thought" part (thought=True, no text,
        # no function_call) carrying only a ``thought_signature``. That signature
        # must be echoed back on the following function_call/text part, otherwise
        # the API will return a 400 ("Function call is missing a thought_signature").
        pending_signature: bytes | None = None
        input_tokens = 0
        output_tokens = 0

        async for chunk in response_stream:
            log.debug("Received Gemini chunk: %s", chunk)
            if chunk.usage_metadata:
                input_tokens = int(chunk.usage_metadata.prompt_token_count or 0)
                output_tokens = int(chunk.usage_metadata.candidates_token_count or 0)

            if not chunk.candidates:
                continue
            candidate = chunk.candidates[0]
            if not candidate.content or not candidate.content.parts:
                continue

            for part in candidate.content.parts:
                # ``thought_signature`` is a field on ``Part`` itself, NOT on
                # ``part.function_call``. Capture it before routing by part kind.
                part_sig = getattr(part, "thought_signature", None)
                if part.text:
                    full_text += part.text
                    if part_sig:
                        full_text_signature = part_sig
                        log.debug("Captured Gemini thought signature for text: %s", part_sig)
                    elif pending_signature is not None:
                        full_text_signature = pending_signature
                        pending_signature = None
                    yield ApiTextDeltaEvent(text=part.text)
                elif part.function_call:
                    args = dict(part.function_call.args) if part.function_call.args else {}
                    fc_sig = part_sig if part_sig is not None else pending_signature
                    pending_signature = None
                    log.debug(
                        "Captured Gemini thought signature for FC %s: %s",
                        part.function_call.name,
                        bool(fc_sig),
                    )
                    tool_calls.append(
                        {"name": part.function_call.name, "args": args, "thought_signature": fc_sig}
                    )
                elif part_sig is not None:
                    # Thought-only part with no text / function_call; hold signature
                    # for the next emitted block so we can echo it back later.
                    log.debug("Captured Gemini thought-only signature (len=%d)", len(part_sig))
                    pending_signature = part_sig

        final_content: list[TextBlock | ToolUseBlock] = []
        if full_text:
            final_content.append(TextBlock(text=full_text, thought_signature=full_text_signature))
        for tc in tool_calls:
            final_content.append(
                ToolUseBlock(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    name=tc["name"],
                    input=tc["args"],
                    thought_signature=tc["thought_signature"],
                )
            )

        # Attach any remaining pending signature to the first content block so
        # it isn't lost on the roundtrip.
        if pending_signature is not None and final_content:
            head = final_content[0]
            if head.thought_signature is None:
                final_content[0] = head.model_copy(update={"thought_signature": pending_signature})

        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=final_content),
            usage=UsageSnapshot(input_tokens=input_tokens, output_tokens=output_tokens),
            stop_reason="tool_use" if tool_calls else "end_turn",
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_gemini_tools(tools: list[dict[str, Any]], types: Any) -> list[Any]:
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


def _build_gemini_contents(messages: list[ConversationMessage], types: Any) -> list[Any]:
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
        log.debug("Building Gemini message: role=%s", msg.role)
        for block in msg.content:
            if isinstance(block, TextBlock):
                if block.thought_signature:
                    log.debug(
                        "Sending Gemini thought signature for text: %s", block.thought_signature
                    )
                    parts.append(
                        types.Part(text=block.text, thought_signature=block.thought_signature)
                    )
                else:
                    parts.append(types.Part.from_text(text=block.text))
            elif isinstance(block, ToolUseBlock):
                if block.thought_signature:
                    log.debug(
                        "Sending Gemini thought signature for FC %s (len=%d)",
                        block.name,
                        len(block.thought_signature),
                    )
                    # ``thought_signature`` is a field on ``Part`` itself; it is
                    # NOT a valid field on ``FunctionCall`` (pydantic rejects it).
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                name=block.name,
                                args=block.input,
                            ),
                            thought_signature=block.thought_signature,
                        )
                    )
                else:
                    parts.append(types.Part.from_function_call(name=block.name, args=block.input))
            elif isinstance(block, ToolResultBlock):
                log.debug("Adding ToolResultBlock: tool_use_id=%s", block.tool_use_id)
                func_name = tool_name_by_id.get(block.tool_use_id, block.tool_use_id)

                # Format the response in a dictionary.
                response_dict = {"result": block.content}

                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=func_name, response=response_dict
                        )
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
