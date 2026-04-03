"""Client factory: select the right API client from Settings.

This is the single place that maps a ``Settings`` object to a concrete
``SupportsStreamingMessages`` implementation.  All call-sites that previously
constructed ``AnthropicApiClient`` directly should use ``create_api_client``
instead so that provider switching is transparent.
"""

from __future__ import annotations

from openharness.api.client import AnthropicApiClient, SupportsStreamingMessages
from openharness.api.provider import _is_gemini
from openharness.config.settings import Settings


def create_api_client(settings: Settings) -> SupportsStreamingMessages:
    """Return the appropriate client for the model configured in *settings*.

    - Gemini models (``gemini-*`` / ``*/gemini*``) → ``GeminiApiClient``.
      Vertex AI is used when ``settings.vertex_project`` is set; otherwise
      the Google AI Studio key is resolved via ``settings.resolve_api_key()``.
    - All other models → ``AnthropicApiClient``.
    """
    if _is_gemini(settings.model):
        from openharness.api.gemini_client import GeminiApiClient  # noqa: PLC0415

        if settings.vertex_project:
            return GeminiApiClient(
                project=settings.vertex_project,
                location=settings.vertex_location,
            )
        return GeminiApiClient(api_key=settings.resolve_api_key())

    return AnthropicApiClient(
        api_key=settings.resolve_api_key(),
        base_url=settings.base_url,
    )
