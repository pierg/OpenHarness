---
name: lab-plan-next
description: >
  Manage the lab/roadmap.md priority queue. Use when the user says
  "queue X for next", "add X to the roadmap", "what's the next
  experiment?", "reorder the queue", "bump X up", "move X to Done",
  or wants to see/edit the planning surface for upcoming experiments.
  Edits lab/roadmap.md only (and the corresponding cross-ref bullet
  on the idea entry in lab/ideas.md when promoting from Proposed).
  Companion skills: lab, lab-propose-idea, lab-run-experiment,
  lab-graduate-component.
---

# Lab — Plan Next

Manage the priority queue in `lab/roadmap.md`. The roadmap is the
"what's next" surface — a mutable, ranked list of concrete
experiments to run, distinct from the unranked themed backlog in
`lab/ideas.md`.

The lab markdowns are deliberately stripped of self-documenting
prose. The entry shape and structural rules below live in this skill
— never copy them back into the markdown.

## When to Use

- User wants to add a planned experiment to the queue ("queue
  `loop-guard-tb2-paired` for next", "let's plan to do X after the
  full sweep").
- User asks "what's the next experiment?" or "what's on the
  roadmap?" → read `lab/roadmap.md` and summarise.
- User wants to reorder, promote, or demote an item in `## Up next`
  ("bump `loop-guard` up", "move stronger-model-baseline down").
- An experiment landed in `experiments.md` and the matching roadmap
  entry needs to be moved to `## Done` (this is normally done as
  the last step of `lab-run-experiment`, but this skill handles
  late catch-ups too).

Do **not** use this skill when:

- The user is proposing a *new idea* with no commitment to run it
  → that's `lab-propose-idea`.
- The user is actually executing the experiment now → that's
  `lab-run-experiment`.

## Roadmap structure

```
## Up next            ← priority order, top = next to run. Mutable; reorder freely.
  ### Suggested       ← daemon-only sub-section (lab-reflect-and-plan). Humans promote.
## Done               ← newest at top. Entries land here when their experiment runs (regardless of outcome).
```

There is no `## Later` section. If something isn't worth queueing
yet, leave it as an idea in `lab/ideas.md` and queue it when ready.

The `### Suggested` substream lives **inside** `## Up next` because
it's a sibling priority queue the daemon writes to without
touching the human queue. Humans:

-   *promote* a suggestion into the main queue with
    `uv run lab roadmap promote <slug>` (this skill); or
-   ignore / drop it (just delete the section).

**Never edit `### Suggested` to insert your own entries** — use
`## Up next` directly for human-curated items.

## Entry shape

```markdown
### <slug>

-   **Idea:** [`<idea-id>`](ideas.md#<idea-id>)   _(or: baseline snapshot / infrastructure)_
-   **Hypothesis:** one sentence on what we expect to learn.
-   **Plan:** one paragraph — agents, dataset slice, what varies vs the current baseline.
-   **Depends on:** `<other-slug>`   _(omit if nothing)_
-   **Cost:** ~$X, ~Y hours wall-clock   _(omit if smoke / unknown)_
```

Slug convention: `<idea-id>-<short-context>` (e.g.
`loop-guard-tb2-paired`, `tb2-baseline-full-sweep`). For meta
experiments, set `**Idea:**` to `baseline snapshot` or
`infrastructure` and use a descriptive slug.

When moved to `## Done`, **append two bullets** at the end of the
entry (do not rewrite the existing ones):

```markdown
-   **Ran:** [<experiment-slug>](experiments.md#YYYY-MM-DD--<slug>)
-   **Outcome:** one sentence — headline pass rates + decision (graduate / iterate / reject).
```

Do **not** add a `Status:` field — section membership encodes state.

## Instructions

### 1. Read the current roadmap

Always start by reading `lab/roadmap.md` to see the current ordering
of `## Up next` and `## Done`. Summarise the top 1–3 entries in
`## Up next` for the user before acting if it's not obvious which
item they're referring to.

### 2. Decide the action

One of:

-   **Add a new entry** to `## Up next` (human-driven).
-   **Promote a suggestion** from `### Suggested` to the main `##
    Up next` queue (`uv run lab roadmap promote <slug>`).
-   **Reorder** existing entries within `## Up next`.
-   **Move a completed entry to `## Done`** with a link to the
    matching `experiments.md` section.

Confirm with the user in one short sentence before mutating.

### 3. Adding a new entry

If the entry corresponds to an existing idea (the common case), the
**idea id must already exist** in `lab/ideas.md`. If it doesn't,
run `lab-propose-idea` first.

After picking the slug, **use the CLI for the mechanical edits** —
it appends in the right shape, refuses duplicate slugs, and only
touches the file you ask it to:

```bash
# 1. Append the entry to ## Up next (at the bottom of that section).
#    If you need it higher in priority, do the reorder by editing
#    the file by hand after — the CLI doesn't currently insert at a
#    specific position because reorders are inherently judgment-heavy.
uv run lab roadmap add <slug> \
  --idea <idea-id> \
  --hypothesis "<one sentence>" \
  --plan "<one paragraph>" \
  [--depends-on <other-slug>] \
  [--cost "~\$X, ~Y hours wall-clock"]

# 2. Promote the idea to ## Trying with a back-reference.
uv run lab idea move <idea-id> trying \
  --cross-ref "**Trying in:** [<slug>](roadmap.md#<slug>)"
```

For meta-experiments (`baseline snapshot` / `infrastructure`), pass
no `--idea` flag and skip step 2 entirely.

### 4a. Promoting a suggestion

```bash
# Move the named slug from `## Up next > ### Suggested` into the
# main `## Up next` queue, preserving its bullets.
uv run lab roadmap promote <slug>
```

The CLI errors if `<slug>` isn't in `### Suggested` or already in
the main queue. Use this whenever the daemon's
`lab-reflect-and-plan` proposed something worth committing to.

### 4. Reordering

Cut and paste entries within `## Up next` to match the new ordering.
No need to leave a comment — the roadmap is mutable by design. If
the user wants to record *why* they reprioritised, mention it in
the chat reply, not in the file.

### 5. Moving a completed entry to `## Done`

When an experiment that was queued in the roadmap lands an entry in
`experiments.md`, use:

```bash
uv run lab roadmap done <slug> \
  --ran "[<experiment-slug>](experiments.md#YYYY-MM-DD--<slug>)" \
  --outcome "<one sentence — headline pass rates + decision>"
```

This cuts the entry from `## Up next`, prepends it to `## Done`
(newest first), and appends the `**Ran:**` and `**Outcome:**`
bullets. Move to `## Done` regardless of whether the experiment
succeeded — the roadmap records the *plan*; the experiment records
the *evidence*.

### 6. Confirm and report

After saving, report:

- The action taken (added / reordered / moved to Done).
- The current top 1–3 of `## Up next` so the user sees what's now
  on deck.
- The path: `lab/roadmap.md`.

Do **not**:

- Edit `lab/experiments.md`, `lab/configs.md`, or `lab/components.md`.
- Touch any agent YAML or experiment spec.
- Run any experiment.
- Create a git worktree, commit, or push.

## Examples

### Example: User wants to queue an idea

Input: "Let's queue `loop-guard` for after the full sweep."

Output:

1. Read `lab/roadmap.md`. Confirm `loop-guard` already exists in
   `lab/ideas.md > ## Proposed > Runtime` (it does).
2. Confirm with user: "Adding `loop-guard-tb2-paired` to
   `## Up next` with `Depends on: tb2-baseline-full-sweep`.
   Promoting the idea entry to `## Trying`."
3. Append the new entry under `## Up next` (below
   `tb2-baseline-full-sweep`).
4. In `lab/ideas.md`, move `#### loop-guard` from
   `## Proposed > Runtime` to `## Trying`. Append the
   `**Trying in:**` bullet.
5. Reply: "Queued `loop-guard-tb2-paired`. Top of `## Up next` is
   still `tb2-baseline-full-sweep`."

### Example: User asks what's next

Input: "What's the next experiment?"

Output:

1. Read `lab/roadmap.md`.
2. Reply with a 3–5 line summary of the top of `## Up next`:
   slug, hypothesis, expected cost, depends-on. Mention there are
   N more items queued if relevant.

### Example: Move a finished experiment to Done

Input: "`tb2-baseline-full-sweep` finished. Update the roadmap."

Output:

1. Confirm there's a corresponding entry at the top of
   `lab/experiments.md` (e.g.
   `## 2026-04-18 — tb2-baseline-full-sweep`).
2. Cut the `### tb2-baseline-full-sweep` entry from `## Up next`.
3. Paste at the top of `## Done`. Append the `**Ran:**` and
   `**Outcome:**` bullets.
4. Reply: "Moved `tb2-baseline-full-sweep` to `## Done`. Top of
   `## Up next` is now `<next-slug>`."

### Example: Refuse a roadmap entry without an idea

Input: "Queue `gemini-thinking-mode-enabled`."

Output:

1. Search `lab/ideas.md` — id not present.
2. Refuse: "No `gemini-thinking-mode-enabled` idea in
   `lab/ideas.md`. Propose it first via `lab-propose-idea`, then I
   can queue it."

(Exception: the slug is for a `baseline snapshot` or
`infrastructure` entry — those don't need a backing idea, since
they're meta-experiments about the framework itself.)
