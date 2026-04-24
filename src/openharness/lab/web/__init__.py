"""Lab web UI — read-only operator + researcher console (Phase 1).

Run with:

    uv run lab webui [--port 8080] [--host 127.0.0.1]

Internally a FastAPI app served by uvicorn. Reads the lab DuckDB
read-only via ``openharness.lab.db.reader`` and reads the markdown
audit surface (``lab/*.md``), per-experiment artefacts under
``runs/experiments/<id>/``, and per-spawn logs under
``runs/lab/logs/`` directly from disk. Token and cost telemetry is
served from the lab DB: agent-run usage comes from ``trials`` and
pipeline model-call usage comes from cached ``spawns`` fields.

This package never writes to the lab; mutations stay in
``uv run lab ...`` per the file-is-truth / DB-is-cache invariant
documented in ``lab/OPERATIONS.md``. Phase 3 will add a single,
whitelisted ``POST /api/cmd`` endpoint that shells to that same CLI.
"""

from __future__ import annotations

from openharness.lab.web.server import run

__all__ = ["run"]
