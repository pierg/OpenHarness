"""Experiment logging utilities."""

from __future__ import annotations

import logging
from pathlib import Path


def setup_experiment_logging(log_path: Path) -> None:
    """Setup a file handler for the experiment runner log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("openharness")
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)

    if not any(
        isinstance(h, logging.FileHandler) and h.baseFilename == str(log_path)
        for h in logger.handlers
    ):
        logger.addHandler(file_handler)
