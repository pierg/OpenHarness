# Components

## Architecture

| ID | Status | Description | Used by | Evidence |
|----|--------|-------------|---------|----------|
| `single-loop` | validated | one model, one tool budget, no subagents and no scratchpad | `basic` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) |
| `planner-executor` | branch | planner subagent emits a plan; executor carries it out in the same env | `planner_executor` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) (wins on security_certificates, system_administration, python_data) |
| `react-loop` | branch | ReAct: thought → action → observation in a tight scratchpad-driven loop | `react` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) (one positive cluster; needs targeted re-test) |
| `reflection-loop` | rejected | worker + critic; critic reflects on each turn before the next | `reflection` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) (≥500k input tokens / trial). Re-add gated on `context-compaction`. |

## Runtime

| ID | Status | Description | Used by | Evidence |
|----|--------|-------------|---------|----------|
| `loop-guard` | proposed | detects no-progress turns (repeated tool calls, empty assistant) and steers toward recovery | — | [idea](ideas.md#loop-guard), queued: [loop-guard-paired-ablation](roadmap.md#loop-guard-paired-ablation) |
| `context-compaction` | proposed | truncates large tool-stdout blocks above a threshold | — | [idea](ideas.md#reflection-context-compaction) (no roadmap entry yet — re-queue under the original `reflection-context-compaction` idea on a meaningful slice) |

## Tools

| ID | Status | Description | Used by | Evidence |
|----|--------|-------------|---------|----------|
| `coding-tools` | validated | `bash + read_file + write_file + edit_file + glob + grep + think` — the standard executor toolbelt | `basic`, `planner_executor.executor`, `react.acter`, `reflection.worker` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) |
| `planner-tools-readonly` | experimental | `read_file + glob + grep` — read-only orient toolbelt for a planner subagent | `planner_executor.planner` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep); ablation folded into [planner-executor-cluster-confirmation](roadmap.md#planner-executor-cluster-confirmation) as Leg C (`planner_executor` with `tools: []`) |

## Prompt

_(none)_

## Model

| ID | Status | Description | Used by | Evidence |
|----|--------|-------------|---------|----------|
| `gemini-3.1-flash-lite-preview` | validated | default coding/reasoning model on the smoke + full sweeps | `basic`, `planner_executor`, `react`, `reflection` | [tb2-baseline-full-sweep](experiments.md#2026-04-17--tb2-baseline-full-sweep) |
| `gemini-2.5-pro` | proposed | stronger SKU, ~10× cost; used to disambiguate "agent too weak" vs "model too weak" | — | queued: [stronger-model-baseline](roadmap.md#stronger-model-baseline) |
