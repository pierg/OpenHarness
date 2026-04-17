"""Experiment execution backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from openharness.experiments.manifest import LegStatus, TrialRecord
from openharness.experiments.plan import Leg
from openharness.experiments.spec import ExperimentSpec


class LegContext(BaseModel):
    experiment_root: Path  # absolute at runtime; never persisted
    leg_dir: Path  # absolute at runtime; = root / "legs" / leg.leg_id
    env: dict[str, str]  # environment variables
    dry_run: bool
    resume: bool
    spec: ExperimentSpec
    instance_id: str = ""  # experiment instance id (timestamped); used for tracing

    model_config = ConfigDict(extra="forbid", frozen=True)


class LegOutcome(BaseModel):
    status: LegStatus
    trials: tuple[TrialRecord, ...] = ()
    started_at: datetime
    finished_at: datetime
    error: str | None = None
    traceback: str | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class Backend(ABC):
    """Execution backend for experiment legs."""

    @abstractmethod
    async def run_leg(self, leg: Leg, ctx: LegContext) -> LegOutcome:
        """Execute a leg and return its outcome."""

    @abstractmethod
    def is_leg_complete(self, leg: Leg, ctx: LegContext) -> bool:
        """Check if a leg is fully complete (used for resume)."""
