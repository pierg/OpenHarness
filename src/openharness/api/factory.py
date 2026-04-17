"""Client factory: select the right API client from Settings."""

from __future__ import annotations

from openharness.api.client import AnthropicApiClient, SupportsStreamingMessages
from openharness.api.codex_client import CodexApiClient
from openharness.api.copilot_client import COPILOT_DEFAULT_MODEL, CopilotClient
from openharness.api.openai_client import OpenAICompatibleClient
from openharness.api.provider import _is_gemini
from openharness.config.settings import Settings


def create_api_client(settings: Settings) -> SupportsStreamingMessages:
    """Return the appropriate client for the effective settings."""
    if _is_gemini(settings.model):
        from openharness.api.gemini_client import GeminiApiClient  # noqa: PLC0415

        if settings.vertex_project:
            return GeminiApiClient(
                project=settings.vertex_project,
                location=settings.vertex_location,
            )
        return GeminiApiClient(api_key=settings.resolve_api_key())

    if settings.api_format == "copilot":
        copilot_model = (
            COPILOT_DEFAULT_MODEL
            if settings.model in {"claude-sonnet-4-20250514", "claude-sonnet-4-6", "sonnet", "default"}
            else settings.model
        )
        return CopilotClient(model=copilot_model)

    if settings.provider == "openai_codex":
        auth = settings.resolve_auth()
        return CodexApiClient(auth_token=auth.value, base_url=settings.base_url)

    if settings.provider == "anthropic_claude":
        return AnthropicApiClient(
            auth_token=settings.resolve_auth().value,
            base_url=settings.base_url,
            claude_oauth=True,
            auth_token_resolver=lambda: settings.resolve_auth().value,
        )

    if settings.api_format == "openai":
        auth = settings.resolve_auth()
        return OpenAICompatibleClient(api_key=auth.value, base_url=settings.base_url)

    auth = settings.resolve_auth()
    return AnthropicApiClient(api_key=auth.value, base_url=settings.base_url)
