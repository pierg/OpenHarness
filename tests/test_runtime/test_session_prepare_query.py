"""Tests for the template-variable contract in `AgentRuntime._prepare_query`."""

from __future__ import annotations

import pytest

from openharness.agents.config import AgentConfig
from openharness.agents.contracts import TaskDefinition
from openharness.config.settings import MemorySettings, Settings
from openharness.runtime.session import AgentRuntime
from openharness.workspace import CommandResult


class _MinimalWorkspace:
    """Just enough Workspace surface to satisfy _prepare_query."""

    def __init__(self, cwd: str = "/workspace") -> None:
        self._cwd = cwd

    @property
    def cwd(self) -> str:
        return self._cwd

    async def run_shell(self, command: str, **kwargs):  # pragma: no cover - unused
        del kwargs
        return CommandResult(stdout="ok\n")


@pytest.fixture(autouse=True)
def _fake_gemini_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_prepare_query` builds a real API client for the configured model.

    The Gemini settings path raises if no key is resolvable from env or config
    file; CI has neither, so seed a dummy value. No API call is ever made.
    """
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")


@pytest.fixture(autouse=True)
def quiet_runtime_context(monkeypatch):
    """Stub `build_runtime_system_prompt` so tests are deterministic."""
    captured: dict = {}

    def fake_build(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "[runtime-context]"

    monkeypatch.setattr("openharness.runtime.session.build_runtime_system_prompt", fake_build)
    return captured


def _runtime() -> AgentRuntime:
    settings = Settings(memory=MemorySettings(enabled=False))
    return AgentRuntime(workspace=_MinimalWorkspace(), settings=settings)


def _config(system: str, user: str = "{{ instruction }}", **extra) -> AgentConfig:
    base = {
        "name": "t",
        "model": "gemini-3.1-flash-lite-preview",
        "tools": ("bash",),
        "prompts": {"system": system, "user": user},
    }
    base.update(extra)
    return AgentConfig.model_validate(base)


def test_payload_is_not_spread_into_system_template(quiet_runtime_context):
    """A payload key like `secret` must not leak into the system surface."""
    runtime = _runtime()
    config = _config(system="role: planner. {{ openharness_system_context }}")
    task = TaskDefinition(instruction="do thing", payload={"secret": "leaks"})

    qctx, _ = runtime._prepare_query(config, task)

    assert "leaks" not in qctx.system_prompt
    assert "[runtime-context]" in qctx.system_prompt


def test_payload_is_spread_into_user_template():
    """User templates retain the legacy `{{ key }}` shorthand."""
    runtime = _runtime()
    config = _config(
        system="role. {{ openharness_system_context }}",
        user="{{ instruction }} :: {{ extra_key }}",
    )
    task = TaskDefinition(instruction="run", payload={"extra_key": "hello"})

    _, messages = runtime._prepare_query(config, task)

    assert "run :: hello" in messages[0].text


def test_user_template_can_iterate_payload_dict():
    """`payload` is also exposed as a dict for safe iteration."""
    runtime = _runtime()
    config = _config(
        system="role. {{ openharness_system_context }}",
        user="{% for k, v in payload.items() %}- {{ k }}={{ v }}\n{% endfor %}",
    )
    task = TaskDefinition(instruction="x", payload={"a": "1", "b": "2"})

    _, messages = runtime._prepare_query(config, task)

    assert "- a=1" in messages[0].text
    assert "- b=2" in messages[0].text


def test_system_template_does_not_receive_cwd_or_provider():
    """`cwd` and `provider` were dropped from render kwargs."""
    runtime = _runtime()
    config = _config(system="hello {{ cwd }} {{ provider }}")
    task = TaskDefinition(instruction="x")

    with pytest.raises(Exception):
        runtime._prepare_query(config, task)


def test_output_schema_instruction_is_template_variable():
    """Templates can interpolate `{{ output_schema_instruction }}` directly."""
    runtime = _runtime()
    config = _config(
        system="role: {{ output_schema_instruction }} END",
        user="{{ instruction }}",
    )
    task = TaskDefinition(instruction="x")

    qctx, _ = runtime._prepare_query(
        config,
        task,
        extra_template_vars={"_output_schema_instruction": "<<schema>>"},
    )

    assert "<<schema>>" in qctx.system_prompt
    # Exactly once: the runtime detects the variable and skips auto-append.
    assert qctx.system_prompt.count("<<schema>>") == 1


def test_output_schema_instruction_auto_appends_when_template_omits_it():
    """Back-compat: if the system template doesn't reference the variable,
    the schema block is appended at the end of the rendered system prompt."""
    runtime = _runtime()
    config = _config(system="role only. {{ openharness_system_context }}")
    task = TaskDefinition(instruction="x")

    qctx, _ = runtime._prepare_query(
        config,
        task,
        extra_template_vars={"_output_schema_instruction": "<<schema>>"},
    )

    assert qctx.system_prompt.endswith("<<schema>>")


def test_runtime_passes_tools_and_sections_to_context_builder(quiet_runtime_context):
    runtime = _runtime()
    config = _config(
        system="role. {{ openharness_system_context }}",
        system_context_sections=("base", "reasoning"),
    )
    task = TaskDefinition(instruction="x")

    runtime._prepare_query(config, task)

    kwargs = quiet_runtime_context["kwargs"]
    assert kwargs["available_tools"] == ("bash",)
    assert kwargs["include_sections"] == ("base", "reasoning")
