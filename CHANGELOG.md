# Changelog

All notable changes to OpenHarness should be recorded in this file.

The format is based on Keep a Changelog, and this project currently tracks changes in a lightweight, repository-oriented way.

## [Unreleased]

### Tools

- Added a `think` tool (`src/openharness/tools/think_tool.py`): a no-op echo tool that gives the model a sanctioned scratchpad slot in its turn loop. Mirrors Anthropic's "think tool" research and the pattern used in modern SWE-bench / TB2 reference agents. Wired through `WORKSPACE_TOOLS` (and the `_TOOL_ALIAS_OVERRIDES` table) in `src/openharness/tools/__init__.py` so per-config YAML lists can request it; also registered in `create_default_tool_registry`. Enabled in the baseline agent configs that have execution tools: `basic.yaml`, `planner_executor.yaml` (executor subagent), `reflection.yaml` (worker subagent), and `react.yaml` (actor subagent). Each system prompt was extended with one explicit rule: "use `think` for planning; do NOT use `bash` with comment-only commands." Motivating trace: `runs/experiments/tb2-tracing-test-001/legs/basic/harbor/tb2-tracing-test-001-basic/regex-log__bNt7WXD/messages.jsonl` showed `gemini-3.1-flash-lite-preview` repurposing `bash` as a 40-line comment-only scratchpad on turn 2 because it had nowhere else to record planning — exactly the failure mode `think` is designed to absorb. Cost on frontier models with native interleaved thinking is one extra tool schema in the prompt; benefit on lite/preview/OSS models is reclaimed turn budget.

### Naming

- Renamed the baseline single-loop agent from `default` to `basic`. The yaml lives at `src/openharness/agents/configs/basic.yaml` (was `default.yaml`), `name: basic`, and `definition.subagent_type: yaml-basic`. Code defaults updated in `AgentConfig.name`, `runtime/workflow.py`, `harbor/agent.py`, `harbor/specs.py`, and `runs/specs.AgentSpec`. `experiments/tb2-baseline.yaml` now lists `- basic` instead of `- default`. The old name was confusing — `default` reads as "the one chosen if you don't pick" rather than describing the architecture, while `basic` accurately conveys "single thought-act loop, no orchestration / reflection". Existing run directories under `runs/` keep their historical `legs/default/...` paths; only new runs will use `legs/basic/`.

### Reliability

- Hardened the API retry path for long unattended sweeps: bumped `MAX_RETRIES` from 3 to 5 in `src/openharness/api/client.py` (which `gemini_client.py` imports) and lifted the Gemini exponential-backoff ceiling `_MAX_DELAY` from 30 s to 90 s in `src/openharness/api/gemini_client.py`. Together this gives ~3 minutes of total retry headroom per request, which matches the observed Google preview-tier TPM cooldown window so a single 429 brownout no longer fails an otherwise-healthy trial.
- `experiments/tb2-baseline.yaml`: switched the parallelism strategy from "many Harbor processes, one trial each" to "one Harbor process per leg, many trials inside". Set `defaults.n_concurrent: 4` (Harbor's native `--n-concurrent` — 4 docker containers + 4 agent loops in a single Harbor job, with native lifecycle, failure isolation, and unified progress reporting) and dropped `leg_concurrency` from 4 to 1 (legs run sequentially now). Peak in-flight trial count stays at 4 (well under the ~30 RPM gemini-3.1-flash-lite-preview standard-tier preview cap), but the runtime semantics are cleaner: one progress bar at a time, one Harbor job to attach to / cancel / inspect, and no more interleaved `Both GOOGLE_API_KEY...` log lines from racing Harbor processes.
- `experiments/tb2-baseline.yaml`: removed `reflection` from the baseline sweep agent list. The smoke run `tb2-baseline-smoke-20260416-205703` showed both reflection trials hit the 900 s harbor wall-clock at ~6.4 M input tokens / $0.67 each — the worker conversation grows quadratically because tool outputs accumulate in history. Excluded pending a context-compaction fix tracked in `lab/ideas.md#reflection-context-compaction` and `lab/experiments.md#reflection-context-blowup-on-smoke`. Re-add via `exec rerun <instance-id> -l reflection` once fixed.

### Agents

Small well-understood agent improvements live here in the changelog.
Longer-running work (ideas, experiments, validated components) is
tracked in [`lab/`](lab/) — three append-only markdown files for
ideas, experiments, and components.

- Reset all baseline agent configs (`default`, `planner_executor`, `reflection`, `react`) to known-good architecture pieces: stripped the unvalidated Phase-1 prompt protocols (onboarding / failure / verification / output-compaction) and the `web_fetch` / `web_search` tools, kept simple worker-style system prompts, and reverted budgets to `max_turns=30` / `max_tokens=8192`.
- Aligned `experiments/tb2-baseline.yaml` `defaults.model` from the now-deprecated `gemini-2.0-flash` to the current preview `gemini-3.1-flash-lite-preview`, matching the standalone fallback declared on every baseline agent yaml.
- `react.yaml` thinker prompt no longer hand-duplicates the JSON schema and example — the runtime auto-injects the actual `Thought` schema when `output_type=Thought`. The prompt now just describes each field semantically.
- `react.yaml` user template: removed the dangling `# Current State` heading whose only child was the `# Previous Steps` section, fixing the H1-inside-H1 hierarchy.
- `reflection.yaml` critic prompt now describes how to populate `approved` / `feedback` / `issues` so retry feedback is concrete (the schema itself is auto-injected by the runtime since the architecture passes `output_type=Verdict`).
- `reflection.yaml` worker prompt renders the critic's `issues` list as a real bullet list on retry instead of the raw Python `['x', 'y']` repr.
- Removed the `planner_executor_critic` agent (`src/openharness/agents/configs/planner_executor_critic.yaml`); it was a `reflection`-over-`planner_executor` composite that had not been measured. The composition is now an idea in [`lab/ideas.md`](lab/ideas.md) until a paired experiment justifies it.
- `LoopGuard` runtime mechanism (empty-turn / identical-tool-call detection in `src/openharness/engine/loop_guard.py`) now defaults to `enabled=False`. The code stays in tree so a future experiment can A/B it without affecting the baseline.
- `AgentConfig` keeps the `components: tuple[str, ...]` free-form metadata field, but the baseline configs ship with no entries. Activated components must be wired in by the experiment that introduces them.
- `experiments/tb2-baseline.yaml` defaults reverted to `max_turns=30` / `max_tokens=8192`; new `smoke` profile pins to two cached, lighter TB2 tasks (`regex-log`, `log-summary-date-ranges`) for fast end-to-end verification before a full run.
- `lab/ideas.md`, `lab/experiments.md`, `lab/components.md` scaffold the iteration workflow: ideas start in `ideas.md`, move through a git-worktree experiment logged in `experiments.md`, and if validated graduate into `components.md`. The `lab/components.md` "Active" section is currently empty by design — the previously-listed components are back in `lab/ideas.md` as proposals.

### Engine

- `Conversation.run_to_completion` now actually enforces the underlying `QueryContext.max_turns` budget. Previously the loop only stopped when the model emitted a tool-free turn (or the outer harbor wall-clock killed it), so a runaway planner could burn the full 900 s timeout despite a `max_turns: 6` config. The new behaviour returns the best-effort `final_text` from the last completed turn and emits a `StatusEvent`, letting downstream consumers (executor, verifier) keep working on partial output.
- `_parse_structured_output` (used for every `output_type=` agent call, e.g. ReAct's `Thought` and Reflection's `Verdict`) is now tolerant of the most common Gemini failure mode: invalid JSON escape sequences inside string values (e.g. `\d`, `\s`, `\Users` from regexes / Windows paths). On a strict-parse failure we sanitize stray backslashes and retry once before propagating the `ValidationError`.
- `AgentRuntime.total_usage` is now a public property exposing the live cumulative `UsageSnapshot` so callers can read partial usage when the agent run is cancelled or errors out before producing an `AgentRunResult`.

### Observability

- Harbor adapter (`src/openharness/harbor/agent.py`) now captures partial token usage from `AgentRuntime.total_usage` when the agent run raises (including `asyncio.CancelledError` from the outer `wait_for` timeout), so per-trial `input_tokens` / `output_tokens` / `total_tokens` / `cost_usd` are no longer null on errored trials. The catch is widened from `Exception` to `BaseException` for this purpose; the original error is always re-raised.
- Cost estimation gains a small `FALLBACK_PRICES_PER_MILLION` table consulted when `genai_prices` returns `None` (typically for brand-new preview models). Initial entry: `gemini-3.1-flash-lite-preview` at `$0.10 / $0.40` per million tokens, matching the closest non-preview sibling until Google publishes preview pricing.

### CLI

- New `rerun` command (`uv run rerun <instance-id-or-prefix-or-path>`) re-runs only the failed legs of a previous experiment in-place. Reads `<root>/experiment.json` + `<root>/config.resolved.yaml`, picks legs whose `status` is `failed` / `interrupted` / `pending` / `running` or whose trial-level `result_status` is `partial` / `all_failed` / `all_errored` / `no_trials` (legs with `all_passed` are skipped), wipes the matching leg directories, and resumes against the same `instance_id` so passing legs stay cached. Supports `--leg`/`-l` (repeatable) for explicit selection, `--status`/`-s` to override the default status filter, and `--dry-run` to preview the selection. Like `status` and `results`, the shorthand resolves a bare instance id (e.g. `tb2-baseline-smoke-20260416-151537`), an experiment-id prefix (`tb2-baseline` → latest run), or a path under `runs/experiments/`.

### Prompts & template-variable contract

- New `Settings.session_mode: Literal["interactive", "autonomous"]` (default `"interactive"`). Autonomous mode swaps in a slimmed base system prompt that explicitly tells the agent there is no human to interact with, drops the conversational/permission language from the interactive CLI base prompt, and removes the host-developer personalization sections (`CLAUDE.md`, `local_rules`, `memory`, `issue_context`, `pr_comments`) from `openharness_system_context`. The Harbor adapter (`src/openharness/harbor/agent.py`) now forces `session_mode="autonomous"` for every trial so that host-machine state stops leaking into trial prompts.
- `build_runtime_system_prompt` is now section-aware. New `available_tools` parameter lets it suppress the `delegation` section when the agent's `tools` list does not include `agent` and the `skills` section when it does not include `skill` — no more advertising tools the agent literally cannot call. New `include_sections` parameter and matching `AgentConfig.system_context_sections` field provide surgical per-agent control (e.g. a planner subagent can opt into just `("base",)`).
- `_prepare_query` template-variable contract has been tightened (see new `docs/template-variables.md`):
  - `task.payload` keys are no longer spread into the **system** template — only the user template gets the legacy `{{ key }}` shorthand. Per-task data can no longer accidentally leak into a static role description.
  - Unused `cwd` and `provider` kwargs are no longer passed (no baseline YAML referenced them; with `StrictUndefined` they were also a footgun).
  - `_output_schema_instruction` is now the real Jinja variable `{{ output_schema_instruction }}`, available in both system and user templates so YAMLs can place it deliberately. For back-compat the runtime still auto-appends the schema block to the system prompt when the template does not reference the variable.
- `planner_executor` now uses structured output for the planner: `runtime.run_agent_config(planner, task, output_type=Plan)` returns a `Plan` Pydantic model and the executor template renders `{{ plan.reasoning }}` / `{% for step in plan.steps %}` instead of dumping a free-form string. The `plan` payload is now consumed by the executor's user template (system templates no longer receive payload spread).
- Removed the dead `{% if payload %}…{% endfor %}{% endif %}` "Additional Context" block in `default.yaml` user template — TB2 tasks never set `payload`, so the block always rendered as a blank section.
- `ReActAgent.__init__` and `ReflectionAgent.__init__` now have explicit `NOTE` comments documenting that on a *parent* config `max_turns` budgets architecture iterations (think→act cycles, refine attempts) rather than LLM conversation turns. The leaf-agent semantic is unchanged.
- New `docs/template-variables.md` catalogs what is rendered where, the section-filtering rules, the `max_turns` semantic per architecture kind, and the procedure for adding a new template variable.
- New `ModelRequest` stream event captures the full request payload sent to the LLM provider for each turn (`model`, `system_prompt`, `tools`, `max_tokens`, `max_turns`, `turn_index`, `message_count`, and the originating `agent` for composite architectures). `Conversation.step` emits one `ModelRequest` immediately before each `run_single_turn` call, so it sits next to the matching `assistant_complete` / `tool_*` rows on the same `events.jsonl` stream. This complements the existing `messages.jsonl` (which by design holds only `ConversationMessage` rows — `Literal["user", "assistant"]`) and gives audit / replay tooling visibility into the system prompt and tool surface without violating the conversation data model. Aligned with the OpenTelemetry GenAI / Langfuse "generation" pattern: one request event per model invocation.

### Bug fixes

- **ReAct: terminal action no longer dropped** (`src/openharness/agents/architectures/react.py`). When the thinker emitted `Thought(is_finished=True, action="<final command>")` — the natural way for a model to encode "run this last write, then we're done" — the orchestrator returned immediately and silently discarded the action. Two of two `react` smoke trials failed for exactly this reason (`/app/regex.txt` and `/app/summary.csv` were never created despite the agent's `final_answer` claiming they were). The loop now executes any non-empty `action` first and only then honours `is_finished`, falling back to the action's observation when `final_answer` is empty. Empty-action / not-finished turns are recorded with a corrective observation so the thinker sees its own indecision instead of spinning silently. Six new unit tests in `tests/test_agents/test_react_architecture.py` lock the contract.
- **ReAct actor prompt hardened** (`src/openharness/agents/configs/react.yaml`). Previously the actor would sometimes reply conversationally ("the directory is empty, please provide more context") instead of running a tool, polluting the next thinker turn with what looked like a request for human input. The system prompt now explicitly forbids questions / clarification requests, mandates a tool call on every turn, and tells the actor to treat empty tool output as a valid observation rather than a reason to ask the caller. Thinker prompt also clarifies the new "is_finished + action runs the action then finishes" contract so the model can rely on it.

### Upstream Integration

- Integrated upstream `HKUDS/OpenHarness` `main` through `1325770` (`2026-04-27`), covering plugin security hardening, sandbox fail-closed behavior for unsupported Docker domain policies, OpenAI-compatible `<think>` stream filtering (including split tags), and safer shell subprocess stdin defaults.
- Integration approach: Mixed (targeted cherry-pick + manual conflict resolution). Adapted `src/openharness/plugins/loader.py`, `src/openharness/commands/registry.py`, and `tests/test_utils/test_shell.py` so upstream hardening works with this fork's existing plugin trust-gating and platform-specific PTY behavior.
- Deferred: plugin tool-import deferral commit `1325770` (upstream SHA) as-is because this fork does not yet carry the full plugin-tool loading path expected by that change; remaining upstream commits after `9caf700` are intentionally queued for a later broader sync to avoid pulling autopilot/dashboard and other large subsystems in the same patch.
- Integrated upstream `HKUDS/OpenHarness` `main` through `9caf700` (`2026-04-15`), covering secure default channel allowlists, profile materialization for base_url resolution, and openai_compat format support.
- Integration approach: Mixed (merge + manual port). Adapted `src/openharness/ui/runtime.py` and `src/openharness/api/factory.py` so upstream profile and format improvements work with the fork's centralized client factory.
- Integrated upstream `HKUDS/OpenHarness` `main` via full merge commit `d821ff5` (`2026-04-27`), bringing in the previously deferred larger surfaces (autopilot service/dashboard, broader CLI/runtime/swarm updates, plugin tooling improvements, and associated tests/docs/workflows) on top of the earlier targeted ports.
- Integration approach: Full merge + compatibility adaptation. Resolved conflicts in fork-touched runtime/tooling/auth/session boundaries to keep fork behavior stable while importing upstream feature and reliability work.
- Status update: the earlier "Deferred" items from the `2026-04-27` targeted port pass are now integrated by the full-merge follow-up in this same unreleased cycle.

### Added

- Docker as an alternative sandbox backend (`sandbox.backend = "docker"`) for stronger execution isolation with configurable resource limits, network isolation, and automatic image management.
- Built-in Google Gemini provider support from upstream, integrated with this fork's native Gemini and Vertex AI client path.
- Google Gemini and Vertex AI client support through the shared streaming API client protocol.
- React TUI assistant messages now render structured Markdown blocks, including headings, lists, code fences, blockquotes, links, and tables.
- `diagnose` skill: trace agent run failures and regressions using structured evidence from run artifacts.
- OpenAI-compatible API client (`--api-format openai`) supporting any provider that implements the OpenAI `/v1/chat/completions` format.
- `OPENHARNESS_API_FORMAT` environment variable for selecting the API format.
- `OPENAI_API_KEY` fallback when using OpenAI-format providers.
- GitHub Actions CI workflow for Python linting, tests, and frontend TypeScript checks.
- `CONTRIBUTING.md` reframed as fork development notes with setup, checks, example policy, and docs policy.
- `docs/examples.md` with concrete OpenHarness usage patterns and demo commands.
- GitHub issue templates and a pull request template.
- Built-in `codex` output style for compact, low-noise transcript rendering in React TUI.
- Autopilot service stack (`src/openharness/autopilot/`) and dashboard assets (`autopilot-dashboard/`, `docs/autopilot/`) from upstream, including CI workflows for autopilot scan/run/pages publication.
- Plugin `tools/` discovery and registration support so plugin-contributed `BaseTool` subclasses can be surfaced in runtime tool registries.
- New hook events for richer lifecycle automation: `user_prompt_submit`, `notification`, `stop`, and `subagent_stop`.
- First-class MiniMax provider wiring in auth/settings/CLI/runtime paths.
- CLI dry-run safe preview mode for safer command planning/execution previews.

### Fixed

- Gemini API client now captures `thought_signature` from the correct `google.genai.types.Part` field (not `FunctionCall`), handles thought-only parts, and echoes the signature back on outgoing parts. `TextBlock` / `ToolUseBlock` store the signature as `bytes` with base64 JSON serialization, fixing `400 Function call is missing a thought_signature` mid-conversation and `UnicodeDecodeError` on JSON round-trips.
- OpenHarness-authored run artifacts (`run.json`, `experiment.json`, `leg.json`, `result.portable.json`) now store paths relative to the experiment root, so runs produced on one machine can be analyzed on another without path rewriting.
- `todo_write` tool now updates an existing unchecked item in-place when `checked=True` instead of appending a duplicate `[x]` line.
- React TUI spinner now stays visible throughout the entire agent turn.
- Skill loader now uses `yaml.safe_load` to parse SKILL.md frontmatter.
- Fixed grep crashes on very long ripgrep lines.
- Fixed React TUI Markdown table sizing with inline formatting.
- Fixed React TUI exit leaving the shell prompt concatenated with the last TUI line.
- `BackendHostConfig` was missing the `cwd` field after the runtime refactor that added `cwd` support to `build_runtime`.
- Shell-escape `$ARGUMENTS` substitution in command hooks to prevent shell injection.
- Swarm `_READ_ONLY_TOOLS` now uses actual registered tool names.
- Memory scanner now parses YAML frontmatter.
- Memory search matches against body content in addition to metadata.
- Memory search tokenizer handles Han characters for multilingual queries.
- Fixed duplicate response in React TUI caused by double Enter key submission in the input handler.
- Fixed concurrent permission modals overwriting each other in TUI default mode when the LLM returns multiple tool calls in one response; `_ask_permission` now serialises callers via an `asyncio.Lock` so each modal is shown and resolved before the next one is emitted.
- Fixed grep tool crashing with `ValueError` / `LimitOverrunError` when ripgrep outputs a line longer than 64 KB (e.g. minified assets or lock files). The asyncio subprocess stream limit is now 8 MB and oversized lines are skipped rather than terminating the session.
- Reduced React TUI redraw pressure when `output_style=codex` by avoiding token-level assistant buffer flushes during streaming.
- OpenAI-compatible streaming now strips `<think>...</think>` blocks robustly, including split-tag streaming edge cases.
- Session restore now sanitizes dangling tool-call state; compacting now preserves tool-use/tool-result pairing across boundaries.
- Shell execution defaults are safer and more predictable (`stdin=DEVNULL` baseline, stronger Windows/TUI/backspace handling), and Docker domain-policy handling fails closed for unsupported policies.

### Changed

- React TUI now groups consecutive `tool` and `tool_result` transcript rows into a single compound row.
- README is now a concise fork overview with setup, example commands, artifact layout, and links into `docs/`.
- Documentation is reorganized around maintained feature, architecture, run, and example guides.
- Example documentation now lists only examples that demonstrate distinct end-to-end behavior.
- Plugin loading/runtime surfaces now include upstream security and trust-boundary hardening while preserving fork-specific compatibility behavior.
- Swarm/coordinator/task lifecycle behavior now incorporates upstream task-cleanup and async-wait reliability improvements from the full merge.

## [0.1.0] - 2026-04-01

### Added

- Initial public release of OpenHarness.
- Core agent loop, tool registry, permission system, hooks, skills, plugins, MCP support, and terminal UI.
