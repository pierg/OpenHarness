"""Scan project memory files."""

from __future__ import annotations

from pathlib import Path

from openharness.memory.paths import get_project_memory_dir
from openharness.memory.types import MemoryHeader


def scan_memory_files(cwd: str | Path, *, max_files: int = 50) -> list[MemoryHeader]:
    """Return memory headers sorted by newest first."""
    memory_dir = get_project_memory_dir(cwd)
    headers: list[MemoryHeader] = []
    for path in memory_dir.glob("*.md"):
        if path.name == "MEMORY.md":
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        title = path.stem
        description = ""
        for line in lines[:10]:
            stripped = line.strip()
            if stripped:
                description = stripped[:160]
                break
        headers.append(
            MemoryHeader(
                path=path,
                title=title,
                description=description,
                modified_at=path.stat().st_mtime,
            )
        )
    headers.sort(key=lambda item: item.modified_at, reverse=True)
    return headers[:max_files]
