"""Portable relative path types for experiment manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer


def _reject_absolute(p: Path) -> Path:
    if p.is_absolute():
        raise ValueError(f"Path must be relative, got absolute path: {p}")
    return p


RelPath = Annotated[
    Path,
    AfterValidator(_reject_absolute),
    PlainSerializer(lambda p: p.as_posix(), when_used="always"),
]


def resolve_rel(root: str | Path, rel: RelPath | str | Path) -> Path:
    """Resolve a relative path against an experiment root."""
    root_path = Path(root).expanduser().resolve()
    rel_path = Path(rel)
    if rel_path.is_absolute():
        try:
            rel_path.relative_to(root_path)
            return rel_path
        except ValueError:
            raise ValueError(f"Absolute path {rel_path} is not under root {root_path}")
    return (root_path / rel_path).resolve()


def make_rel(root: str | Path, path: str | Path) -> Path:
    """Make an absolute path relative to *root*. Raises if *path* is outside *root*."""
    root_path = Path(root).expanduser().resolve()
    full_path = Path(path).expanduser().resolve()
    return full_path.relative_to(root_path)


def try_make_rel(root: str | Path, path: str | Path) -> Path | None:
    """Best-effort relative path; returns None if *path* is outside *root*."""
    try:
        return make_rel(root, path)
    except ValueError:
        return None
