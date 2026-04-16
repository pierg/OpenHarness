"""OpenHarness Experiments.

Provides declarative configuration, orchestration, and results collection
for evaluating agents across datasets.
"""

from openharness.experiments.manifest import (
    ExperimentManifest,
    LegAggregate,
    LegRecord,
    LegResultStatus,
    LegStatus,
    TrialError,
    TrialErrorPhase,
    TrialRecord,
)
from openharness.experiments.plan import ExperimentPlan, Leg, plan_experiment
from openharness.experiments.results import (
    AgentSummary,
    ExperimentResultRow,
    ResultsSummary,
    collect_results,
    summarize_results,
    write_results,
)
from openharness.experiments.runner import run_experiment
from openharness.experiments.spec import (
    ExperimentSpec,
    LoadedExperimentSpec,
    load_experiment_spec,
    load_experiment_spec_full,
)

__all__ = [
    "AgentSummary",
    "ExperimentManifest",
    "ExperimentPlan",
    "ExperimentResultRow",
    "ExperimentSpec",
    "Leg",
    "LegAggregate",
    "LegRecord",
    "LegResultStatus",
    "LegStatus",
    "LoadedExperimentSpec",
    "ResultsSummary",
    "TrialError",
    "TrialErrorPhase",
    "TrialRecord",
    "collect_results",
    "load_experiment_spec",
    "load_experiment_spec_full",
    "plan_experiment",
    "run_experiment",
    "summarize_results",
    "write_results",
]
