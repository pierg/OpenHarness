from __future__ import annotations

from openharness.agents.factory import AgentFactory


_BUILTIN_AGENT_IDS = (
    "basic",
    "planner_executor",
    "reflection",
    "react",
)


def test_builtin_tb2_agent_ids_are_loadable():
    factory = AgentFactory.with_default_configs()

    assert set(_BUILTIN_AGENT_IDS).issubset(set(factory.list_agents()))

    for agent_id in _BUILTIN_AGENT_IDS:
        factory.create(agent_id)


def _iter_tool_configs(config):
    yield config
    for sub in config.subagents.values():
        yield from _iter_tool_configs(sub)


def test_builtin_tb2_agent_configs_have_required_workspace_tools():
    factory = AgentFactory.with_default_configs()
    required = {"bash", "read_file", "write_file", "edit_file", "glob", "grep"}

    for agent_id in _BUILTIN_AGENT_IDS:
        config = factory.get_config(agent_id)
        configs = list(_iter_tool_configs(config))
        tool_configs = [item for item in configs if item.tools]
        assert tool_configs, f"{agent_id} has no tool-capable config"
        assert any(required.issubset(set(item.tools)) for item in tool_configs), agent_id
