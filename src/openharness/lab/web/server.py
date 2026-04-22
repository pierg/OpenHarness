"""Uvicorn entry point invoked by ``uv run lab webui``."""

from __future__ import annotations

import logging

import uvicorn

from openharness.lab.web.app import create_app

log = logging.getLogger(__name__)


def run(*, host: str = "127.0.0.1", port: int = 8765, reload: bool = False,
        log_level: str = "info") -> None:
    """Start the lab web UI on (host, port).

    ``reload=True`` is for development only — it makes uvicorn re-import
    the app on file changes; we point it at the package factory.
    """
    if reload:
        uvicorn.run(
            "openharness.lab.web.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=True,
            log_level=log_level,
        )
        return

    uvicorn.run(create_app(), host=host, port=port, log_level=log_level)
