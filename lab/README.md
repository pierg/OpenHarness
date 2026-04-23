# Lab

The lab is a tiny audit surface in front of an autonomous research
loop. Three artifacts have completely different lifetimes:

| Artifact | What it is | Lifetime |
|----------|------------|----------|
| [`configs.md`](configs.md) | **The configuration tree** — trunk + branches + rejected + proposed *agent configs* (composed harnesses we've actually run). Current best knowledge of which compositions work. | Persistent state, mutated by `lab tree apply`. |
| [`components.md`](components.md) | **The catalog of atoms** — every architectural / runtime / tools / prompt / model building block we've experimented with, plus its current status (proposed / experimental / branch / validated / rejected / superseded). The vocabulary we compose from. | Persistent state, auto-bumped (forward only) by `lab tree apply` as a side-effect of verdicts; explicit edits via `lab components`. |
| [`experiments.md`](experiments.md) | **The journal** — append-only log of dated events; each entry records one experiment that proposed one diff to the tree. | Append-only, never edited. |

`configs.md` and `components.md` describe the same evidence at two
levels: configs.md records *which compositions* are validated
together, components.md records *which atoms* are validated, where
they're used, and why they were rejected. Verdicts are computed from
agent-config legs; component statuses are derived (forward only —
never demoted by automation). This split is descriptive today and
gives us the vocabulary for runtime composition later, without
constraining how we run experiments now.

Two human-curated planning surfaces feed the loop:

| File | Contents |
|------|----------|
| [`ideas.md`](ideas.md) | Themed backlog of agent improvements; humans own `## Proposed / Trying / Graduated / Rejected`, daemon owns `## Auto-proposed`. |
| [`roadmap.md`](roadmap.md) | Priority queue. Humans own `## Up next`, daemon writes follow-ups under `## Up next > ### Suggested` (humans promote). |

> **Audit-only files.** The five files above contain *only*
> high-signal content. They do not document themselves. The
> mental model lives here ([`README.md`](README.md)); the
> **scientific methodology contract** (slice / legs / repetitions
> / control / verdict thresholds) lives in
> [`METHODOLOGY.md`](METHODOLOGY.md); the operations runbook lives
> in [`OPERATIONS.md`](OPERATIONS.md); per-skill instructions live
> under [`.agents/skills/`](../.agents/skills/).

## How experiments mutate the tree

```
ideas.md                                                   roadmap.md
   │                                                          │
   │ human curates                                            │ human queues
   ▼                                                          ▼
   ├──────────────────────────────────────────────►   ## Up next
                                                              │
                                                              │ daemon picks the top entry
                                                              ▼
                                          lab-run-experiment
                                                              │
                                                              ▼
                                          runs/experiments/<id>/...
                                                              │
                                                              ▼
                                          experiment-critic + tree_ops.evaluate
                                                              │
                                                              ▼
                                                      TreeDiff
                                                  /     |     |     \
                                            graduate  add_branch  reject  no_op
                                              │           │         │       │
                                              │ STAGED    │ AUTO    │ AUTO  │ AUTO
                                              │           ▼         ▼       │
                                              │      configs.md mutated     │
                                              │           │                 │
                                              │           │ side-effect     │
                                              │           ▼ (forward bump)  │
                                              │      components.md statuses │
                                              ▼                             ▼
                                         (human runs                experiments.md
                                       `lab graduate                appends one
                                        confirm <slug>`)            ### Tree effect
```

Key invariants:

1.  Configs are the state. The journal is the log. Components are
    the derived view of which atoms have evidence. An experiment
    proposes exactly one TreeDiff over agent configs (Graduate /
    AddBranch / Reject / NoOp); configs.md may absorb it, the
    journal always records it, and components.md statuses bump
    forward as a side-effect.
2.  One trunk at a time.
    [`src/openharness/agents/configs/trunk.yaml`](../src/openharness/agents/configs/trunk.yaml)
    is the source of truth for the current best agent; everything else
    is "trunk + delta".
3.  Asymmetric autonomy. Daemon auto-applies AddBranch / Reject /
    NoOp. Trunk swaps require `uv run lab graduate confirm <slug>`
    (or the [`lab-graduate-component`](../.agents/skills/lab-graduate-component/SKILL.md)
    skill in Cursor).
4.  The default experiment is a paired ablation (trunk leg + 1
    mutation leg). Multi-leg shapes (3-leg multi-arm, broad-sweeps)
    are opt-in and used when the question's structure demands them.
    The scientific contract — slice shapes, leg counts, repetitions,
    control, verdict thresholds — is pinned in
    [`METHODOLOGY.md`](METHODOLOGY.md) and is the source of truth
    for whether an experiment is well-formed.

See [`METHODOLOGY.md`](METHODOLOGY.md) for the experimental-design
contract every skill, every spec, and `tree_ops.evaluate` must
satisfy. See [`OPERATIONS.md`](OPERATIONS.md) for the daemon's
tick, the file-ownership matrix, the codex auth rules, the
per-skill model profiles, and the operating commands. Per-skill
instructions live in [`.agents/skills/`](../.agents/skills/).


## How the skills compose into one closed loop

human → lab-propose-idea           (capture)
      ↘ 
        lab-plan-next               (queue / promote / Done)
          ↘
            lab-run-experiment      ← daemon picks top of `## Up next`
              ↘ scripts/exp/start.sh exec → runs/experiments/<id>/
                ↘ ingest
                  ↘ task-features × N           (parallel, cached)
                  ↘ trial-critic   × M          (one per trial)
                    ↘ experiment-critic          (multi-agent fan-out across tasks)
                      ↘ ingest-critiques
                        ↘ lab experiments synthesize    (narrative subsections)
                        ↘ lab tree apply                (### Tree effect, auto-apply or stage)
                          ↘ lab-reflect-and-plan        (### Suggested + ## Auto-proposed)
                          ↘ lab-plan-next               (move entry to ## Done)
                          ↘ every Mth: cross-experiment-critic  (components_perf + apex snapshot)
                          ↘ if Graduate → STAGED for human → lab-graduate-component

 agents only ever write JSON files and call uv run lab CLI commands. They never touch the markdown directly, never touch the DB directly. 