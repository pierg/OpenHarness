"""Synthesise the four narrative sections of a journal entry from critic JSONs.

The experiment-critic agent produces a structured JSON at
`<run_dir>/critic/experiment-critic.json`. This module renders that
JSON (plus per-task comparisons + DB stats) into the markdown blocks
that live in `lab/experiments.md`:

- ``### Aggregate``         — pass-rate / cost / token table per leg.
- ``### Mutation impact``   — what swapping the mutation actually moved.
- ``### Failure modes``     — recurring failure clusters from comparisons.
- ``### Linked follow-ups`` — ideas / branches / rejected items spawned.

Note: the ``### Tree effect`` block is owned by ``tree.apply_diff``
(it depends on ``tree_ops.evaluate`` and the verdict, not on the
experiment-critic JSON), so we never write it from here.

This module is **deterministic**. The narrative content comes from
the agent JSON; this module only stitches it into markdown.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openharness.lab import critic_io
from openharness.lab import db as labdb
from openharness.lab import lab_docs

NARRATIVE_SECTIONS: tuple[str, ...] = (
    "Aggregate",
    "Mutation impact",
    "Failure modes",
    "Linked follow-ups",
)


def synthesize(
    *,
    slug: str,
    instance_id: str,
    only_sections: list[str] | None = None,
    lab_root: Path | None = None,
) -> list[str]:
    """Synthesize and write the narrative sections for `slug`. Returns names written.

    Reads everything off disk + DB. Writes to `lab/experiments.md`
    via `lab_docs.set_section`. Skips sections that have no source
    data rather than writing empty stubs.
    """
    sections = list(only_sections) if only_sections else list(NARRATIVE_SECTIONS)
    sections = [s for s in sections if s in NARRATIVE_SECTIONS]

    run_dir = critic_io.run_dir_from_instance(instance_id)
    exp_payload = _load_experiment_critic(run_dir) if run_dir else {}
    cmp_payloads = _load_comparisons(run_dir) if run_dir else []
    leg_stats = _leg_stats_from_db(instance_id)

    written: list[str] = []
    for section in sections:
        body = _render_section(
            section,
            slug=slug,
            instance_id=instance_id,
            exp_payload=exp_payload,
            cmp_payloads=cmp_payloads,
            leg_stats=leg_stats,
        )
        if not body.strip():
            continue
        kwargs: dict[str, Any] = {"slug": slug, "section": section, "body": body}
        if lab_root is not None:
            kwargs["lab_root"] = lab_root
        try:
            lab_docs.set_section(**kwargs)
            written.append(section)
        except lab_docs.LabDocError:
            continue
    return written


def _load_experiment_critic(run_dir: Path) -> dict[str, Any]:
    path = critic_io.experiment_critic_path(run_dir)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _load_comparisons(run_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _, data in critic_io.iter_comparisons(run_dir):
        out.append(data)
    return out


def _leg_stats_from_db(instance_id: str) -> list[dict[str, Any]]:
    try:
        with labdb.reader() as conn:
            rows = conn.execute(
                """
                SELECT l.leg_id, l.agent_id,
                       count(t.trial_id)            AS n_trials,
                       sum(CAST(t.passed AS INT))   AS n_passed,
                       count(t.trial_id) - sum(CAST(t.passed AS INT))
                                                    AS n_failed,
                       coalesce(sum(t.cost_usd), 0) AS cost_usd,
                       coalesce(sum(t.duration_sec), 0) AS duration_sec
                FROM legs l
                LEFT JOIN trials t USING (instance_id, leg_id)
                WHERE l.instance_id = ?
                GROUP BY l.leg_id, l.agent_id
                ORDER BY l.leg_id
                """,
                [instance_id],
            ).fetchall()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        n = int(r[2] or 0)
        np = int(r[3] or 0)
        out.append({
            "leg_id": r[0],
            "agent_id": r[1] or r[0],
            "n_trials": n,
            "n_passed": np,
            "n_failed": int(r[4] or 0),
            "pass_rate": (np / n) if n else 0.0,
            "cost_usd": float(r[5] or 0.0),
            "duration_sec": float(r[6] or 0.0),
        })
    return out


# ----- per-section renderers ----------------------------------------------


def _render_section(
    section: str,
    *,
    slug: str,
    instance_id: str,
    exp_payload: dict[str, Any],
    cmp_payloads: list[dict[str, Any]],
    leg_stats: list[dict[str, Any]],
) -> str:
    if section == "Aggregate":
        return _render_aggregate(leg_stats=leg_stats, exp_payload=exp_payload)
    if section == "Mutation impact":
        return _render_mutation_impact(
            exp_payload=exp_payload, leg_stats=leg_stats,
        )
    if section == "Failure modes":
        return _render_failure_modes(
            exp_payload=exp_payload, cmp_payloads=cmp_payloads,
        )
    if section == "Linked follow-ups":
        return _render_followups(exp_payload=exp_payload)
    return ""


def _render_aggregate(
    *,
    leg_stats: list[dict[str, Any]],
    exp_payload: dict[str, Any],
) -> str:
    if not leg_stats:
        # Fall back to whatever the critic put in `aggregate` if the DB
        # is empty (e.g. a worktree with no ingest yet).
        agg = exp_payload.get("aggregate") or exp_payload.get("Aggregate")
        if isinstance(agg, str) and agg.strip():
            return agg.strip()
        return ""
    lines: list[str] = [
        "| Leg | Agent | Trials | Passed | Failed | Pass rate | Cost (USD) |",
        "|-----|-------|-------:|-------:|-------:|----------:|-----------:|",
    ]
    for s in leg_stats:
        lines.append(
            f"| `{s['leg_id']}` | `{s['agent_id']}` | "
            f"{s['n_trials']} | {s['n_passed']} | {s['n_failed']} | "
            f"{s['pass_rate']:.1%} | ${s['cost_usd']:.2f} |"
        )
    note = exp_payload.get("aggregate_note") or exp_payload.get("aggregate")
    if isinstance(note, str) and note.strip():
        lines.append("")
        lines.append(note.strip())
    return "\n".join(lines)


def _render_mutation_impact(
    *,
    exp_payload: dict[str, Any],
    leg_stats: list[dict[str, Any]],
) -> str:
    impact = exp_payload.get("mutation_impact") or exp_payload.get("Mutation impact")
    if isinstance(impact, str) and impact.strip():
        return impact.strip()
    if isinstance(impact, dict):
        bullets: list[str] = []
        for k, v in impact.items():
            bullets.append(f"-   **{k}:** {v}")
        return "\n".join(bullets)
    if isinstance(impact, list):
        return "\n".join(f"-   {item}" for item in impact)
    if len(leg_stats) >= 2:
        sorted_legs = sorted(leg_stats, key=lambda s: s["pass_rate"], reverse=True)
        best, worst = sorted_legs[0], sorted_legs[-1]
        delta_pp = (best["pass_rate"] - worst["pass_rate"]) * 100.0
        return (
            f"-   **Best leg:** `{best['leg_id']}` ({best['pass_rate']:.1%}, "
            f"${best['cost_usd']:.2f})\n"
            f"-   **Worst leg:** `{worst['leg_id']}` ({worst['pass_rate']:.1%}, "
            f"${worst['cost_usd']:.2f})\n"
            f"-   **Spread:** {delta_pp:+.1f} pp\n"
            "-   _(experiment-critic JSON missing a `mutation_impact` "
            "field; this is a DB-only fallback.)_"
        )
    return ""


def _render_failure_modes(
    *,
    exp_payload: dict[str, Any],
    cmp_payloads: list[dict[str, Any]],
) -> str:
    fm = exp_payload.get("failure_modes") or exp_payload.get("Failure modes")
    if isinstance(fm, str) and fm.strip():
        return fm.strip()
    if isinstance(fm, list) and fm:
        bullets: list[str] = []
        for item in fm:
            if isinstance(item, dict):
                name = item.get("name") or item.get("mode") or "(unnamed)"
                count = item.get("count")
                desc = item.get("description") or item.get("evidence") or ""
                head = f"-   **{name}**"
                if count is not None:
                    head += f" (×{count})"
                if desc:
                    head += f": {desc}"
                bullets.append(head)
            else:
                bullets.append(f"-   {item}")
        return "\n".join(bullets)
    counts: dict[str, int] = {}
    for cmp in cmp_payloads:
        for tag in cmp.get("failure_tags", []) or []:
            if isinstance(tag, str):
                counts[tag] = counts.get(tag, 0) + 1
    if not counts:
        return ""
    lines = ["| Failure tag | Count |", "|-------------|------:|"]
    for tag, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{tag}` | {n} |")
    lines.append("")
    lines.append(
        "_(derived from per-task `failure_tags`; experiment-critic JSON "
        "missing a `failure_modes` block.)_"
    )
    return "\n".join(lines)


def _render_followups(*, exp_payload: dict[str, Any]) -> str:
    fu = (
        exp_payload.get("linked_followups")
        or exp_payload.get("followups")
        or exp_payload.get("Linked follow-ups")
    )
    if isinstance(fu, str) and fu.strip():
        return fu.strip()
    if isinstance(fu, list) and fu:
        bullets: list[str] = []
        for item in fu:
            if isinstance(item, dict):
                kind = item.get("kind") or "follow-up"
                slug = item.get("slug") or item.get("idea_id") or ""
                why = item.get("why") or item.get("hypothesis") or ""
                head = f"-   **{kind}**"
                if slug:
                    head += f" `{slug}`"
                if why:
                    head += f": {why}"
                bullets.append(head)
            else:
                bullets.append(f"-   {item}")
        return "\n".join(bullets)
    return ""
