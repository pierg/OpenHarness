"""First-class workflow/topology layer for OpenHarness."""

from __future__ import annotations

from openharness.workflows.catalog import (
    CatalogWorkflowSpec,
    get_catalog_workflow_spec,
    get_catalog_workflow_specs,
    iter_catalog_workflow_specs,
)
from openharness.workflows.contracts import (
    RoleRunResult,
    WorkerHandle,
    WorkflowMessage,
    WorkflowRunResult,
    WorkflowTopology,
)
from openharness.workflows.engine import WorkflowEngine
from openharness.workflows.registry import get_topology, list_topologies, register_topology
from openharness.workflows.runtime import WorkflowRuntime
from openharness.workflows.specs import (
    CoordinationSpec,
    LifecycleSpec,
    MessagingSpec,
    ObservabilitySpec,
    PermissionsSpec,
    RoleSpec,
    RoutingPolicy,
    WorkflowSpec,
)

__all__ = [
    "CatalogWorkflowSpec",
    "CoordinationSpec",
    "LifecycleSpec",
    "MessagingSpec",
    "ObservabilitySpec",
    "PermissionsSpec",
    "RoleRunResult",
    "RoleSpec",
    "RoutingPolicy",
    "WorkerHandle",
    "WorkflowEngine",
    "WorkflowMessage",
    "WorkflowRunResult",
    "WorkflowRuntime",
    "WorkflowSpec",
    "WorkflowTopology",
    "get_catalog_workflow_spec",
    "get_catalog_workflow_specs",
    "get_topology",
    "iter_catalog_workflow_specs",
    "list_topologies",
    "register_topology",
]
