"""Tests for OpenHarnessHarborAgent."""

from __future__ import annotations

import json
import importlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from openharness.api.client import ApiMessageCompleteEvent
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock

pytest.importorskip("harbor", reason="Install Harbor test dependencies with `uv sync --extra harbor`.")
ExecResult = pytest.importorskip("harbor.environments.base").ExecResult
AgentContext = pytest.importorskip("harbor.models.agent.context").AgentContext
OpenHarnessHarborAgent = importlib.import_module("openharness.harbor").OpenHarnessHarborAgent


@dataclass
class _FakeResponse:
    message: ConversationMessage
    usage: UsageSnapshot = None

    def __post_init__(self):
        if self.usage is None:
            self.usage = UsageSnapshot()


class FakeApiClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)

    async def stream_message(self, request):
        del request
        resp = self._responses.pop(0)
        yield ApiMessageCompleteEvent(message=resp.message, usage=resp.usage, stop_reason=None)


class FakeEnvironment:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        del command, cwd, env, timeout_sec, user
        return ExecResult(stdout="", stderr="", return_code=0)

    async def upload_file(self, source_path, target_path):
        self.files[target_path] = Path(source_path).read_bytes()

    async def download_file(self, source_path, target_path):
        Path(target_path).write_bytes(self.files[source_path])

    async def is_dir(self, path, user=None):
        del user
        return any(k.startswith(path.rstrip("/") + "/") for k in self.files)

    async def is_file(self, path, user=None):
        del user
        return path in self.files


@pytest.mark.asyncio
async def test_openharness_harbor_agent_solves_hello_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "run-root"
    run_root.mkdir()
    api_client = FakeApiClient([
        _FakeResponse(
            message=ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(
                    id="toolu_write", name="write_file",
                    input={"path": "hello.txt", "content": "Hello, world!\n"},
                )],
            ),
            usage=UsageSnapshot(input_tokens=11, output_tokens=7),
        ),
        _FakeResponse(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="Done.")]),
            usage=UsageSnapshot(input_tokens=5, output_tokens=3),
        ),
    ])
    environment = FakeEnvironment()
    context = AgentContext()
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "0")
    agent = OpenHarnessHarborAgent(
        logs_dir=tmp_path / "agent",
        agent_name="harbor_test_agent",
        model_name="claude-test",
        api_client=api_client,
        agent_config_yaml="""\
name: harbor_test_agent
architecture: simple
model: claude-test
max_turns: 4
max_tokens: 1024
tools:
  - write_file
prompts:
  system: |
    {{ openharness_system_context }}
  user: |
    {{ instruction }}
""",
        run_id="run-test123456",
        run_root=run_root,
    )

    await agent.run(
        'Create a file called hello.txt with "Hello, world!" as the content.',
        environment,
        context,
    )

    assert environment.files["/app/hello.txt"] == b"Hello, world!\n"
    assert context.n_input_tokens == 16
    assert context.n_output_tokens == 10
    assert context.metadata is not None
    assert context.metadata["run_id"] == "run-test123456"
    assert context.metadata["run_root"] == str(run_root)
    assert context.metadata["trace_url"] is None
    assert context.metadata["summary"]["final_text"] == "Done."
    assert json.loads((run_root / "run.json").read_text())["run_id"] == "run-test123456"

    events_path = run_root / "events.jsonl"
    messages_path = run_root / "messages.jsonl"
    assert events_path.exists()
    assert messages_path.exists()

    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert any(row["type"] == "tool_started" for row in events)
    assert any(row["type"] == "tool_completed" for row in events)

    msgs = [json.loads(line) for line in messages_path.read_text().splitlines()]
    assert msgs[0]["role"] == "user"
    assert msgs[-1]["role"] == "assistant"
