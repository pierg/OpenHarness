"""OpenHarness lab — autonomous experiment-pipeline support.

This package powers the lab's autonomous loop:

- `db`             — DuckDB connection + versioned migrations.
- `ingest`         — read a `runs/experiments/<id>/` directory and persist
                     trial-grain rows into the lab DB.
- `lab_docs`       — deterministic markdown helpers used by the five
                     lab/* skills (so file mutations are validated once
                     in Python, not re-derived in every prompt).
- `cli`            — `uv run lab ...` Typer entry point. The single
                     interface every skill (and the orchestrator) calls
                     for deterministic mutations.

See `lab/README.md` and the `.agents/skills/lab/SKILL.md` family for
the human-facing audit trail this package supports.
"""

from openharness.lab.paths import (
    LAB_DB_PATH,
    LAB_LOGS_DIR,
    LAB_ROOT,
    LAB_RUNS_ROOT,
    ORCHESTRATOR_LOCK_PATH,
)

__all__ = [
    "LAB_ROOT",
    "LAB_RUNS_ROOT",
    "LAB_DB_PATH",
    "LAB_LOGS_DIR",
    "ORCHESTRATOR_LOCK_PATH",
]
