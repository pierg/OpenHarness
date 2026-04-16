# Changelog

All notable changes to OpenHarness should be recorded in this file.

The format is based on Keep a Changelog, and this project currently tracks changes in a lightweight, repository-oriented way.

## [Unreleased]

### Agents

Small well-understood agent improvements live here in the changelog.
Longer-running work (ideas, experiments, validated components) is
tracked in [`lab/`](lab/) — three append-only markdown files for
ideas, experiments, and components.

- `AgentConfig` gained a `components: tuple[str, ...]` field (plain metadata, no validation). It is persisted into each run's `agent.resolved.yaml` so trial artifacts list the active components and outcomes can be grouped by component set after the fact.
- Planner system prompt (`planner_executor`, `planner_executor_critic`) tightened with a strict output schema (Observations / Assumptions / Steps / Success Criteria) and explicit bans on pseudo-tool-call code fences, eliminating the `tool_code` hallucinations we saw on Gemini.
- Default, planner, executor, and critic system prompts gained an onboarding protocol (pwd/ls/README before editing), a failure protocol (hypothesis-before-retry, no duplicate commands), a verification protocol (run a concrete check before declaring done), and output-compaction guidance (head/tail/wc for large outputs).
- `lab/ideas.md`, `lab/experiments.md`, `lab/components.md` scaffold the iteration workflow: ideas start in `ideas.md`, move through a git-worktree experiment logged in `experiments.md`, and if validated graduate into `components.md`.

### Upstream Integration

- Integrated upstream `HKUDS/OpenHarness` `main` through `9caf700` (`2026-04-15`), covering secure default channel allowlists, profile materialization for base_url resolution, and openai_compat format support.
- Integration approach: Mixed (merge + manual port). Adapted `src/openharness/ui/runtime.py` and `src/openharness/api/factory.py` so upstream profile and format improvements work with the fork's centralized client factory.

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

### Changed

- React TUI now groups consecutive `tool` and `tool_result` transcript rows into a single compound row.
- README is now a concise fork overview with setup, example commands, artifact layout, and links into `docs/`.
- Documentation is reorganized around maintained feature, architecture, run, and example guides.
- Example documentation now lists only examples that demonstrate distinct end-to-end behavior.

## [0.1.0] - 2026-04-01

### Added

- Initial public release of OpenHarness.
- Core agent loop, tool registry, permission system, hooks, skills, plugins, MCP support, and terminal UI.
