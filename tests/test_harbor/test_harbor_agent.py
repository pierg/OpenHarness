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

pytest.importorskip(
    "harbor", reason="Install Harbor test dependencies with `uv sync --extra harbor`."
)
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


def _final_text_client(text: str = "Done.") -> FakeApiClient:
    return FakeApiClient(
        [
            _FakeResponse(
                message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
                usage=UsageSnapshot(input_tokens=5, output_tokens=3),
            )
        ]
    )


async def _run_router_trial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    trial_id: str,
) -> tuple[AgentContext, Path]:
    trial_dir = tmp_path / trial_id
    trial_dir.mkdir()
    logs_dir = trial_dir / "agent"
    logs_dir.mkdir()

    environment = FakeEnvironment()
    context = AgentContext()
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "0")
    agent = OpenHarnessHarborAgent(
        logs_dir=logs_dir,
        agent_name="harbor_router_test_agent",
        model_name="gemini-3.1-pro-preview",
        api_client=_final_text_client(),
        agent_config_yaml="""\
name: harbor_router_test_agent
architecture: simple
model: gemini-3.1-pro-preview
max_turns: 4
max_tokens: 1024
extras:
  model_router:
    default_model: gemini-3-flash-preview
tools: []
prompts:
  system: |
    {{ openharness_system_context }}
  user: |
    {{ instruction }}
""",
        run_id="job-route123456",
        run_root=str(tmp_path),
    )

    await agent.run("Return a short completion.", environment, context)

    return context, trial_dir


def _logged_model_request(trial_dir: Path) -> dict[str, object]:
    events = [json.loads(line) for line in (trial_dir / "events.jsonl").read_text().splitlines()]
    model_requests = [row for row in events if row["type"] == "model_request"]
    assert len(model_requests) == 1
    return model_requests[0]


@pytest.mark.asyncio
async def test_openharness_harbor_agent_solves_hello_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial_dir = tmp_path / "trial-build-foo__abc123"
    trial_dir.mkdir()
    logs_dir = trial_dir / "agent"
    logs_dir.mkdir()
    api_client = FakeApiClient(
        [
            _FakeResponse(
                message=ConversationMessage(
                    role="assistant",
                    content=[
                        ToolUseBlock(
                            id="toolu_write",
                            name="write_file",
                            input={"path": "hello.txt", "content": "Hello, world!\n"},
                        )
                    ],
                ),
                usage=UsageSnapshot(input_tokens=11, output_tokens=7),
            ),
            _FakeResponse(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="Done.")]),
                usage=UsageSnapshot(input_tokens=5, output_tokens=3),
            ),
        ]
    )
    environment = FakeEnvironment()
    context = AgentContext()
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "0")
    agent = OpenHarnessHarborAgent(
        logs_dir=logs_dir,
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
        run_id="job-test123456",
        run_root=str(tmp_path),
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
    assert context.metadata["run_id"] == trial_dir.name
    assert context.metadata["run_root"] == str(trial_dir)
    assert context.metadata["trace_url"] is None
    assert context.metadata["summary"]["final_text"] == "Done."

    manifest = json.loads((trial_dir / "run.json").read_text())
    assert manifest["run_id"] == trial_dir.name
    assert manifest["metadata"]["harbor_job_id"] == "job-test123456"

    events_path = trial_dir / "events.jsonl"
    messages_path = trial_dir / "messages.jsonl"
    assert events_path.exists()
    assert messages_path.exists()

    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert any(row["type"] == "tool_started" for row in events)
    assert any(row["type"] == "tool_completed" for row in events)

    msgs = [json.loads(line) for line in messages_path.read_text().splitlines()]
    assert msgs[0]["role"] == "user"
    assert msgs[-1]["role"] == "assistant"

    trajectory_path = logs_dir / "trajectory.json"
    assert trajectory_path.exists()
    trajectory = json.loads(trajectory_path.read_text())
    assert trajectory["schema_version"] == "ATIF-v1.6"
    assert trajectory["session_id"] == trial_dir.name
    assert trajectory["agent"]["name"] == "harbor_test_agent"
    assert len(trajectory["steps"]) >= 2
    assert trajectory["steps"][0]["source"] == "user"
    assert any(s["source"] == "agent" for s in trajectory["steps"])


@pytest.mark.asyncio
async def test_openharness_harbor_agent_uses_router_default_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, trial_dir = await _run_router_trial(
        tmp_path,
        monkeypatch,
        trial_id="log-summary-date-ranges__abc123",
    )

    assert _logged_model_request(trial_dir)["model"] == "gemini-3-flash-preview"
    assert context.metadata is not None
    assert context.metadata["model"] == "gemini-3-flash-preview"

    trajectory = json.loads((trial_dir / "agent" / "trajectory.json").read_text())
    assert trajectory["agent"]["model_name"] == "gemini-3-flash-preview"


def test_openharness_harbor_agent_rejects_task_identity_model_router(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trial_dir = tmp_path / "fix-ocaml-gc__abc123"
    trial_dir.mkdir()
    logs_dir = trial_dir / "agent"
    logs_dir.mkdir()
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "0")

    with pytest.raises(ValueError, match="exact benchmark task identity"):
        OpenHarnessHarborAgent(
            logs_dir=logs_dir,
            agent_name="harbor_oracle_router_test_agent",
            model_name="gemini-3.1-pro-preview",
            api_client=_final_text_client(),
            agent_config_yaml="""\
name: harbor_oracle_router_test_agent
architecture: simple
model: gemini-3.1-pro-preview
max_turns: 4
max_tokens: 1024
extras:
  model_router:
    default_model: gemini-3-flash-preview
    task_models:
      fix-ocaml-gc: gemini-3.1-pro-preview
tools: []
prompts:
  system: |
    {{ openharness_system_context }}
  user: |
    {{ instruction }}
""",
            run_id="job-route123456",
            run_root=str(tmp_path),
        )
