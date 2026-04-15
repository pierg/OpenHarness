from __future__ import annotations

from openharness.agents.factory import AgentFactory


def test_builtin_tb2_agent_ids_are_loadable():
    factory = AgentFactory.with_default_configs()

    assert {"default", "planner_executor", "reflection", "react"}.issubset(
        set(factory.list_agents())
    )

    for agent_id in ("default", "planner_executor", "reflection", "react"):
        factory.create(agent_id)


def test_builtin_tb2_agent_configs_have_required_workspace_tools():
    factory = AgentFactory.with_default_configs()
    required = {"bash", "read_file", "write_file", "edit_file", "glob", "grep"}

    for agent_id in ("default", "planner_executor", "reflection", "react"):
        config = factory.get_config(agent_id)
        configs = [config, *config.subagents.values()]
        tool_configs = [item for item in configs if item.tools]
        assert tool_configs, f"{agent_id} has no tool-capable config"
        assert any(required.issubset(set(item.tools)) for item in tool_configs), agent_id
