# Ideas

## Proposed

### Architecture

#### planner-executor-critic

-   **Motivation:** The reflection critic catches premature completions for the simple `reflection` worker; it might do the same for a richer `planner_executor` worker.
-   **Sketch:** Compose the existing `reflection` architecture with a `planner_executor` worker (pure YAML composition, no new code) and a strict critic prompt that fails reports lacking concrete verification.

#### planner-rerank

-   **Motivation:** First plan the planner produces is often mediocre.
-   **Sketch:** Generate N plans, rerank with a small judge model (same family, smaller size), execute top-1.

### Runtime

#### loop-guard

-   **Motivation:** Some Gemini variants emit empty assistant turns or repeat the same tool call indefinitely; without intervention the agent silently exhausts its budget on no progress.
-   **Sketch:** Runtime mechanism (already in `src/openharness/engine/loop_guard.py`, off by default) that detects empty turns and identical tool-call streaks, injects a short steering nudge, and gives up after a small budget.

#### tool-result-summariser

-   **Motivation:** Large tool results eat context and often drown useful signal.
-   **Sketch:** After any tool result above K tokens, inject a short summary before the next turn; keep the full result in the trace but hide it from the model.

#### reflection-context-compaction

-   **Motivation:** Reflection's worker conversation grows quadratically in tokens because every turn re-sends the full history including raw `bash`/`grep` stdout. A pre-reset smoke run saw both reflection trials hit the 900 s harbor wall-clock at 6.4 M input tokens / $0.67 each — long before the 30-turn worker budget could fire. Until this is fixed, `reflection` is excluded from `experiments/tb2-baseline.yaml`.
-   **Sketch:** Truncate or summarise tool outputs above some threshold (e.g. keep first/last 50 lines, replace middle with a `<truncated N lines>` marker) before they re-enter the next turn's history. Opt-in via an `AgentConfig` flag so the basic loop stays unaffected until measured.

#### executor-bash-timeout-aware-retry

-   **Motivation:** Long-running commands (builds, downloads) hit the bash tool timeout and we have no recovery path.
-   **Sketch:** Detect timeouts, relaunch the command in background, poll status. Or expose a `run_in_background=True` flag on the bash tool.

#### extended-budget

-   **Motivation:** The 30/8192 baseline sometimes hits the agent-phase timeout on heavier `build-*` and `git-*` tasks. Raising to 60/16384 might convert near-misses into passes.
-   **Sketch:** Bump `defaults.max_turns` to 60 and `max_tokens` to 16384 in `experiments/tb2-baseline.yaml` (and matching agent configs).

### Tools

#### grounded-planner-tools

-   **Motivation:** The `planner_executor` planner is currently wired with read-only tools (`read_file`, `glob`, `grep`) but no experiment has measured whether removing them hurts — or whether the planner still hallucinates `tool_code` blocks anyway.
-   **Sketch:** Paired ablation — planner with read-only tools (current default) vs planner with no tools. Same prompt, same model.

#### web-tools

-   **Motivation:** Some tasks need external documentation, source tarballs, or upstream references that aren't in the sandbox image.
-   **Sketch:** Add `web_fetch` + `web_search` tools to the `basic` agent and to the `planner_executor` planner/executor subagents.

### Memory

#### skill-memory

-   **Motivation:** Agents re-derive the same command sequences every run.
-   **Sketch:** Persist successful command sequences to a task-local `skills/` directory that the planner reads on its first turn.

#### episodic-memory

-   **Motivation:** Cross-task patterns (e.g. "how to read a Dockerfile before editing") aren't reused.
-   **Sketch:** Indexed store of post-run reflections keyed by task signature; planner pulls top-k before producing a plan.

## Trying

_(none)_

## Graduated

_(none)_

## Rejected

_(none)_

## Auto-proposed

#### loop-guard-on-planner-executor

-   **Motivation:** Once loop-guard lands as a runtime atom on trunk, the same mechanism is more likely to help on planner_executor (which adds a planning hop that can also stall) than on basic alone. Composition test, not yet runnable.
-   **Sketch:** Paired ablation on planner_executor only: leg A current YAML; leg B same YAML with loop-guard runtime atom enabled. Run on the three positive clusters from the planner_executor branch (security_certificates, system_administration, python_data) plus a same-size negative-cluster control.
-   **Auto-proposed by:** lab-reflect-and-plan@2026-04-18

#### tool-result-summariser-paired

-   **Motivation:** Sibling of context-compaction but cheaper to test in isolation: rather than truncating raw tool stdout, inject an LLM-generated short summary of any tool result above K tokens before the next turn. Could rescue reflection (currently rejected) without the brittle line-count heuristic of context-compaction.
-   **Sketch:** Implement behind an AgentConfig flag (off by default). Paired ablation on basic (cheapest harness) with the flag on/off on a slice biased toward tasks where tool stdout exceeded 50 lines in tb2-baseline-full-sweep. Re-test on reflection only if the basic ablation is positive.
-   **Auto-proposed by:** lab-reflect-and-plan@2026-04-18
