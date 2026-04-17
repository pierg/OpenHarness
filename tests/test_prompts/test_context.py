"""Tests for openharness.prompts.context section filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.config.settings import MemorySettings, Settings
from openharness.prompts import context as ctx
from openharness.prompts.context import build_runtime_system_prompt


@pytest.fixture
def isolated_cwd(tmp_path: Path) -> Path:
    """Return an empty tmp dir with no CLAUDE.md / memory / rules."""
    return tmp_path


@pytest.fixture(autouse=True)
def isolate_runtime_prompt(monkeypatch):
    """Stop the runtime prompt from picking up host-machine state."""
    monkeypatch.setattr(ctx, "load_local_rules", lambda: "")
    # Ensure no leftover coordinator-mode env var from sibling tests.
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    yield


def _settings(**overrides) -> Settings:
    base = Settings(memory=MemorySettings(enabled=False))
    if overrides:
        return base.model_copy(update=overrides)
    return base


def test_interactive_default_includes_delegation_and_skills(isolated_cwd, monkeypatch):
    """Interactive mode injects delegation guidance unconditionally."""
    monkeypatch.setattr(
        ctx, "_build_skills_section", lambda *a, **kw: "# Available Skills\n\n- foo: bar"
    )
    prompt = build_runtime_system_prompt(_settings(), cwd=isolated_cwd)
    assert "Delegation And Subagents" in prompt
    assert "Available Skills" in prompt


def test_autonomous_uses_autonomous_base_prompt(isolated_cwd):
    prompt = build_runtime_system_prompt(_settings(session_mode="autonomous"), cwd=isolated_cwd)
    # Autonomous prompt explicitly states there's no human.
    assert "no human" in prompt.lower()
    # Interactive base prompt has the phrase "interactive agent that helps users".
    assert "interactive agent" not in prompt.lower()


def test_autonomous_drops_host_personalization_sections(isolated_cwd, monkeypatch):
    """Autonomous mode skips CLAUDE.md / local rules / memory sections."""
    monkeypatch.setattr(ctx, "load_claude_md_prompt", lambda cwd: "# CLAUDE\nshould not appear")
    monkeypatch.setattr(ctx, "load_local_rules", lambda: "should not appear either")
    prompt = build_runtime_system_prompt(_settings(session_mode="autonomous"), cwd=isolated_cwd)
    assert "should not appear" not in prompt


def test_available_tools_filters_delegation(isolated_cwd):
    """Without the 'agent' tool, the delegation section is dropped."""
    prompt = build_runtime_system_prompt(
        _settings(),
        cwd=isolated_cwd,
        available_tools=("bash", "read_file"),
    )
    assert "Delegation And Subagents" not in prompt


def test_available_tools_keeps_delegation_when_agent_present(isolated_cwd):
    prompt = build_runtime_system_prompt(
        _settings(),
        cwd=isolated_cwd,
        available_tools=("bash", "agent"),
    )
    assert "Delegation And Subagents" in prompt


def test_available_tools_filters_skills(isolated_cwd, monkeypatch):
    """Without the 'skill' tool, the skills section is dropped."""
    monkeypatch.setattr(
        ctx, "_build_skills_section", lambda *a, **kw: "# Available Skills\n\n- foo: bar"
    )
    prompt = build_runtime_system_prompt(
        _settings(),
        cwd=isolated_cwd,
        available_tools=("bash",),
    )
    assert "Available Skills" not in prompt


def test_explicit_include_sections_overrides_session_default(isolated_cwd):
    """`include_sections=("base",)` strips everything but the base prompt."""
    prompt = build_runtime_system_prompt(
        _settings(),
        cwd=isolated_cwd,
        include_sections=("base",),
    )
    assert "Reasoning Settings" not in prompt
    assert "Delegation And Subagents" not in prompt
    # Base is always present.
    assert "OpenHarness" in prompt


def test_include_sections_always_keeps_base(isolated_cwd):
    """Even an empty include_sections still emits 'base'."""
    prompt = build_runtime_system_prompt(
        _settings(),
        cwd=isolated_cwd,
        include_sections=(),
    )
    assert "OpenHarness" in prompt
