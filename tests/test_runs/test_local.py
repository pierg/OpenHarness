"""Tests for high-level run orchestration helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openharness.api.client import ApiMessageCompleteEvent
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.runs import AgentSpec, InlineTaskSpec, LocalAgentRunSpec, run_local_agent


class _StaticApiClient:
    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._text)]),
            usage=UsageSnapshot(input_tokens=5, output_tokens=7),
            stop_reason=None,
        )


@pytest.mark.asyncio
async def test_run_local_agent_writes_run_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "0")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = await run_local_agent(
        LocalAgentRunSpec(
            cwd=workspace,
            run_cwd=tmp_path,
            task=InlineTaskSpec(instruction="Say hi"),
            agent=AgentSpec(name="basic"),
            run_id="run-local123456",
            api_client=_StaticApiClient("done"),
        )
    )

    assert result.run_id == "run-local123456"
    assert result.run_dir == tmp_path / "runs" / "run-local123456"
    assert result.manifest_path.exists()
    assert result.result_path is not None and result.result_path.exists()
    assert result.metrics_path is not None and result.metrics_path.exists()
    assert (result.run_dir / "messages.jsonl").exists()
    assert (result.run_dir / "events.jsonl").exists()

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["run_id"] == "run-local123456"
    assert manifest["schema_version"] == 1
    assert manifest["paths"]["anchor"] == "run_dir"
    assert manifest["paths"]["workspace"] == "../../workspace"
    assert manifest["paths"]["messages"] == "messages.jsonl"
    assert manifest["paths"]["events"] == "events.jsonl"
    assert manifest["paths"]["results"] == "results.json"
    assert manifest["paths"]["metrics"] == "metrics.json"

    results = json.loads(result.result_path.read_text(encoding="utf-8"))
    assert results["final_text"] == "done"

    metrics = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    assert metrics["input_tokens"] == 5
    assert metrics["output_tokens"] == 7
