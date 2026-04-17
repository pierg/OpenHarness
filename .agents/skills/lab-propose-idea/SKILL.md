---
name: lab-propose-idea
description: >
  Append a new entry to lab/ideas.md under "## Proposed". Use when the
  user proposes a new agent improvement ("what if we…", "I have an
  idea", "let's try doing X"), wants to capture an idea for later, or
  asks to record a proposal in the lab without running anything yet.
  Companion skills: lab, lab-run-experiment, lab-graduate-component.
---

# Lab — Propose Idea

Capture a new agent-improvement idea in `lab/ideas.md` so it can be
picked up later. This skill **does not** run any experiment, change
any agent YAML, or commit anything. It only edits `lab/ideas.md`.

## When to Use

- User proposes a new agent improvement (memory layer, new tool,
  prompt change, runtime mechanism, architecture variant, …).
- User asks to "park", "log", or "record" an idea for later.
- User has just finished a discussion and wants to write down what to
  try next.

Do **not** use this skill if the user says "let's try it" or "run an
experiment for it" — that's `lab-run-experiment`. Capturing the idea
is the *first* step, but `lab-run-experiment` will do that itself
when it scaffolds the experiment.

## Instructions

### 1. Confirm the idea has a stable id

If the user hasn't named it, propose a kebab-case id and confirm
before writing. Examples: `executor-bash-timeout-aware-retry`,
`planner-rerank`, `tool-result-summariser`, `skill-memory`.

Before adding, **check for collisions**:

```bash
rg -n "^### " lab/ideas.md lab/components.md
```

If the id is already used (in any of the three lab files), pick a
different one. Ids are permanent.

### 2. Draft the entry

Use this exact shape (mirrors the template at the top of
`lab/ideas.md`):

```markdown
### <kebab-id>

**Status:** proposed
**Motivation:** one-sentence why.
**Sketch:** one-paragraph how.
**Expected experiment:** what we'd measure, on which slice.
```

Keep it concise — no design doc here. Long-form rationale belongs in
the experiment entry once we actually try it.

### 3. Insert under "## Proposed"

Read `lab/ideas.md`, find the `## Proposed` heading, and insert the
new entry at the **bottom** of that section (preserve order — first
in, first out). Do not touch any other section.

### 4. Confirm and report

After saving, report:

- The id used.
- The motivation in one line.
- The path: `lab/ideas.md`.

Do **not**:

- Edit `lab/experiments.md` or `lab/components.md`.
- Touch any agent YAML.
- Run any experiment.
- Create a git worktree, commit, or push.

## Examples

### Example: User has a vague idea

Input: "Could we make the planner generate multiple plans and pick
the best one?"

Output:

1. Propose id `planner-rerank`, confirm with the user.
2. Append to `lab/ideas.md` under `## Proposed`:

   ```markdown
   ### planner-rerank

   **Status:** proposed
   **Motivation:** First plan the planner produces is often mediocre.
   **Sketch:** Generate N plans, rerank with a small judge model
   (same family, smaller size), execute the top-1.
   **Expected experiment:** compare 1-plan vs 3-plan-rerank on
   `planner_executor`; track pass rate and plan-quality notes.
   ```

3. Reply: "Recorded `planner-rerank` in `lab/ideas.md`. When you
   want to actually try it, ask me to run an experiment for it."

### Example: User asks to record an idea they already named

Input: "Park `episodic-memory` for now — cross-task memory of
post-run reflections, retrievable by task signature."

Output:

1. Confirm `episodic-memory` is unused in lab files.
2. Append the entry under `## Proposed` with the user's wording.
3. Reply: "Recorded `episodic-memory` in `lab/ideas.md`."
