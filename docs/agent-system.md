# Agent System

This document describes the composable agent framework: contracts, configuration, architectures, runtime, and the end-to-end workflow.

---

## Contracts — `agents.contracts`

Everything builds on three types:

```
TaskDefinition          what to do
  instruction: str      primary natural-language task
  payload: dict         extra template variables forwarded to prompts

Agent (Protocol)        who does it
  run(task, runtime) -> AgentRunResult

AgentRunResult[T]       what came back
  output: T             str for free-text, BaseModel for structured
  input_tokens: int
  output_tokens: int
  final_text -> str     backward-compat property
```

`Agent` is a `Protocol` — any class with an `async def run(task, runtime)` method satisfies it, so architectures compose freely.

---

## Configuration — `agents.config`

`AgentConfig` is a Pydantic model loadable from YAML:

```yaml
name: my_agent
architecture: simple          # simple | planner_executor | reflection | react
model: gemini-2.5-flash-lite
max_turns: 15
max_tokens: 8192
tools: [bash, read_file, write_file, edit_file, glob, grep]

prompts:
  system: "{{ openharness_system_context }}  ..."
  user:   "{{ instruction }}"

subagents:                    # recursive — each value is another AgentConfig
  planner: { ... }
  executor: { ... }
```

Key method: `render_prompt(name, **vars)` — renders a Jinja template from the `prompts` dict.

---

## Factory — `agents.factory`

`AgentFactory` loads YAML configs, resolves architecture classes, and recursively builds agent trees.

```
factory = AgentFactory.with_default_configs()   # loads agents/configs/*.yaml
agent   = factory.create("default")             # returns Agent

factory.register(config)                        # programmatic registration
factory.list_agents()                           # -> ["default", "react_example", ...]
factory.get_config("default")                   # -> AgentConfig
AgentFactory.register_architecture("custom", MyAgent)
```

Architectures are looked up from an internal registry:

| Key                | Class                |
|--------------------|----------------------|
| `simple`           | `SimpleAgent`        |
| `planner_executor` | `PlannerExecutorAgent` |
| `reflection`       | `ReflectionAgent`    |
| `react`            | `ReActAgent`         |

Subagent configs are built recursively via `_build_agent(config)`.

---

## Architectures — `agents.architectures`

Each architecture is a class satisfying `Agent`. They differ only in orchestration; the LLM interaction is always delegated to `AgentRuntime`.

### SimpleAgent

Leaf node. Delegates entirely to `runtime.run_agent_config(config, task)`.

```
SimpleAgent(config)
  run(task, runtime) -> AgentRunResult[str]
```

### PlannerExecutorAgent

Composes two `Agent` instances. The planner produces output, the executor receives it as `task.payload["plan"]`.

```
PlannerExecutorAgent(config, planner=Agent, executor=Agent)
  run(task, runtime):
    plan   = planner.run(task, runtime)
    result = executor.run(task + plan, runtime)
```

### ReflectionAgent

Worker + critic loop. The worker is any `Agent`; the critic is an `AgentConfig` that returns structured `Verdict { approved, feedback, issues }`. Iterates up to `config.max_turns` attempts.

```
ReflectionAgent(config, worker=Agent)
  run(task, runtime):
    loop:
      result  = worker.run(task, runtime)
      verdict = runtime.run_agent_config(critic_config, ..., output_type=Verdict)
      if verdict.approved: return result
      task += feedback
```

### ReActAgent

Think / Act / Observe loop. The thinker produces structured `Thought { reasoning, action, is_finished, final_answer }`; the actor executes the action with tools. Runs up to `config.max_turns` steps.

```
ReActAgent(config)
  run(task, runtime):
    loop:
      thought = runtime.run_agent_config(thinker, ..., output_type=Thought)
      if thought.is_finished: return final_answer
      observation = runtime.run_agent_config(actor, thought.action)
      observations.append(...)
```

---

## Conversation — `engine.conversation`

Low-level handle over a single agent's multi-turn LLM loop. Created by `AgentRuntime.create_conversation()`.

```
conv = runtime.create_conversation(config, task)

# Step-by-step control
result: TurnResult = await conv.step()
conv.inject(ConversationMessage.from_user_text("try again"))
result = await conv.step()

# Or run to completion
text = await conv.run_to_completion(on_turn_complete=callback)
```

Key properties and methods:

| Member              | Description                                          |
|---------------------|------------------------------------------------------|
| `step()`            | Execute one LLM turn, return `TurnResult`            |
| `run_to_completion()`| Loop `step()` until done                            |
| `inject(message)`   | Insert a message and re-open the conversation        |
| `messages`           | Current message list                                |
| `is_complete`        | Whether the conversation has finished                |
| `final_text`         | Last assistant text output                          |

---

## Runtime — `runtime.session`

`AgentRuntime` is the execution substrate that every agent receives. It wires together settings, API clients, tool registries, permissions, tracing, usage tracking, and JSONL logging.

```
runtime = AgentRuntime(
    workspace,
    settings=...,              # Settings (loaded from config/env)
    permission_mode=...,       # override PermissionMode (FULL_AUTO for headless)
    api_client=...,            # explicit client, or auto-created per model
    log_paths=...,             # JSONL event/message paths
    trace_observer=...,        # telemetry
    tool_registry_factory=..., # custom tool builder
)
```

### High-level helpers (what architectures call)

| Method                                  | Returns         | Description                                      |
|-----------------------------------------|-----------------|--------------------------------------------------|
| `run_agent_config(config, task)`        | `str`           | Run a config end-to-end, return final text       |
| `run_agent_config(..., output_type=T)`  | `T`             | Same, but parse structured output into Pydantic  |
| `create_conversation(config, task)`     | `Conversation`  | For step-level control                           |
| `build_result(output)`                  | `AgentRunResult` | Package output + accumulated token usage        |

### How structured output works

When `output_type` is provided:
1. The JSON schema is injected into the system prompt
2. The conversation runs to completion
3. The response text is parsed and validated against the Pydantic model

---

## Workflow — `runtime.workflow`

Top-level orchestrator for standalone task runs. Wires factory -> agent -> runtime.

```python
from openharness.runtime import Workflow

wf = Workflow(workspace)
result: WorkflowResult = await wf.run(
    task=TaskDefinition(instruction="Fix the bug"),
    agent_name="default",
)
print(result.agent_result.final_text)
```

`WorkflowResult` wraps `AgentRunResult` and an optional `evaluation` dict.

---

## End-to-end flow

```
YAML configs
    |
    v
AgentFactory.with_default_configs()
    |
    v
factory.create("react_example")     # builds Agent tree recursively
    |
    v
agent.run(task, runtime)
    |
    ├── runtime.run_agent_config()   # renders prompts, builds tools, calls LLM
    |       |
    |       v
    |   Conversation.run_to_completion()
    |       |
    |       v
    |   engine.query.run_single_turn()   # API call + tool execution + permissions
    |
    └── runtime.build_result(output)     # package output + usage
            |
            v
        AgentRunResult[T]
```

---

## Composition

Any `Agent` can be used as a building block inside another. The factory builds trees recursively from YAML `subagents`:

```yaml
# A planner-executor where the executor is itself a reflection agent
name: nested_example
architecture: planner_executor
subagents:
  planner:
    architecture: simple
    tools: []
  executor:
    architecture: reflection
    subagents:
      worker:
        architecture: simple
        tools: [bash, write_file]
      critic:
        architecture: simple
        tools: []
```

Because the `Agent` protocol is a single method, any architecture can nest any other without coupling.
