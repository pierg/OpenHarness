"""Tests for memory helpers."""

from __future__ import annotations

from pathlib import Path

from openharness.memory import (
    find_relevant_memories,
    get_memory_entrypoint,
    get_project_memory_dir,
    load_memory_prompt,
)


def test_memory_paths_are_stable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()

    memory_dir = get_project_memory_dir(project_dir)
    entrypoint = get_memory_entrypoint(project_dir)

    assert memory_dir.exists()
    assert entrypoint.parent == memory_dir


def test_load_memory_prompt_includes_entrypoint(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    entrypoint = get_memory_entrypoint(project_dir)
    entrypoint.write_text("# Index\n- [Testing](testing.md)\n", encoding="utf-8")

    prompt = load_memory_prompt(project_dir)

    assert prompt is not None
    assert "Persistent memory directory" in prompt
    assert "Testing" in prompt


def test_find_relevant_memories(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    memory_dir = get_project_memory_dir(project_dir)
    (memory_dir / "pytest_tips.md").write_text("Pytest markers and fixtures\n", encoding="utf-8")
    (memory_dir / "docker_notes.md").write_text("Docker compose caveats\n", encoding="utf-8")

    matches = find_relevant_memories("fix pytest fixtures", project_dir)

    assert matches
    assert matches[0].path.name == "pytest_tips.md"
