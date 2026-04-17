"""Simple heuristic memory search."""

from __future__ import annotations

import re
from pathlib import Path

from openharness.memory.scan import scan_memory_files
from openharness.memory.types import MemoryHeader


def find_relevant_memories(
    query: str,
    cwd: str | Path,
    *,
    max_results: int = 5,
) -> list[MemoryHeader]:
    """Return the memory files whose titles or descriptions overlap the query."""
    tokens = {token for token in re.findall(r"[A-Za-z0-9_]+", query.lower()) if len(token) >= 3}
    if not tokens:
        return []

    scored: list[tuple[int, MemoryHeader]] = []
    for header in scan_memory_files(cwd, max_files=100):
        haystack = f"{header.title} {header.description}".lower()
        score = sum(1 for token in tokens if token in haystack)
        if score:
            scored.append((score, header))
    scored.sort(key=lambda item: (-item[0], -item[1].modified_at))
    return [header for _, header in scored[:max_results]]
