# Template variables

OpenHarness agent configs are YAML files whose `prompts.system` and
`prompts.user` fields are Jinja2 templates rendered at runtime. This
document is the source of truth for which variables are available where,
and how the runtime decides what to inject.

## TL;DR

| Variable | System | User | Source |
| --- | --- | --- | --- |
| `openharness_system_context` | yes | no | `build_runtime_system_prompt` |
| `output_schema_instruction` | yes | yes | runtime, when `output_type=` is requested |
| `instruction` | no | yes | `task.instruction` |
| `payload` | no | yes | `task.payload` (whole dict) |
| individual `payload.*` keys | no | yes (auto-spread, legacy) | `task.payload` |
| architecture-specific extras | yes | yes | `extra_template_vars` |

Anything not in this table is **not** passed and will raise a
`StrictUndefined` error at render time. Use this discipline to your
advantage: a typo will fail loudly the first time the template renders.

## System template variables

System templates render the agent's role and persistent instructions.
The runtime deliberately keeps this surface narrow so that nothing from
the per-task `payload` can leak into a static instruction.

### `openharness_system_context`

Assembled by `openharness.prompts.context.build_runtime_system_prompt`.
It concatenates a configurable set of sections:

- `base` — always present. Selects between the interactive CLI prompt
  (`session_mode="interactive"`, default) and the autonomous prompt
  (`session_mode="autonomous"`, used by Harbor trials).
- `session_mode` — fast-mode hint when `settings.fast_mode` is true.
- `reasoning` — effort/passes settings.
- `skills` — list of available skills. Auto-skipped when the agent does
  not have the `skill` tool registered.
- `delegation` — instructions for spawning subagents via the `agent`
  tool. Auto-skipped when the agent does not have the `agent` tool.
- `project_instructions` — `CLAUDE.md` / `AGENTS.md` if present.
- `local_rules` — host-machine personalization rules.
- `issue_context` / `pr_comments` — when their files exist on disk.
- `memory` — memory entrypoint and relevant memories.

Two filters control which sections actually appear:

1. **Session mode** (`Settings.session_mode`). In `"autonomous"` mode,
   the default section set is `{"base", "session_mode", "reasoning",
   "skills", "delegation"}`. The host-developer sections
   (`project_instructions`, `local_rules`, `memory`, `issue_context`,
   `pr_comments`) are dropped because they reference files on the host
   machine that have no meaning inside an isolated trial container.
2. **Per-agent override** (`AgentConfig.system_context_sections`).
   Surgical control. Set to `("base",)` for a planner subagent that
   wants nothing but the slim base prompt. `"base"` is always included.

In addition, the `skills` and `delegation` sections are dropped when the
agent's `tools` list does not include the `skill` or `agent` tool
respectively — they would otherwise advertise tools the agent cannot
call.

### `output_schema_instruction`

Empty string by default. When `runtime.run_agent_config(config, task,
output_type=SomeModel)` is invoked, the runtime renders
`SomeModel.model_json_schema()` into a JSON-only instruction block and
exposes it as `{{ output_schema_instruction }}` in both system and user
templates.

For backward compatibility with templates that don't reference the
variable explicitly, the runtime auto-appends the rendered block to the
system prompt when (a) a schema is requested and (b) the system template
text does not contain the substring `output_schema_instruction`.

New templates can interpolate it explicitly to control placement, e.g.
near the role description.

### Architecture-provided extras

A composite architecture (planner/executor, react, reflection) may pass
`extra_template_vars` when calling `runtime.run_agent_config`. Those
variables are visible to both the system and user templates of the
inner config. Today only the user template should use them; treating
the system template as static is a deliberate convention.

## User template variables

The user template renders the per-turn instruction. It receives every
system variable plus:

- `instruction` — `task.instruction` as a string.
- `payload` — `task.payload` as a dict. Prefer this for safe iteration:

  ```jinja
  {% for key, value in payload.items() %}- {{ key }}: {{ value }}
  {% endfor %}
  ```

- All keys of `task.payload` spread at the top level (e.g. `{{ plan }}`
  if the task payload has a `plan` key). This is a legacy shorthand;
  new templates should prefer `payload.foo` to avoid name collisions
  with runtime variables.

### Architecture-provided payload keys

Composite architectures inject specific keys into their inner agents'
`task.payload` (and therefore into the inner user template):

- `planner_executor.executor` ← `plan: Plan` — the structured plan
  emitted by the planner. Use `{{ plan.reasoning }}` and
  `{% for step in plan.steps %}` to render its fields.
- `react.thinker` ← `observations: list[dict]`, `step: int` — running
  history of think/act/observe steps.
- `reflection.worker` ← (on retries) `previous_attempt: str`,
  `feedback: str`, `issues: list[str]`.
- `reflection.critic` ← `solution: str`, `attempt: int`.

## `max_turns` semantics by architecture

`max_turns` always means "iteration budget" but the *thing* being
iterated differs depending on whether the config is a leaf agent or a
composite parent:

| Config kind | What `max_turns` budgets |
| --- | --- |
| Leaf (`architecture: simple`) | LLM conversation turns |
| `react` parent | think→act cycles |
| `react.thinker` / `react.actor` (leaf) | LLM conversation turns |
| `reflection` parent | worker→critic refinement attempts |
| `reflection.worker` (leaf) | LLM conversation turns |
| `planner_executor.executor` (leaf) | LLM conversation turns |
| `planner_executor.planner` (leaf) | LLM conversation turns |

When the budget is exhausted, the conversation loop logs a
`StatusEvent` and returns the best-effort `final_text` rather than
raising. Architectures that exhaust their parent budget simply return
the last attempt's result.

## Adding a new variable

1. Decide whether it belongs in the system surface (constant for the
   agent's role) or the user surface (per-turn task data).
2. Plumb it via `extra_template_vars` in the architecture, or via
   `task.payload` if it's per-task.
3. Update this document. The Jinja env uses `StrictUndefined`, so
   forgetting any of the above will fail loudly on first render.
