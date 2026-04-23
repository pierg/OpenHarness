"""DuckDB connection + versioned migrations for the lab pipeline.

The lab DB lives at `runs/lab/trials.duckdb`. There is exactly one
writer (the orchestrator daemon, plus humans/agents invoking the `uv
run lab` CLI) and any number of readers (the lab web UI, ad hoc
queries). DuckDB locks the file for the writer process, so readers
MUST connect with `read_only=True` — see `connect_read_only`.

Migrations are SQL files in `migrations/`, applied lexically and
recorded in `_lab_migrations` so re-running is a no-op.
"""

from __future__ import annotations

import contextlib
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import duckdb

from openharness.lab.paths import LAB_DB_PATH, ensure_lab_runs_dir

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _migration_files() -> list[Path]:
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def _ensure_migrations_table(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _lab_migrations (
            id          TEXT PRIMARY KEY,
            sha256      TEXT NOT NULL,
            applied_at  TIMESTAMPTZ NOT NULL
        )
        """
    )


def apply_migrations(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Apply any unapplied migration files. Returns the list of ids applied."""
    _ensure_migrations_table(conn)
    applied: set[str] = {
        row[0] for row in conn.execute("SELECT id FROM _lab_migrations").fetchall()
    }
    newly_applied: list[str] = []
    for path in _migration_files():
        mig_id = path.stem
        if mig_id in applied:
            continue
        sql = path.read_text()
        sha = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        conn.execute("BEGIN")
        try:
            conn.execute(sql)
            conn.execute(
                "INSERT INTO _lab_migrations (id, sha256, applied_at) VALUES (?, ?, ?)",
                [mig_id, sha, datetime.now(timezone.utc)],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        newly_applied.append(mig_id)
    return newly_applied


def connect(*, db_path: Path | None = None, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the lab DB. Auto-creates the parent directory and runs migrations.

    Set `read_only=True` for the web UI and any concurrent reader so the
    orchestrator's writer connection isn't blocked.
    """
    path = db_path or LAB_DB_PATH
    if not read_only:
        ensure_lab_runs_dir()
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.exists():
        raise FileNotFoundError(
            f"Lab DB does not exist at {path}. Run `uv run lab init` (or any write "
            "command) first."
        )
    conn = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        apply_migrations(conn)
    return conn


@contextlib.contextmanager
def writer(*, db_path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """Context-managed writer connection."""
    conn = connect(db_path=db_path, read_only=False)
    try:
        yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def reader(*, db_path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    """Context-managed read-only connection."""
    conn = connect(db_path=db_path, read_only=True)
    try:
        yield conn
    finally:
        conn.close()
