"""Import regression tests for swarm startup."""

from __future__ import annotations

import importlib
import sys


def test_create_default_tool_registry_does_not_import_mailbox_eagerly():
    removed_modules = {}
    for module_name in list(sys.modules):
        if module_name == "openharness.tools" or module_name.startswith("openharness.tools."):
            removed_modules[module_name] = sys.modules.pop(module_name)
        if module_name == "openharness.swarm" or module_name.startswith("openharness.swarm."):
            removed_modules[module_name] = sys.modules.pop(module_name)

    try:
        tools = importlib.import_module("openharness.tools")
        registry = tools.create_default_tool_registry()

        assert registry.get("bash") is not None
        assert "openharness.swarm.mailbox" not in sys.modules
        assert "openharness.swarm.lockfile" not in sys.modules
    finally:
        for module_name in list(sys.modules):
            if module_name == "openharness.tools" or module_name.startswith("openharness.tools."):
                sys.modules.pop(module_name, None)
            if module_name == "openharness.swarm" or module_name.startswith("openharness.swarm."):
                sys.modules.pop(module_name, None)
        sys.modules.update(removed_modules)
        for module_name, module in removed_modules.items():
            if "." not in module_name:
                continue
            parent_name, child_name = module_name.rsplit(".", 1)
            parent = sys.modules.get(parent_name)
            if parent is not None:
                setattr(parent, child_name, module)
