"""OpenHarness Experiments.

Provides declarative configuration, orchestration, and results collection
for evaluating agents across datasets.
"""

from openharness.experiments.manifest import ExperimentManifest, LegRecord, TrialRecord, LegStatus
from openharness.experiments.plan import ExperimentPlan, Leg, plan_experiment
from openharness.experiments.results import (
    ExperimentResultRow,
    ResultsSummary,
    collect_results,
    summarize_results,
    write_results,
)
from openharness.experiments.runner import run_experiment
from openharness.experiments.spec import ExperimentSpec, load_experiment_spec

__all__ = [
    "ExperimentManifest",
    "ExperimentPlan",
    "ExperimentResultRow",
    "ExperimentSpec",
    "Leg",
    "LegRecord",
    "LegStatus",
    "ResultsSummary",
    "TrialRecord",
    "collect_results",
    "load_experiment_spec",
    "plan_experiment",
    "run_experiment",
    "summarize_results",
    "write_results",
]
