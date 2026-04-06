"""Registry of workflow topology implementations."""

from __future__ import annotations

from openharness.workflows.contracts import WorkflowTopology
from openharness.workflows.topologies import (
    CoordinatorWorkerTopology,
    FanoutJoinTopology,
    SingleTopology,
)

_TOPOLOGIES: dict[str, WorkflowTopology] = {
    "single": SingleTopology(),
    "fanout_join": FanoutJoinTopology(),
    "coordinator_worker": CoordinatorWorkerTopology(),
}


def register_topology(name: str, topology: WorkflowTopology) -> None:
    """Register a custom workflow topology."""
    _TOPOLOGIES[name] = topology


def get_topology(name: str) -> WorkflowTopology:
    """Return a workflow topology by name."""
    return _TOPOLOGIES[name]


def list_topologies() -> list[str]:
    """Return available topology names."""
    return sorted(_TOPOLOGIES)

