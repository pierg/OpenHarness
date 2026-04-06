"""Tests for workflow specs and catalog loading."""

from __future__ import annotations

import pytest

from openharness.workflows.catalog import load_workflow_configs_dir
from openharness.workflows.specs import WorkflowSpec


def test_workflow_spec_requires_roles() -> None:
    with pytest.raises(ValueError, match="at least one role"):
        WorkflowSpec(name="broken", roles={})


def test_workflow_spec_validates_entry_role() -> None:
    with pytest.raises(ValueError, match="entry_role"):
        WorkflowSpec(
            name="broken",
            entry_role="missing",
            roles={"worker": {"agent": "default"}},
        )


def test_workflow_spec_validates_routing_targets() -> None:
    with pytest.raises(ValueError, match="unknown role 'missing'"):
        WorkflowSpec(
            name="broken",
            roles={"leader": {"agent": "default"}},
            routing={"leader": {"may_message": ["missing"]}},
        )


def test_workflow_spec_from_yaml_uses_filename_when_name_is_missing(tmp_path) -> None:
    path = tmp_path / "demo.yaml"
    path.write_text(
        """
kind: workflow
topology: single
roles:
  main:
    agent: default
""".strip(),
        encoding="utf-8",
    )
    spec = WorkflowSpec.from_yaml(path)
    assert spec.name == "demo"
    assert spec.topology == "single"


def test_load_workflow_configs_dir_reads_multiple_specs(tmp_path) -> None:
    (tmp_path / "one.yaml").write_text(
        """
kind: workflow
name: one
roles:
  main:
    agent: default
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "two.yml").write_text(
        """
kind: workflow
name: two
roles:
  main:
    agent: default
""".strip(),
        encoding="utf-8",
    )

    loaded = load_workflow_configs_dir(tmp_path, source="project")
    assert [item.spec.name for item in loaded] == ["one", "two"]

