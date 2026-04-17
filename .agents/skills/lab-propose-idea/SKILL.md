---
name: lab-propose-idea
description: >
  Append a new entry to lab/ideas.md under "## Proposed > <Theme>".
  Use when the user proposes a new agent improvement ("what if we…",
  "I have an idea", "let's try doing X"), wants to capture an idea
  for later, or asks to record a proposal in the lab without running
  anything yet. Companion skills: lab, lab-plan-next,
  lab-run-experiment, lab-graduate-component.
---

# Lab — Propose Idea

Capture a new agent-improvement idea in `lab/ideas.md` so it can be
picked up later. This skill **does not** run any experiment, change
any agent YAML, edit `roadmap.md`, or commit anything. It only edits
`lab/ideas.md`.

The lab markdowns are deliberately stripped of self-documenting
prose. The entry shape, theme list, and structural rules below live
in this skill — never copy them back into the markdown.

## When to Use

- User proposes a new agent improvement (memory layer, new tool,
  prompt change, runtime mechanism, architecture variant, …).
- User asks to "park", "log", or "record" an idea for later.
- User has just finished a discussion and wants to write down what
  to try next.

Do **not** use this skill if the user says "let's try it" or "queue
it" — that's `lab-plan-next` (or `lab-run-experiment` for immediate
execution). Capture the idea here as a prerequisite step, then
hand off.

## Instructions

### 1. Confirm the idea has a stable id

If the user hasn't named it, propose a kebab-case id and confirm
before writing. Examples: `executor-bash-timeout-aware-retry`,
`planner-rerank`, `tool-result-summariser`, `skill-memory`.

Before adding, **check for collisions across all lab files**:

```bash
rg -n "^####? " lab/ideas.md lab/components.md
rg -n "^### " lab/roadmap.md lab/experiments.md
```

If the id is already used in any of the four lab files, pick a
different one. Ids are permanent.

### 2. Pick the theme

`## Proposed` is grouped under four `### <Theme>` subsections:

| Theme | Use for… |
|-------|----------|
| **Architecture** | new agent shapes, planner/executor/critic compositions, reranking, parallel sampling. |
| **Runtime** | mid-loop mechanisms (loop-guard, context compaction, budget tweaks, retry policies, tool-output summarisation). |
| **Tools** | new tools wired into agents, or ablations of existing tool wiring. |
| **Memory** | cross-task or cross-run state (skill notes, episodic stores, retrieval). |

Pick by where the change actually lives. If none fit cleanly, add a
new `### <NewTheme>` heading under `## Proposed` — but err toward
the existing four.

### 3. Draft the entry

Use this exact shape. The heading is `####` (four hashes); themes
are `###`. Two bullets, no more:

```markdown
#### <kebab-id>

-   **Motivation:** one sentence on why we'd want this.
-   **Sketch:** one or two sentences on what the change actually is.
```

Do **not** add:

- A `Status:` field — state is encoded by which section the entry
  sits in.
- An `Expected experiment:` field — that detail belongs in the
  `roadmap.md` entry the moment the idea is queued.
- Any other field. The two bullets are the whole shape.

### 4. Insert under the right theme

Read `lab/ideas.md`. Find `## Proposed > ### <Theme>`. Append the
new `#### <kebab-id>` entry at the **bottom** of that theme
subsection (preserve order — first in, first out). Do not touch any
other section.

### 5. Confirm and report

After saving, report:

- The id used.
- The theme it landed under.
- The motivation in one line.
- The path: `lab/ideas.md`.
- A reminder: "If you want to actually try this, ask me to queue
  it (`lab-plan-next`) or run it directly
  (`lab-run-experiment`)."

Do **not**:

- Edit `lab/roadmap.md`, `lab/experiments.md`, or
  `lab/components.md`.
- Touch any agent YAML.
- Run any experiment.
- Create a git worktree, commit, or push.

## Examples

### Example: User has a vague idea

Input: "Could we make the planner generate multiple plans and pick
the best one?"

Output:

1. Propose id `planner-rerank`, confirm with the user.
2. Theme: `Architecture` (it's a new agent shape).
3. Append under `## Proposed > Architecture`:

   ```markdown
   #### planner-rerank

   -   **Motivation:** First plan the planner produces is often mediocre.
   -   **Sketch:** Generate N plans, rerank with a small judge model (same family, smaller size), execute the top-1.
   ```

4. Reply: "Recorded `planner-rerank` under
   `## Proposed > Architecture` in `lab/ideas.md`. If you want to
   actually try it, ask me to queue it (`lab-plan-next`) or run
   it directly (`lab-run-experiment`)."

### Example: User asks to record an idea they already named

Input: "Park `episodic-memory` for now — cross-task memory of
post-run reflections, retrievable by task signature."

Output:

1. Confirm `episodic-memory` is unused in lab files.
2. Theme: `Memory`.
3. Append the entry under `## Proposed > Memory` with the user's
   wording.
4. Reply: "Recorded `episodic-memory` under `## Proposed > Memory`
   in `lab/ideas.md`."
