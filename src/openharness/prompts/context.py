"""Higher-level system prompt assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from openharness.config.paths import get_project_issue_file, get_project_pr_comments_file
from openharness.config.settings import Settings
from openharness.coordinator.coordinator_mode import (
    get_coordinator_system_prompt,
    is_coordinator_mode,
)
from openharness.memory import find_relevant_memories, load_memory_prompt
from openharness.personalization.rules import load_local_rules
from openharness.prompts.claudemd import load_claude_md_prompt
from openharness.prompts.system_prompt import build_system_prompt
from openharness.skills.loader import load_skill_registry

# All section names that ``build_runtime_system_prompt`` can emit. The
# ``base`` section is always present; the rest are opt-in subject to the
# ``include_sections`` filter, the session mode, registered tools, and
# whether the underlying data exists (e.g. CLAUDE.md present on disk).
SYSTEM_CONTEXT_SECTIONS: tuple[str, ...] = (
    "base",
    "session_mode",
    "reasoning",
    "skills",
    "delegation",
    "project_instructions",
    "local_rules",
    "issue_context",
    "pr_comments",
    "memory",
)

# Sections injected by default in autonomous mode. Anything tied to the
# *host* developer environment (CLAUDE.md, local rules, memory) and any
# guidance about tools the agent does not have is dropped to keep the
# prompt small and contextually accurate.
_AUTONOMOUS_DEFAULT_SECTIONS: frozenset[str] = frozenset(
    {"base", "session_mode", "reasoning", "skills", "delegation"}
)


def _build_skills_section(
    cwd: str | Path,
    *,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    settings: Settings | None = None,
) -> str | None:
    """Build a system prompt section listing available skills."""
    registry = load_skill_registry(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=settings,
    )
    skills = registry.list_skills()
    if not skills:
        return None
    lines = [
        "# Available Skills",
        "",
        "The following skills are available via the `skill` tool. "
        'When a user\'s request matches a skill, invoke it with `skill(name="<skill_name>")` '
        "to load detailed instructions before proceeding.",
        "",
    ]
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
    return "\n".join(lines)


def _build_delegation_section() -> str:
    """Build a concise section describing delegation and worker usage."""
    return "\n".join(
        [
            "# Delegation And Subagents",
            "",
            "OpenHarness can delegate background work with the `agent` tool.",
            "Use it when the user explicitly asks for a subagent, background worker, or parallel investigation, "
            "or when the task clearly benefits from splitting off a focused worker.",
            "",
            "Default pattern:",
            '- Spawn with `agent(description=..., prompt=..., subagent_type="worker")`.',
            "- Inspect running or recorded workers with `/agents`.",
            "- Inspect one worker in detail with `/agents show TASK_ID`.",
            "- Send follow-up instructions with `send_message(task_id=..., message=...)`.",
            "- Read worker output with `task_output(task_id=...)`.",
            "",
            "Prefer a normal direct answer for simple tasks. Use subagents only when they materially help.",
        ]
    )


def _resolve_active_sections(
    settings: Settings,
    include_sections: Iterable[str] | None,
) -> frozenset[str]:
    """Return the set of section names this call is allowed to emit."""
    if include_sections is not None:
        return frozenset(include_sections) | {"base"}
    if settings.session_mode == "autonomous":
        return _AUTONOMOUS_DEFAULT_SECTIONS
    return frozenset(SYSTEM_CONTEXT_SECTIONS)


def build_runtime_system_prompt(
    settings: Settings,
    *,
    cwd: str | Path,
    latest_user_prompt: str | None = None,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    available_tools: Iterable[str] | None = None,
    include_sections: Iterable[str] | None = None,
) -> str:
    """Build the runtime system prompt with project instructions and memory.

    Args:
        settings: Active settings; ``session_mode`` controls the base
            prompt and the default section set.
        cwd: Working directory the prompt should describe.
        latest_user_prompt: Used to retrieve relevant memories when memory
            is enabled.
        extra_skill_dirs / extra_plugin_roots: Forwarded to the skill
            registry loader.
        available_tools: Names of the tools the *current agent* has
            registered. The ``delegation`` section (which advertises the
            ``agent`` tool) and the ``skills`` section (which advertises
            the ``skill`` tool) are dropped automatically when the
            corresponding tool is not in this set. Pass ``None`` to skip
            tool-aware filtering.
        include_sections: Optional explicit allowlist of section names
            (see ``SYSTEM_CONTEXT_SECTIONS``). When provided it overrides
            the session-mode default. ``"base"`` is always included.
    """
    active = _resolve_active_sections(settings, include_sections)

    tools_set: frozenset[str] | None = (
        frozenset(available_tools) if available_tools is not None else None
    )

    sections: list[str] = []

    # ---- base ----------------------------------------------------------
    if is_coordinator_mode():
        sections.append(get_coordinator_system_prompt())
    elif settings.system_prompt is not None:
        sections.append(
            build_system_prompt(
                custom_prompt=settings.system_prompt,
                cwd=str(cwd),
                session_mode=settings.session_mode,
            )
        )
    else:
        sections.append(build_system_prompt(cwd=str(cwd), session_mode=settings.session_mode))

    coord = is_coordinator_mode()

    # ---- session_mode --------------------------------------------------
    if "session_mode" in active and settings.fast_mode:
        sections.append(
            "# Session Mode\nFast mode is enabled. Prefer concise replies, "
            "minimal tool use, and quicker progress over exhaustive exploration."
        )

    # ---- reasoning -----------------------------------------------------
    if "reasoning" in active:
        sections.append(
            "# Reasoning Settings\n"
            f"- Effort: {settings.effort}\n"
            f"- Passes: {settings.passes}\n"
            "Adjust depth and iteration count to match these settings while still completing the task."
        )

    # ---- skills (requires the `skill` tool) ----------------------------
    if "skills" in active and not coord:
        if tools_set is None or "skill" in tools_set:
            skills_section = _build_skills_section(
                cwd,
                extra_skill_dirs=extra_skill_dirs,
                extra_plugin_roots=extra_plugin_roots,
                settings=settings,
            )
            if skills_section:
                sections.append(skills_section)

    # ---- delegation (requires the `agent` tool) ------------------------
    if "delegation" in active and not coord:
        if tools_set is None or "agent" in tools_set:
            sections.append(_build_delegation_section())

    # ---- project_instructions (CLAUDE.md / AGENTS.md) ------------------
    if "project_instructions" in active:
        claude_md = load_claude_md_prompt(cwd)
        if claude_md:
            sections.append(claude_md)

    # ---- local_rules ---------------------------------------------------
    if "local_rules" in active:
        local_rules = load_local_rules()
        if local_rules:
            sections.append(f"# Local Environment Rules\n\n{local_rules}")

    # ---- issue_context / pr_comments ----------------------------------
    issue_pairs = []
    if "issue_context" in active:
        issue_pairs.append(("Issue Context", get_project_issue_file(cwd)))
    if "pr_comments" in active:
        issue_pairs.append(("Pull Request Comments", get_project_pr_comments_file(cwd)))
    for title, path in issue_pairs:
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"# {title}\n\n```md\n{content[:12000]}\n```")

    # ---- memory --------------------------------------------------------
    if "memory" in active and settings.memory.enabled:
        memory_section = load_memory_prompt(
            cwd,
            max_entrypoint_lines=settings.memory.max_entrypoint_lines,
        )
        if memory_section:
            sections.append(memory_section)

        if latest_user_prompt:
            relevant = find_relevant_memories(
                latest_user_prompt,
                cwd,
                max_results=settings.memory.max_files,
            )
            if relevant:
                lines = ["# Relevant Memories"]
                for header in relevant:
                    content = header.path.read_text(encoding="utf-8", errors="replace").strip()
                    lines.extend(
                        [
                            "",
                            f"## {header.path.name}",
                            "```md",
                            content[:8000],
                            "```",
                        ]
                    )
                sections.append("\n".join(lines))

    return "\n\n".join(section for section in sections if section.strip())
