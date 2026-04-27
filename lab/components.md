# Components

## Architecture

| ID | Status | Description | Used by | Evidence |
|----|--------|-------------|---------|----------|
| `single-loop` | validated | one model, one tool budget, no subagents and no scratchpad | `basic` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) |
| `planner-executor` | rejected | planner subagent emits a plan; executor carries it out in the same env | `planner_executor` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) showed narrow cluster wins, but [planner-schema-guard-paired](experiments.md#2026-04-24--planner-schema-guard-paired) matched control on score. |
| `react-loop` | rejected | ReAct: thought → action → observation in a tight scratchpad-driven loop | `react` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) had one narrow positive cluster and no standing branch in the simplified lab. |
| `reflection-loop` | rejected | worker + critic; critic reflects on each turn before the next | `reflection` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) (≥500k input tokens / trial). Re-add gated on `context-compaction`. |

## Runtime

| ID | Status | Description | Used by | Evidence |
|----|--------|-------------|---------|----------|
| `loop-guard` | rejected | detects no-progress turns (repeated tool calls, empty assistant) and steers toward recovery | — | [loop-guard-on-basic-near-miss](experiments.md#2026-04-24--loop-guard-on-basic-near-miss) scored 1/46 vs trunk 2/46; re-add only as part of a concrete recovery playbook, not as a standalone brake |
| `context-compaction` | proposed | truncates large tool-stdout blocks above a threshold | — | [idea](ideas.md#reflection-context-compaction) (no roadmap entry yet — re-queue under the original `reflection-context-compaction` idea on a meaningful slice) |

## Tools

| ID | Status | Description | Used by | Evidence |
|----|--------|-------------|---------|----------|
| `coding-tools` | validated | `bash + read_file + write_file + edit_file + glob + grep + think` — the standard executor toolbelt | `basic`, `planner_executor.executor`, `react.actor`, `reflection.worker` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) |
| `planner-tools-readonly` | experimental | `read_file + glob + grep` — read-only orient toolbelt for a planner subagent | `planner_executor.planner` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep); ablation folded into [planner-executor-cluster-confirmation](roadmap.md#planner-executor-cluster-confirmation) as Leg C (`planner_executor` with `tools: []`) |

## Prompt

_(none)_

## Model

| ID | Status | Description | Used by | Evidence |
|----|--------|-------------|---------|----------|
| `gemini-3.1-flash-lite-preview` | validated | default coding/reasoning model on the smoke + full sweeps | `basic`, `planner_executor`, `react`, `reflection` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) |
| `gemini-3-flash-preview` | experimental | Gemini 3 Flash preview measured as a stronger cheap `basic` model floor | `basic_flash` | [tb2-gemini3-model-baseline](experiments.md#2026-04-24--tb2-gemini3-model-baseline) |
| `gemini-3.1-pro-preview` | experimental | Gemini 3.1 Pro preview measured as the strongest but high-cost `basic` model branch candidate | `basic_pro` | [tb2-gemini3-model-baseline](experiments.md#2026-04-24--tb2-gemini3-model-baseline) |
| `gemini-2.5-pro` | deferred | superseded stronger-model candidate from the older near-miss baseline proposal | — | superseded by [tb2-gemini3-model-baseline](roadmap.md#tb2-gemini3-model-baseline) |
