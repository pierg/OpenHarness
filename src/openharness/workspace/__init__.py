"""Workspace abstraction: protocol and implementations.

The ``Workspace`` protocol defines a substrate-agnostic interface for file I/O
and shell execution.  Implementations live alongside this package:

- ``LocalWorkspace`` -- pathlib + asyncio subprocess (this machine)
- ``HarborWorkspace`` -- Harbor ``BaseEnvironment`` adapter (import from
  ``openharness.workspace.harbor`` or ``openharness.harbor``)
"""

from openharness.workspace.contracts import CommandResult, Workspace
from openharness.workspace.local import LocalWorkspace

__all__ = [
    "CommandResult",
    "LocalWorkspace",
    "Workspace",
]
