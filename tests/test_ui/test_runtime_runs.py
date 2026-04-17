"""Run artifact integration tests for interactive runtime sessions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openharness.api.client import ApiMessageCompleteEvent
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.ui.runtime import build_runtime, close_runtime, handle_line, start_runtime


class _StaticApiClient:
    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._text)]),
            usage=UsageSnapshot(input_tokens=2, output_tokens=3),
            stop_reason=None,
        )


@pytest.mark.asyncio
async def test_interactive_runtime_persists_run_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "0")

    bundle = await build_runtime(
        cwd=str(tmp_path),
        api_client=_StaticApiClient("runtime ok"),
    )
    assert bundle.run_context is not None

    async def _print_system(_message: str) -> None:
        return None

    async def _render_event(_event) -> None:
        return None

    async def _clear_output() -> None:
        return None

    await start_runtime(bundle)
    try:
        await handle_line(
            bundle,
            "Hello",
            print_system=_print_system,
            render_event=_render_event,
            clear_output=_clear_output,
        )
    finally:
        await close_runtime(bundle)

    run_dir = bundle.run_context.run_dir
    manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["run_id"] == bundle.run_context.run_id
    assert (run_dir / "messages.jsonl").exists()
    assert (run_dir / "events.jsonl").exists()

    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["last_assistant_text"] == "runtime ok"

    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["input_tokens"] == 2
    assert metrics["output_tokens"] == 3
