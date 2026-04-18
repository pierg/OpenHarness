"""Deterministic markdown helpers for `lab/*.md` files.

The five lab/* skills are *prompts*: they instruct an agent on judgment-
heavy steps (which idea to propose, which decision to write, how to
phrase a comparison). The mechanical edits (move an idea between
sections, append cross-ref bullets, stub an experiment entry, fill the
result table from `summary.md`) need to be reproducible byte-for-byte
across humans, Cursor, codex, and the orchestrator daemon — so they
live here, exposed via the `uv run lab` CLI.

Every helper is **read-then-write idempotent where possible** and
refuses to silently corrupt: missing entry → raise `LabDocError`. The
caller (a skill or the orchestrator) decides how to surface the error.

All paths default to `lab/` at the repo root. Pass `lab_root=...` for
tests / worktrees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from openharness.lab.paths import LAB_ROOT

VALID_IDEAS_SECTIONS: tuple[str, ...] = ("Proposed", "Trying", "Graduated", "Rejected")
VALID_ROADMAP_SECTIONS: tuple[str, ...] = ("Up next", "Done")
VALID_THEMES: tuple[str, ...] = ("Architecture", "Runtime", "Tools", "Memory")


class LabDocError(RuntimeError):
    """Raised when a lab markdown mutation cannot be safely applied."""


@dataclass(slots=True)
class IdeaEntry:
    idea_id: str
    theme: str | None  # only meaningful while in `## Proposed`
    bullets: list[str]


# ----- file helpers ---------------------------------------------------------


def _ideas_path(lab_root: Path) -> Path:
    return lab_root / "ideas.md"


def _roadmap_path(lab_root: Path) -> Path:
    return lab_root / "roadmap.md"


def _experiments_path(lab_root: Path) -> Path:
    return lab_root / "experiments.md"


def _components_path(lab_root: Path) -> Path:
    return lab_root / "components.md"


def _read(path: Path) -> str:
    if not path.is_file():
        raise LabDocError(f"Missing lab file: {path}")
    return path.read_text()


def _write(path: Path, text: str) -> None:
    if not text.endswith("\n"):
        text = text + "\n"
    path.write_text(text)


# ----- generic markdown section parser --------------------------------------


def _split_top_sections(text: str, level: int = 2) -> list[tuple[str | None, str]]:
    """Split a markdown doc into [(heading_or_none, body), ...].

    The first chunk's heading is None — it's everything before the first
    heading at `level` (preamble / reset notes etc.).
    """
    prefix = "#" * level + " "
    parts: list[tuple[str | None, str]] = []
    cur_heading: str | None = None
    cur_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(prefix) and not line.startswith(prefix + "#"):
            parts.append((cur_heading, "\n".join(cur_lines).rstrip("\n")))
            cur_heading = line[len(prefix):].strip()
            cur_lines = []
        else:
            cur_lines.append(line)
    parts.append((cur_heading, "\n".join(cur_lines).rstrip("\n")))
    return parts


def _join_top_sections(parts: list[tuple[str | None, str]], level: int = 2) -> str:
    prefix = "#" * level + " "
    chunks: list[str] = []
    for i, (heading, body) in enumerate(parts):
        if heading is None:
            chunks.append(body)
        else:
            chunks.append(f"{prefix}{heading}\n\n{body}".rstrip())
    return "\n\n".join(c for c in chunks if c.strip()).rstrip() + "\n"


# ----- ideas.md operations --------------------------------------------------


_IDEA_HEADING_RE = re.compile(r"^####\s+(\S+)\s*$", re.MULTILINE)
_THEME_HEADING_RE = re.compile(r"^###\s+(\S.*)$", re.MULTILINE)


def parse_ideas(text: str) -> dict[str, list[IdeaEntry]]:
    """Return `{section_name: [IdeaEntry, ...]}` for `## Proposed/Trying/...`."""
    result: dict[str, list[IdeaEntry]] = {s: [] for s in VALID_IDEAS_SECTIONS}
    for heading, body in _split_top_sections(text, level=2):
        if heading is None or heading not in VALID_IDEAS_SECTIONS:
            continue
        # Inside Proposed, themes are `### Architecture` etc.
        # Inside Trying/Graduated/Rejected entries are flat.
        if heading == "Proposed":
            for theme, theme_body in _split_top_sections(body, level=3):
                if theme is None:
                    continue
                for entry in _entries_from_body(theme_body, theme=theme):
                    result[heading].append(entry)
        else:
            for entry in _entries_from_body(body, theme=None):
                result[heading].append(entry)
    return result


def _entries_from_body(body: str, *, theme: str | None) -> list[IdeaEntry]:
    chunks: list[IdeaEntry] = []
    cur_id: str | None = None
    cur_lines: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^####\s+(\S+)\s*$", line)
        if m:
            if cur_id is not None:
                chunks.append(IdeaEntry(cur_id, theme, _strip_blank(cur_lines)))
            cur_id = m.group(1).strip()
            cur_lines = []
        else:
            if cur_id is not None:
                cur_lines.append(line)
    if cur_id is not None:
        chunks.append(IdeaEntry(cur_id, theme, _strip_blank(cur_lines)))
    return chunks


def _strip_blank(lines: list[str]) -> list[str]:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def find_idea_entry(
    text: str, idea_id: str
) -> tuple[str, IdeaEntry] | None:
    parsed = parse_ideas(text)
    for section, entries in parsed.items():
        for entry in entries:
            if entry.idea_id == idea_id:
                return section, entry
    return None


def _render_idea_entry(entry: IdeaEntry) -> str:
    bullets = "\n".join(entry.bullets) if entry.bullets else ""
    return f"#### {entry.idea_id}\n\n{bullets}".rstrip() + "\n"


def _render_ideas(parsed: dict[str, list[IdeaEntry]], *, preamble: str) -> str:
    out: list[str] = []
    if preamble.strip():
        out.append(preamble.rstrip())
    for section in VALID_IDEAS_SECTIONS:
        out.append(f"## {section}")
        entries = parsed.get(section, [])
        if section == "Proposed":
            by_theme: dict[str, list[IdeaEntry]] = {}
            for entry in entries:
                by_theme.setdefault(entry.theme or "Architecture", []).append(entry)
            ordered_themes: list[str] = list(VALID_THEMES) + [
                t for t in by_theme if t not in VALID_THEMES
            ]
            any_theme = False
            for theme in ordered_themes:
                if theme not in by_theme:
                    continue
                any_theme = True
                out.append(f"### {theme}")
                for entry in by_theme[theme]:
                    out.append(_render_idea_entry(entry).rstrip())
            if not any_theme:
                out.append("_(none)_")
        else:
            if not entries:
                out.append("_(none)_")
            else:
                for entry in entries:
                    out.append(_render_idea_entry(entry).rstrip())
    return "\n\n".join(out).rstrip() + "\n"


def _split_ideas_preamble(text: str) -> tuple[str, dict[str, list[IdeaEntry]]]:
    parts = _split_top_sections(text, level=2)
    preamble_chunks: list[str] = []
    parsed: dict[str, list[IdeaEntry]] = {s: [] for s in VALID_IDEAS_SECTIONS}
    for heading, body in parts:
        if heading is None:
            preamble_chunks.append(body)
            continue
        if heading not in VALID_IDEAS_SECTIONS:
            preamble_chunks.append(f"## {heading}\n\n{body}")
            continue
        if heading == "Proposed":
            for theme, theme_body in _split_top_sections(body, level=3):
                if theme is None:
                    continue
                for entry in _entries_from_body(theme_body, theme=theme):
                    parsed[heading].append(entry)
        else:
            for entry in _entries_from_body(body, theme=None):
                parsed[heading].append(entry)
    return ("\n\n".join(preamble_chunks).rstrip(), parsed)


def move_idea(
    *,
    idea_id: str,
    target_section: str,
    cross_ref_bullet: str | None = None,
    target_theme: str | None = None,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Move an idea entry to another section, appending a cross-ref bullet.

    `target_theme` only applies when target_section == "Proposed" (rare).
    Returns the rewritten file text.
    """
    if target_section not in VALID_IDEAS_SECTIONS:
        raise LabDocError(
            f"Unknown ideas section: {target_section!r} (valid: {VALID_IDEAS_SECTIONS})"
        )
    path = _ideas_path(lab_root)
    text = _read(path)
    preamble, parsed = _split_ideas_preamble(text)

    found_section: str | None = None
    found_entry: IdeaEntry | None = None
    for section, entries in parsed.items():
        for entry in entries:
            if entry.idea_id == idea_id:
                found_section = section
                found_entry = entry
                break
        if found_entry is not None:
            break
    if found_entry is None or found_section is None:
        raise LabDocError(f"Idea {idea_id!r} not found in {path}")

    if found_section == target_section and target_section != "Proposed":
        # Idempotent no-op for non-Proposed: just append the cross-ref if missing.
        pass
    else:
        parsed[found_section].remove(found_entry)
        if target_section == "Proposed" and target_theme:
            found_entry.theme = target_theme
        elif target_section != "Proposed":
            found_entry.theme = None
        parsed[target_section].append(found_entry)

    if cross_ref_bullet:
        bullet = cross_ref_bullet.strip()
        if not bullet.startswith("-"):
            bullet = f"-   {bullet}"
        if bullet not in [b.strip() for b in found_entry.bullets]:
            found_entry.bullets.append("")
            found_entry.bullets.append(bullet)

    new_text = _render_ideas(parsed, preamble=preamble)
    _write(path, new_text)
    return new_text


def append_idea(
    *,
    idea_id: str,
    theme: str,
    motivation: str,
    sketch: str,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Append a fresh idea under `## Proposed > <theme>`."""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", idea_id):
        raise LabDocError(
            f"Idea id {idea_id!r} must be kebab-case ([a-z][a-z0-9-]*)."
        )
    path = _ideas_path(lab_root)
    text = _read(path)
    preamble, parsed = _split_ideas_preamble(text)
    if find_idea_entry(text, idea_id):
        raise LabDocError(f"Idea {idea_id!r} already exists in {path}")
    entry = IdeaEntry(
        idea_id=idea_id,
        theme=theme,
        bullets=[
            f"-   **Motivation:** {motivation.strip()}",
            f"-   **Sketch:** {sketch.strip()}",
        ],
    )
    parsed["Proposed"].append(entry)
    new_text = _render_ideas(parsed, preamble=preamble)
    _write(path, new_text)
    return new_text


def append_auto_proposed_idea(
    *,
    idea_id: str,
    motivation: str,
    sketch: str,
    source: str,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Append a follow-up suggestion to `## Auto-proposed` (a separate
    section the cross-experiment-critic owns; never touched by humans
    until they manually promote one to `## Proposed`).
    """
    if not re.fullmatch(r"[a-z][a-z0-9-]*", idea_id):
        raise LabDocError(
            f"Idea id {idea_id!r} must be kebab-case ([a-z][a-z0-9-]*)."
        )
    path = _ideas_path(lab_root)
    text = _read(path)

    if re.search(rf"^####\s+{re.escape(idea_id)}\s*$", text, re.MULTILINE):
        raise LabDocError(
            f"Idea id {idea_id!r} already exists somewhere in {path}; pick another."
        )

    if "## Auto-proposed" not in text:
        # Insert a new section at the very bottom so humans see it last.
        text = text.rstrip() + (
            "\n\n## Auto-proposed\n\n"
            "_Suggested by `cross-experiment-critic`. Promote to `## Proposed` "
            "manually if you want them to be runnable._\n"
        )

    entry = (
        f"\n#### {idea_id}\n\n"
        f"-   **Motivation:** {motivation.strip()}\n"
        f"-   **Sketch:** {sketch.strip()}\n"
        f"-   **Auto-proposed by:** {source.strip()}\n"
    )
    text = text.rstrip() + "\n" + entry
    _write(path, text)
    return text


# ----- experiments.md operations --------------------------------------------


def stub_experiment(
    *,
    slug: str,
    hypothesis: str,
    variant: str,
    on_date: date | None = None,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Insert a new in-progress experiment entry at the top of experiments.md."""
    path = _experiments_path(lab_root)
    text = _read(path)
    on_date = on_date or date.today()
    header = f"## {on_date.isoformat()} — {slug}"

    if header in text:
        raise LabDocError(f"Experiment {slug!r} already stubbed in {path}")

    stub = (
        f"{header}\n\n"
        f"-   **Hypothesis:** {hypothesis.strip()}\n"
        f"-   **Variant:** {variant.strip()}\n"
        "-   **Run:** _(filled after the run completes)_\n\n"
        "### Results\n\n"
        "| Leg | Trials | Passed | Errored | Pass rate | Total tokens | Cost (USD) |\n"
        "|-----|-------:|-------:|--------:|----------:|-------------:|-----------:|\n"
        "|     |        |        |         |           |              |            |\n\n"
        "### Notes\n\n"
        "-   _(filled after the run completes)_\n\n"
        "### Decision\n\n"
        "_(filled after the run completes)_\n"
    )
    parts = _split_top_sections(text, level=2)
    preamble = parts[0][1]
    rest = parts[1:]
    rebuilt = preamble.rstrip() + "\n\n" + stub.rstrip() + "\n"
    for heading, body in rest:
        if heading is None:
            continue
        rebuilt += f"\n## {heading}\n\n{body.rstrip()}\n"
    _write(path, rebuilt)
    return rebuilt


def fill_experiment_results(
    *,
    slug: str,
    run_path: str,
    results_table: str,
    notes: Iterable[str],
    decision: str,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Fill in the in-progress entry for `slug`.

    `results_table` should be the full markdown table including header rows.
    """
    path = _experiments_path(lab_root)
    text = _read(path)
    pattern = re.compile(
        r"(## \d{4}-\d{2}-\d{2} — " + re.escape(slug) + r"\b.*?)(?=\n## |\Z)",
        re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        raise LabDocError(f"No experiment entry for {slug!r} in {path}")
    entry = m.group(1)

    # Run line
    entry = re.sub(
        r"-\s*\*\*Run:\*\*[^\n]*\n",
        f"-   **Run:** [`{run_path}`](../{run_path})\n",
        entry,
        count=1,
    )
    # Results
    entry = re.sub(
        r"### Results.*?(?=### Notes)",
        f"### Results\n\n{results_table.strip()}\n\n",
        entry,
        count=1,
        flags=re.DOTALL,
    )
    notes_block = "\n".join(
        n if n.startswith("-") else f"-   {n}" for n in notes if n.strip()
    )
    entry = re.sub(
        r"### Notes.*?(?=### Decision)",
        f"### Notes\n\n{notes_block.strip()}\n\n",
        entry,
        count=1,
        flags=re.DOTALL,
    )
    entry = re.sub(
        r"### Decision.*\Z",
        f"### Decision\n\n{decision.strip()}\n",
        entry,
        count=1,
        flags=re.DOTALL,
    )
    new_text = text[: m.start()] + entry + text[m.end():]
    _write(path, new_text)
    return new_text


# ----- roadmap.md operations -----------------------------------------------


def add_roadmap_entry(
    *,
    slug: str,
    idea_id: str | None,
    hypothesis: str,
    plan: str,
    depends_on: str | None = None,
    cost: str | None = None,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Append a new entry to roadmap.md `## Up next` (bottom)."""
    path = _roadmap_path(lab_root)
    text = _read(path)
    if re.search(rf"^### {re.escape(slug)}\b", text, re.MULTILINE):
        raise LabDocError(f"Roadmap entry {slug!r} already exists in {path}")
    idea_line = (
        f"-   **Idea:** [`{idea_id}`](ideas.md#{idea_id})"
        if idea_id
        else "-   **Idea:** baseline snapshot"
    )
    bullets = [
        idea_line,
        f"-   **Hypothesis:** {hypothesis.strip()}",
        f"-   **Plan:** {plan.strip()}",
    ]
    if depends_on:
        bullets.append(f"-   **Depends on:** `{depends_on}`")
    if cost:
        bullets.append(f"-   **Cost:** {cost.strip()}")
    block = f"### {slug}\n\n" + "\n".join(bullets) + "\n"

    parts = _split_top_sections(text, level=2)
    rebuilt: list[str] = []
    inserted = False
    preamble = parts[0][1]
    rebuilt.append(preamble.rstrip())
    for heading, body in parts[1:]:
        if heading == "Up next" and not inserted:
            body = body.rstrip() + "\n\n" + block.rstrip()
            inserted = True
        rebuilt.append(f"## {heading}\n\n{body.rstrip()}")
    if not inserted:
        rebuilt.append(f"## Up next\n\n{block.rstrip()}")
    new_text = "\n\n".join(c for c in rebuilt if c.strip()).rstrip() + "\n"
    _write(path, new_text)
    return new_text


def move_roadmap_entry_to_done(
    *,
    slug: str,
    ran_link: str,
    outcome: str,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Cut a `## Up next` entry to the top of `## Done` with Ran/Outcome bullets."""
    path = _roadmap_path(lab_root)
    text = _read(path)
    parts = _split_top_sections(text, level=2)
    up_next: tuple[str | None, str] | None = None
    done: tuple[str | None, str] | None = None
    other: list[tuple[str | None, str]] = []
    preamble = parts[0]
    for heading, body in parts[1:]:
        if heading == "Up next":
            up_next = (heading, body)
        elif heading == "Done":
            done = (heading, body)
        else:
            other.append((heading, body))
    if up_next is None:
        raise LabDocError("No `## Up next` section in roadmap.md")
    pattern = re.compile(
        r"(### " + re.escape(slug) + r"\b.*?)(?=\n### |\Z)", re.DOTALL
    )
    m = pattern.search(up_next[1])
    if not m:
        raise LabDocError(f"No roadmap entry {slug!r} in `## Up next`")
    entry = m.group(1).rstrip()
    new_up_body = (up_next[1][: m.start()] + up_next[1][m.end():]).strip() or "_(none)_"
    entry += f"\n\n-   **Ran:** {ran_link.strip()}\n-   **Outcome:** {outcome.strip()}"
    done_body = (done[1] if done else "").strip()
    if not done_body or done_body == "_(none)_":
        new_done = entry
    else:
        new_done = entry + "\n\n" + done_body
    rebuilt = [preamble[1].rstrip(), f"## Up next\n\n{new_up_body}"]
    rebuilt.append(f"## Done\n\n{new_done}")
    for heading, body in other:
        rebuilt.append(f"## {heading}\n\n{body.rstrip()}")
    new_text = "\n\n".join(c for c in rebuilt if c.strip()).rstrip() + "\n"
    _write(path, new_text)
    return new_text


# ----- summary.md → results table renderer ---------------------------------


def render_results_table_from_summary(summary_md: str) -> str:
    """Extract a normalised Results table from `runs/.../results/summary.md`.

    Source columns (Harbor):
      Leg | Trials | Passed | Failed | Errored | Errors by Phase | Pass Rate
          | Mean Score | Tokens | Cost | Median Time
    Target (lab/experiments.md):
      Leg | Trials | Passed | Errored | Pass rate | Total tokens | Cost (USD)
    """
    rows: list[list[str]] = []
    for line in summary_md.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == "Leg":
            continue
        if cells and set(cells[0]) <= {"-", ":", " "}:
            continue
        if len(cells) < 11:
            continue
        rows.append(cells)

    out_lines = [
        "| Leg | Trials | Passed | Errored | Pass rate | Total tokens | Cost (USD) |",
        "|-----|-------:|-------:|--------:|----------:|-------------:|-----------:|",
    ]
    for cells in rows:
        leg = cells[0]
        trials = cells[1]
        passed = cells[2]
        errored = cells[4]
        try:
            pass_rate = f"{float(cells[6]) * 100:.1f}%"
        except ValueError:
            pass_rate = cells[6]
        try:
            tokens = f"{int(cells[8]):,}"
        except ValueError:
            tokens = cells[8]
        try:
            cost = f"${float(cells[9]):.2f}"
        except ValueError:
            cost = cells[9]
        out_lines.append(
            f"| {leg} | {trials} | {passed} | {errored} | {pass_rate} | {tokens} | {cost} |"
        )
    return "\n".join(out_lines)
