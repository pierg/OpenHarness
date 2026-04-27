"""Deterministic markdown helpers for `lab/*.md` files.

The lab skills are *prompts*: they instruct an agent on judgment-
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

VALID_IDEAS_SECTIONS: tuple[str, ...] = ("Proposed", "Trying", "Accepted", "Rejected")
VALID_ROADMAP_SECTIONS: tuple[str, ...] = ("Up next", "Done")
VALID_THEMES: tuple[str, ...] = (
    "Prompting",
    "Architecture",
    "Memory",
    "Tools",
    "Runtime",
    "Exploration",
    "Test-Time Inference",
    "Model Policy",
    "Evaluation",
)

# Sections of a tree-shaped journal entry (the new shape; see plan).
JOURNAL_SECTIONS: tuple[str, ...] = (
    "Aggregate",
    "Mutation impact",
    "Failure modes",
    "Tree effect",
    "Linked follow-ups",
)

# Sections inside `lab/configs.md` (the measured configuration state).
TREE_SECTIONS: tuple[str, ...] = ("Current best", "Rejected", "Proposed")


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


def _configs_path(lab_root: Path) -> Path:
    return lab_root / "configs.md"


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
    heading at `level` (preamble / reset notes etc.). Bodies are
    stripped of leading and trailing blank lines so callers that
    rebuild via ``f"## {h}\n\n{body}"`` don't accumulate blank lines on
    every round-trip.
    """
    prefix = "#" * level + " "
    parts: list[tuple[str | None, str]] = []
    cur_heading: str | None = None
    cur_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith(prefix) and not line.startswith(prefix + "#"):
            parts.append((cur_heading, "\n".join(cur_lines).strip("\n")))
            cur_heading = line[len(prefix):].strip()
            cur_lines = []
        else:
            cur_lines.append(line)
    parts.append((cur_heading, "\n".join(cur_lines).strip("\n")))
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
        # Inside Trying/Accepted/Rejected entries are flat.
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
    if (
        target_section == "Proposed"
        and target_theme is not None
        and target_theme not in VALID_THEMES
    ):
        raise LabDocError(
            f"Unknown idea theme: {target_theme!r} (valid: {VALID_THEMES})"
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
    if theme not in VALID_THEMES:
        raise LabDocError(
            f"Unknown idea theme: {theme!r} (valid: {VALID_THEMES})"
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
        text = text.rstrip() + "\n\n## Auto-proposed\n"

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


def move_roadmap_entry(
    *,
    slug: str,
    before: str | None = None,
    after: str | None = None,
    to_top: bool = False,
    to_bottom: bool = False,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Reorder one ``## Up next > ### <slug>`` entry within the main queue."""
    choices = [bool(before), bool(after), to_top, to_bottom]
    if sum(int(v) for v in choices) != 1:
        raise LabDocError(
            "roadmap move requires exactly one of before/after/to_top/to_bottom"
        )
    path = _roadmap_path(lab_root)
    text = _read(path)
    parts = _split_top_sections(text, level=2)
    rebuilt: list[str] = []
    moved = False
    for heading, body in parts:
        if heading is None:
            if body.strip():
                rebuilt.append(body.rstrip())
            continue
        if heading != "Up next":
            rebuilt.append(f"## {heading}\n\n{body.rstrip()}")
            continue
        new_body, moved = _move_up_next_entry(
            body,
            slug=slug,
            before=before,
            after=after,
            to_top=to_top,
            to_bottom=to_bottom,
        )
        rebuilt.append(f"## Up next\n\n{new_body.rstrip()}")
    if not moved:
        raise LabDocError(f"No `## Up next > ### {slug}` entry to move in roadmap.md.")
    new_text = "\n\n".join(c for c in rebuilt if c.strip()).rstrip() + "\n"
    _write(path, new_text)
    return new_text


# ----- summary.md → results table renderer ---------------------------------


# ----- experiments.md: structured journal-entry CRUD ----------------------
#
# The new tree-shaped journal entries are nested:
#
#     ## YYYY-MM-DD — <slug>
#     - **Type:** ...
#     - **Hypothesis:** ...
#
#     ### Aggregate
#     ### Mutation impact
#     ### Failure modes
#     ### Tree effect
#     ### Linked follow-ups
#
# `set_section(slug, "Tree effect", body)` rewrites exactly one ###
# section inside one ## entry. Idempotent. Tolerates missing entries
# by raising `LabDocError` so the caller (CLI / runner) decides.


def _entry_pattern(slug: str) -> "re.Pattern[str]":
    return re.compile(
        r"(?P<header>## \d{4}-\d{2}-\d{2} — "
        + re.escape(slug)
        + r"\b.*?)(?=\n## |\Z)",
        re.DOTALL,
    )


def _entry_text(text: str, slug: str) -> tuple[int, int, str]:
    m = _entry_pattern(slug).search(text)
    if not m:
        raise LabDocError(f"No experiment entry for slug {slug!r}.")
    return m.start(), m.end(), m.group("header")


def get_section(*, slug: str, section: str, lab_root: Path = LAB_ROOT) -> str | None:
    """Return the body of `### <section>` inside `## … — <slug>`, or None."""
    text = _read(_experiments_path(lab_root))
    try:
        _, _, entry = _entry_text(text, slug)
    except LabDocError:
        return None
    pat = re.compile(
        r"^### " + re.escape(section) + r"\s*\n(?P<body>.*?)(?=\n### |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    m = pat.search(entry)
    if not m:
        return None
    return m.group("body").strip("\n")


def set_section(
    *,
    slug: str,
    section: str,
    body: str,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Replace (or create) `### <section>` inside the journal entry for `slug`.

    Sections are inserted in the canonical order from `JOURNAL_SECTIONS`
    if they don't yet exist, so the entry always reads top-down in
    the same shape.
    """
    path = _experiments_path(lab_root)
    text = _read(path)
    start, end, entry = _entry_text(text, slug)
    body = body.rstrip() + "\n"
    section_header = f"### {section}\n"

    pat = re.compile(
        r"^### " + re.escape(section) + r"\s*\n.*?(?=\n### |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    new_block = section_header + body
    if pat.search(entry):
        new_entry = pat.sub(new_block.rstrip(), entry, count=1)
    else:
        # Insert in canonical order: walk JOURNAL_SECTIONS, find the next
        # one already present, insert before it. If none, append.
        try:
            idx = JOURNAL_SECTIONS.index(section)
        except ValueError:
            idx = len(JOURNAL_SECTIONS)
        successor: re.Match | None = None
        for s in JOURNAL_SECTIONS[idx + 1 :]:
            m = re.search(
                r"^### " + re.escape(s) + r"\s*$", entry, re.MULTILINE
            )
            if m:
                successor = m
                break
        if successor:
            insert_at = successor.start()
            new_entry = (
                entry[:insert_at].rstrip()
                + "\n\n"
                + new_block.rstrip()
                + "\n\n"
                + entry[insert_at:].lstrip()
            )
        else:
            new_entry = entry.rstrip() + "\n\n" + new_block.rstrip() + "\n"
    new_text = text[:start] + new_entry.rstrip() + "\n" + text[end:]
    _write(path, new_text)
    return new_text


def journal_entry_exists(slug: str, *, lab_root: Path = LAB_ROOT) -> bool:
    text = _read(_experiments_path(lab_root))
    return _entry_pattern(slug).search(text) is not None


def append_journal_entry(
    *,
    slug: str,
    type_: str,
    current_best_at_runtime: str,
    mutation: str | None,
    hypothesis: str,
    run_path: str | None,
    branch: str | None = None,
    on_date: date | None = None,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Insert a new tree-shaped journal entry at the top of `experiments.md`.

    Stub-shaped: header bullets present, every `### <section>` empty,
    populated later by `synthesize` and `decision apply`.

    ``branch`` is the experiment branch name (e.g. ``lab/<slug>``) the
    code variant lives on. Always rendered — either as the recorded
    branch (if provided) or as the placeholder ``_(pending finalize)_``
    so downstream tooling and humans can see at a glance whether the
    run has been finalized into a PR.
    """
    path = _experiments_path(lab_root)
    text = _read(path)
    on_date = on_date or date.today()
    header = f"## {on_date.isoformat()} — {slug}"
    if header in text:
        raise LabDocError(f"Experiment {slug!r} already in journal.")

    bullets = [
        f"-   **Type:** {type_.strip()}",
        f"-   **Current best at run-time:** {current_best_at_runtime.strip()}",
    ]
    if mutation:
        bullets.append(f"-   **Mutation:** {mutation.strip()}")
    bullets.append(f"-   **Hypothesis:** {hypothesis.strip()}")
    if run_path:
        bullets.append(f"-   **Run:** [`{run_path}`](../{run_path})")
    else:
        bullets.append("-   **Run:** _(filled after the run completes)_")
    if branch:
        bullets.append(f"-   **Branch:** `{branch.strip()}`")
    else:
        bullets.append("-   **Branch:** _(pending finalize)_")

    sections = "\n\n".join(f"### {s}\n\n_(pending)_" for s in JOURNAL_SECTIONS)
    stub = f"{header}\n\n" + "\n".join(bullets) + "\n\n" + sections + "\n"

    parts = _split_top_sections(text, level=2)
    preamble = parts[0][1]
    rebuilt = preamble.rstrip() + "\n\n" + stub.rstrip() + "\n"
    for heading, body in parts[1:]:
        if heading is None:
            continue
        rebuilt += f"\n## {heading}\n\n{body.rstrip()}\n"
    _write(path, rebuilt)
    return rebuilt


# Match the Branch bullet inside an entry. The bullet is always
# present after a `lab experiments append-entry`; this regex finds it
# whether it currently holds the placeholder or a real branch name.
_BRANCH_BULLET_RE = re.compile(
    r"^(-\s+\*\*Branch:\*\*\s+).*$",
    re.MULTILINE,
)

# Match the Run bullet (placeholder or already-filled).
_RUN_BULLET_RE = re.compile(
    r"^(-\s+\*\*Run:\*\*\s+).*$",
    re.MULTILINE,
)


def set_journal_run_path(
    *,
    slug: str,
    instance_id: str,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Replace the placeholder ``**Run:**`` bullet with the real path.

    Called by the run phase once an instance id is known. Idempotent;
    safe to call repeatedly with the same id.
    """
    path = _experiments_path(lab_root)
    text = _read(path)
    start, end, entry = _entry_text(text, slug)
    rendered = (
        f"-   **Run:** [`runs/experiments/{instance_id}`]"
        f"(../runs/experiments/{instance_id})"
    )
    new_entry, n = _RUN_BULLET_RE.subn(rendered, entry, count=1)
    if n == 0:
        raise LabDocError(
            f"No **Run:** bullet found in journal entry for {slug!r}."
        )
    new_text = text[:start] + new_entry.rstrip() + "\n" + text[end:]
    _write(path, new_text)
    return new_text


def set_journal_branch(
    *,
    slug: str,
    branch: str,
    pr_url: str | None = None,
    rejected_reason: str | None = None,
    discarded_sha: str | None = None,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Replace the ``**Branch:**`` header bullet on the journal entry for ``slug``.

    Rendering modes (controlled by which arguments are set):

    -   ``pr_url`` provided -> "Branch: [<branch>](<pr_url>)".
        When ``rejected_reason`` / ``discarded_sha`` are also passed,
        append a closed-experiment-PR note so reject/no_op outcomes can
        link to the reviewed diff without pretending the discarded code
        landed on ``main``.
    -   ``rejected_reason`` provided without ``pr_url`` -> "Branch:
        <branch> — not opened (<reason>)". When ``discarded_sha`` is
        also passed the short SHA is appended so a curious human can
        fetch the deleted branch back later (`git fetch origin
        <sha>:retro/...`).
    -   Neither -> just "Branch: <branch>". Used for intermediate
        states (e.g. preflight finished, branch exists, no PR yet).

    Idempotent: re-running with the same arguments produces the same
    file. Only ever rewrites the one bullet, never anything else in
    the entry.
    """
    path = _experiments_path(lab_root)
    text = _read(path)
    start, end, entry = _entry_text(text, slug)

    if pr_url:
        suffix = ""
        if rejected_reason:
            suffix = f" — closed experiment PR ({rejected_reason}"
            if discarded_sha:
                suffix += f"; discarded=`{discarded_sha[:7]}`"
            suffix += ")"
        rendered = f"-   **Branch:** [`{branch}`]({pr_url}){suffix}"
    elif rejected_reason:
        suffix = ""
        if discarded_sha:
            suffix = f"; head=`{discarded_sha[:7]}`"
        rendered = f"-   **Branch:** `{branch}` — not opened ({rejected_reason}{suffix})"
    else:
        rendered = f"-   **Branch:** `{branch}`"

    if not _BRANCH_BULLET_RE.search(entry):
        # Older entries (pre-Branch-bullet) don't have it yet. Insert
        # immediately after the **Run:** bullet so the header order
        # stays canonical.
        run_re = re.compile(r"^-\s+\*\*Run:\*\*.*$", re.MULTILINE)
        m = run_re.search(entry)
        if not m:
            raise LabDocError(
                f"Cannot place Branch bullet in entry {slug!r}: "
                "no **Run:** bullet found to anchor against."
            )
        new_entry = entry[: m.end()] + "\n" + rendered + entry[m.end():]
    else:
        new_entry = _BRANCH_BULLET_RE.sub(rendered, entry, count=1)

    new_text = text[:start] + new_entry.rstrip() + "\n" + text[end:]
    _write(path, new_text)
    return new_text


# ----- configs.md: configuration-tree CRUD ---------------------------------


@dataclass(slots=True)
class TreeRejected:
    branch_id: str
    reason: str
    evidence: str | None = None


@dataclass(slots=True)
class TreeProposed:
    branch_id: str
    sketch: str
    linked_idea: str | None = None


@dataclass(slots=True)
class TreeSnapshot:
    current_best_id: str
    current_best_anchor: str | None
    rejected: list[TreeRejected]
    proposed: list[TreeProposed]


def _ensure_configs_skeleton(text: str) -> str:
    """If configs.md is missing any state section, add an empty one."""
    out = text.rstrip()
    for s in TREE_SECTIONS:
        if not re.search(rf"^## {re.escape(s)}\b", out, re.MULTILINE):
            out += f"\n\n## {s}\n\n_(none)_\n"
    return out + "\n"


def tree_snapshot(*, lab_root: Path = LAB_ROOT) -> TreeSnapshot:
    """Parse `lab/configs.md` and return the current lab configuration state."""
    path = _configs_path(lab_root)
    text = _read(path)
    text = _ensure_configs_skeleton(text)
    parts = dict(_split_top_sections(text, level=2))

    current_body = parts.get("Current best", "")
    current_best_id = "basic"
    current_best_anchor: str | None = None
    m_id = re.search(r"\*\*Agent:\*\*\s*\[`([^`]+)`\]", current_body)
    if m_id:
        current_best_id = m_id.group(1)
    m_why = re.search(r"\*\*Why:\*\*\s*(.+)", current_body)
    if m_why:
        current_best_anchor = m_why.group(1).strip()

    rejected: list[TreeRejected] = []
    for row in _parse_md_table(parts.get("Rejected", "")):
        if len(row) < 2:
            continue
        rejected.append(TreeRejected(
            branch_id=_strip_md_link(row[0]),
            reason=row[1],
            evidence=row[2] if len(row) > 2 else None,
        ))

    proposed: list[TreeProposed] = []
    proposed_body = parts.get("Proposed", "")
    for row in _parse_md_table(proposed_body):
        if len(row) < 2:
            continue
        proposed.append(TreeProposed(
            branch_id=_strip_md_link(row[0]),
            sketch=row[1],
            linked_idea=row[2] if len(row) > 2 else None,
        ))

    return TreeSnapshot(
        current_best_id=current_best_id,
        current_best_anchor=current_best_anchor,
        rejected=rejected,
        proposed=proposed,
    )


def _parse_md_table(body: str) -> list[list[str]]:
    """Return data rows (no header, no separator) of a single MD table."""
    rows: list[list[str]] = []
    saw_header = False
    saw_sep = False
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        # Skip header + separator row.
        if not saw_header:
            saw_header = True
            continue
        if not saw_sep:
            if cells and set(cells[0]) <= {"-", ":", " "}:
                saw_sep = True
                continue
            saw_sep = True
        rows.append(cells)
    return rows


def _strip_md_link(cell: str) -> str:
    """Return the visible text of a markdown link, or the raw cell."""
    cell = cell.strip()
    m = re.match(r"^\[`?([^\]`]+)`?\]\([^)]+\)$", cell)
    if m:
        return m.group(1)
    m = re.match(r"^`([^`]+)`$", cell)
    if m:
        return m.group(1)
    return cell


def set_current_best(
    *,
    agent_id: str,
    reason: str,
    journal_link: str | None = None,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Rewrite `## Current best` in configs.md to point at `agent_id`."""
    path = _configs_path(lab_root)
    text = _read(path)
    text = _ensure_configs_skeleton(text)
    body_lines = [
        f"-   **Agent:** [`{agent_id}`](../src/openharness/agents/configs/{agent_id}.yaml)",
        f"-   **Why:** {reason.strip()}",
    ]
    if journal_link:
        body_lines.append(f"-   **Anchored by:** {journal_link.strip()}")
    new_body = "\n".join(body_lines)
    new_text = _replace_top_section(text, "Current best", new_body)
    _write(path, new_text)
    return new_text


def add_rejected(
    *,
    branch_id: str,
    reason: str,
    evidence: str,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Append a row to `## Rejected` (replace if `branch_id` already there)."""
    path = _configs_path(lab_root)
    text = _read(path)
    text = _ensure_configs_skeleton(text)
    snap = tree_snapshot(lab_root=lab_root)
    rejected = [r for r in snap.rejected if r.branch_id != branch_id]
    rejected.append(TreeRejected(branch_id=branch_id, reason=reason, evidence=evidence))
    body = _render_rejected_table(rejected)
    new_text = _replace_top_section(text, "Rejected", body)
    _write(path, new_text)
    return new_text


def _render_rejected_table(rejected: list[TreeRejected]) -> str:
    if not rejected:
        return "_(none)_"
    lines = [
        "| ID | Reason | Evidence |",
        "|----|--------|----------|",
    ]
    for r in rejected:
        lines.append(f"| `{r.branch_id}` | {r.reason} | {r.evidence or ''} |")
    return "\n".join(lines)


def _replace_top_section(text: str, heading: str, new_body: str) -> str:
    """Replace the body of a `## <heading>` section in-place."""
    parts = _split_top_sections(text, level=2)
    rebuilt: list[str] = []
    found = False
    for h, b in parts:
        if h is None:
            if b.strip():
                rebuilt.append(b.rstrip())
            continue
        if h == heading:
            rebuilt.append(f"## {h}\n\n{new_body.rstrip()}")
            found = True
        else:
            rebuilt.append(f"## {h}\n\n{b.rstrip()}")
    if not found:
        rebuilt.append(f"## {heading}\n\n{new_body.rstrip()}")
    return "\n\n".join(c for c in rebuilt if c.strip()).rstrip() + "\n"


# ----- roadmap.md: Suggested + promote -------------------------------------


def add_suggested_followup(
    *,
    slug: str,
    hypothesis: str,
    source: str,
    cost: str | None = None,
    lab_root: Path = LAB_ROOT,
) -> str:
    """Append an entry to `## Up next > ### Suggested` (daemon write-zone).

    The Suggested section is a staging area: a human promotes one to
    the main `## Up next` queue with `roadmap promote`. Idempotent:
    re-adding the same slug rewrites the bullets.
    """
    path = _roadmap_path(lab_root)
    text = _read(path)
    parts = _split_top_sections(text, level=2)
    rebuilt: list[str] = []
    found_up_next = False
    for h, b in parts:
        if h is None:
            if b.strip():
                rebuilt.append(b.rstrip())
            continue
        if h == "Up next":
            found_up_next = True
            new_body = _set_or_append_suggested(
                b, slug=slug, hypothesis=hypothesis, source=source, cost=cost,
            )
            rebuilt.append(f"## Up next\n\n{new_body.rstrip()}")
        else:
            rebuilt.append(f"## {h}\n\n{b.rstrip()}")
    if not found_up_next:
        body = _set_or_append_suggested(
            "_(none)_", slug=slug, hypothesis=hypothesis, source=source, cost=cost,
        )
        rebuilt.append(f"## Up next\n\n{body.rstrip()}")
    new_text = "\n\n".join(c for c in rebuilt if c.strip()).rstrip() + "\n"
    _write(path, new_text)
    return new_text


def _set_or_append_suggested(
    up_next_body: str,
    *,
    slug: str,
    hypothesis: str,
    source: str,
    cost: str | None,
) -> str:
    """Inside the body of `## Up next`, set/replace `### Suggested > #### <slug>`."""
    suggested_re = re.compile(
        r"^### Suggested\s*\n(?P<body>.*?)(?=\n### (?!Suggested)|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    bullet = (
        f"#### {slug}\n\n"
        f"-   **Hypothesis:** {hypothesis.strip()}\n"
        f"-   **Source:** {source.strip()}\n"
    )
    if cost:
        bullet += f"-   **Cost:** {cost.strip()}\n"
    m = suggested_re.search(up_next_body)
    if m:
        sug_body = m.group("body").strip()
        # Drop any prior copy of this slug.
        per_slug = re.compile(
            rf"#### {re.escape(slug)}\b.*?(?=\n#### |\Z)", re.DOTALL,
        )
        sug_body = per_slug.sub("", sug_body).strip()
        if sug_body and sug_body != "_(none)_":
            sug_body = sug_body.rstrip() + "\n\n" + bullet.rstrip()
        else:
            sug_body = bullet.rstrip()
        new_block = f"### Suggested\n\n{sug_body.rstrip()}"
        return up_next_body[: m.start()] + new_block + up_next_body[m.end():]
    # No Suggested subsection yet — append one at the bottom of Up next.
    new_block = f"### Suggested\n\n{bullet.rstrip()}"
    base = up_next_body.rstrip()
    if base == "_(none)_":
        base = ""
    return (base + "\n\n" + new_block).strip()


def promote_suggested(*, slug: str, lab_root: Path = LAB_ROOT) -> str:
    """Move a `### Suggested > #### <slug>` entry into the main `## Up next` queue.

    The promoted entry becomes a normal `### <slug>` block. The
    `Source:` bullet is preserved so we never lose attribution.
    """
    path = _roadmap_path(lab_root)
    text = _read(path)
    parts = _split_top_sections(text, level=2)
    rebuilt: list[str] = []
    found = False
    for h, b in parts:
        if h is None:
            if b.strip():
                rebuilt.append(b.rstrip())
            continue
        if h == "Up next":
            new_body, promoted = _promote_suggested_inplace(b, slug)
            if not promoted:
                raise LabDocError(
                    f"No `### Suggested > #### {slug}` entry to promote in roadmap.md."
                )
            found = True
            rebuilt.append(f"## Up next\n\n{new_body.rstrip()}")
        else:
            rebuilt.append(f"## {h}\n\n{b.rstrip()}")
    if not found:
        raise LabDocError("No `## Up next` section in roadmap.md")
    new_text = "\n\n".join(c for c in rebuilt if c.strip()).rstrip() + "\n"
    _write(path, new_text)
    return new_text


def demote_to_suggested(*, slug: str, lab_root: Path = LAB_ROOT) -> str:
    """Move `## Up next > ### <slug>` back into `### Suggested > #### <slug>`.

    The inverse of :func:`promote_suggested`. The original bullets are
    preserved verbatim under the new ``#### <slug>`` header so any
    Hypothesis / Plan / Depends on / Cost / Source rows survive the
    round-trip. If no Suggested subsection exists yet it is created.
    Raises :class:`LabDocError` if the slug isn't currently in
    ``## Up next`` (or only exists inside Suggested already).
    """
    path = _roadmap_path(lab_root)
    text = _read(path)
    parts = _split_top_sections(text, level=2)
    rebuilt: list[str] = []
    found = False
    for h, b in parts:
        if h is None:
            if b.strip():
                rebuilt.append(b.rstrip())
            continue
        if h == "Up next":
            new_body, demoted = _demote_to_suggested_inplace(b, slug)
            if not demoted:
                raise LabDocError(
                    f"No `## Up next > ### {slug}` entry to demote in roadmap.md."
                )
            found = True
            rebuilt.append(f"## Up next\n\n{new_body.rstrip()}")
        else:
            rebuilt.append(f"## {h}\n\n{b.rstrip()}")
    if not found:
        raise LabDocError("No `## Up next` section in roadmap.md")
    new_text = "\n\n".join(c for c in rebuilt if c.strip()).rstrip() + "\n"
    _write(path, new_text)
    return new_text


def _demote_to_suggested_inplace(up_next_body: str, slug: str) -> tuple[str, bool]:
    # Locate the top-level entry (### <slug>) inside Up next, but not the
    # Suggested subsection itself or any of its #### children.
    if slug == "Suggested":
        return up_next_body, False
    pattern = re.compile(
        r"^### " + re.escape(slug) + r"\b(?P<entry>.*?)(?=\n### |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    m = pattern.search(up_next_body)
    if not m:
        return up_next_body, False
    entry_body = m.group("entry").strip()
    suggested_block = f"#### {slug}\n\n{entry_body}".rstrip()

    # Cut the matched ### block out.
    body_without = (up_next_body[: m.start()] + up_next_body[m.end():]).strip()

    # Find or insert the Suggested subsection.
    suggested_re = re.compile(
        r"^### Suggested\s*\n(?P<body>.*?)(?=\n### (?!Suggested)|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    sm = suggested_re.search(body_without)
    if sm:
        sug_body = sm.group("body").strip()
        if sug_body == "_(none)_":
            sug_body = ""
        new_sug_body = (sug_body + "\n\n" + suggested_block).strip() if sug_body else suggested_block
        replaced = f"### Suggested\n\n{new_sug_body}"
        new_up = body_without[: sm.start()].rstrip() + (
            "\n\n" if body_without[: sm.start()].strip() else ""
        ) + replaced + body_without[sm.end():]
    else:
        # No Suggested section yet — append one at the bottom of Up next.
        sep = "\n\n" if body_without.strip() else ""
        new_up = body_without.rstrip() + sep + f"### Suggested\n\n{suggested_block}"
    return new_up.strip(), True


def remove_roadmap_entry(
    *,
    slug: str,
    sections: tuple[str, ...] = ("Up next", "Suggested", "Done"),
    lab_root: Path = LAB_ROOT,
) -> str:
    """Delete a roadmap entry by slug from any of the three sections.

    Scans ``## Up next > ### <slug>``, ``## Up next > ### Suggested >
    #### <slug>``, and ``## Done > ### <slug>`` (constrained by
    ``sections`` if you want to limit the scope). Removes the first
    match. Raises :class:`LabDocError` if the slug isn't found in any
    of the searched sections.
    """
    path = _roadmap_path(lab_root)
    text = _read(path)
    parts = _split_top_sections(text, level=2)
    rebuilt: list[str] = []
    removed = False
    for h, b in parts:
        if h is None:
            if b.strip():
                rebuilt.append(b.rstrip())
            continue
        if h == "Up next" and not removed:
            new_body, did = _remove_from_up_next(b, slug, sections=sections)
            removed = removed or did
            new_body = new_body.strip() or "_(none)_"
            rebuilt.append(f"## Up next\n\n{new_body}")
        elif h == "Done" and "Done" in sections and not removed:
            new_body, did = _strip_level3_block(b, slug)
            removed = removed or did
            new_body = new_body.strip() or "_(none)_"
            rebuilt.append(f"## Done\n\n{new_body}")
        else:
            rebuilt.append(f"## {h}\n\n{b.rstrip()}")
    if not removed:
        raise LabDocError(
            f"No roadmap entry matching slug {slug!r} in sections {sections!r}."
        )
    new_text = "\n\n".join(c for c in rebuilt if c.strip()).rstrip() + "\n"
    _write(path, new_text)
    return new_text


def _strip_level3_block(body: str, slug: str) -> tuple[str, bool]:
    """Remove `### <slug>` and its body from a section's body."""
    pattern = re.compile(
        r"(?:^|\n)### " + re.escape(slug) + r"\b.*?(?=\n### |\Z)", re.DOTALL,
    )
    m = pattern.search(body)
    if not m:
        return body, False
    return (body[: m.start()] + body[m.end():]).strip(), True


def _move_up_next_entry(
    up_next_body: str,
    *,
    slug: str,
    before: str | None,
    after: str | None,
    to_top: bool,
    to_bottom: bool,
) -> tuple[str, bool]:
    suggested_re = re.compile(
        r"^### Suggested\s*\n(?P<body>.*?)(?=\n### (?!Suggested)|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    suggested_match = suggested_re.search(up_next_body)
    suggested_block = ""
    main_body = up_next_body
    if suggested_match:
        suggested_block = suggested_match.group(0).strip()
        main_body = (
            up_next_body[: suggested_match.start()] + up_next_body[suggested_match.end():]
        ).strip()

    pattern = re.compile(
        r"^### (?P<slug>(?!Suggested)\S+)\s*\n.*?(?=^### (?!Suggested)|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    entries = [
        (m.group("slug"), m.group(0).strip())
        for m in pattern.finditer(main_body)
    ]
    if not entries:
        return up_next_body, False
    index_by_slug = {name: i for i, (name, _) in enumerate(entries)}
    if slug not in index_by_slug:
        return up_next_body, False

    moving = entries.pop(index_by_slug[slug])
    if before:
        if before not in {name for name, _ in entries}:
            raise LabDocError(f"Cannot move before unknown slug {before!r}.")
        idx = next(i for i, (name, _) in enumerate(entries) if name == before)
        entries.insert(idx, moving)
    elif after:
        if after not in {name for name, _ in entries}:
            raise LabDocError(f"Cannot move after unknown slug {after!r}.")
        idx = next(i for i, (name, _) in enumerate(entries) if name == after)
        entries.insert(idx + 1, moving)
    elif to_top:
        entries.insert(0, moving)
    elif to_bottom:
        entries.append(moving)

    pieces: list[str] = []
    if entries:
        pieces.append("\n\n".join(block for _, block in entries).strip())
    else:
        pieces.append("_(none)_")
    if suggested_block:
        pieces.append(suggested_block)
    return "\n\n".join(p for p in pieces if p.strip()).strip(), True


def _strip_level4_block(body: str, slug: str) -> tuple[str, bool]:
    """Remove `#### <slug>` and its body from a Suggested section's body."""
    pattern = re.compile(
        r"(?:^|\n)#### " + re.escape(slug) + r"\b.*?(?=\n#### |\Z)", re.DOTALL,
    )
    m = pattern.search(body)
    if not m:
        return body, False
    return (body[: m.start()] + body[m.end():]).strip(), True


def _remove_from_up_next(
    up_next_body: str,
    slug: str,
    *,
    sections: tuple[str, ...],
) -> tuple[str, bool]:
    # Try the Suggested subsection first when caller asked for it; this
    # avoids accidentally matching the Suggested heading via the
    # level-3 stripper (which excludes the literal name "Suggested").
    suggested_re = re.compile(
        r"^### Suggested\s*\n(?P<body>.*?)(?=\n### (?!Suggested)|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    if "Suggested" in sections:
        sm = suggested_re.search(up_next_body)
        if sm:
            sug_body, did = _strip_level4_block(sm.group("body"), slug)
            if did:
                sug_body_clean = sug_body.strip() or "_(none)_"
                replaced = f"### Suggested\n\n{sug_body_clean}"
                return (
                    up_next_body[: sm.start()].rstrip()
                    + ("\n\n" if up_next_body[: sm.start()].strip() else "")
                    + replaced
                    + up_next_body[sm.end():]
                ).strip(), True

    if "Up next" in sections:
        # Match `### <slug>` at the top level (not Suggested).
        if slug == "Suggested":
            return up_next_body, False
        pattern = re.compile(
            r"^### " + re.escape(slug) + r"\b.*?(?=\n### |\Z)",
            re.DOTALL | re.MULTILINE,
        )
        m = pattern.search(up_next_body)
        if m:
            return (up_next_body[: m.start()] + up_next_body[m.end():]).strip(), True

    return up_next_body, False


def _promote_suggested_inplace(up_next_body: str, slug: str) -> tuple[str, bool]:
    suggested_re = re.compile(
        r"^### Suggested\s*\n(?P<body>.*?)(?=\n### (?!Suggested)|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    m = suggested_re.search(up_next_body)
    if not m:
        return up_next_body, False
    sug_body = m.group("body").strip()
    per_slug = re.compile(
        rf"#### {re.escape(slug)}\b(?P<entry>.*?)(?=\n#### |\Z)", re.DOTALL,
    )
    em = per_slug.search(sug_body)
    if not em:
        return up_next_body, False
    body = em.group("entry").strip()
    new_main_block = f"### {slug}\n\n{body}"
    # Strip the suggested entry, then inject the new main block at the
    # top of Up next (above the Suggested subsection).
    sug_body_after = (sug_body[: em.start()] + sug_body[em.end():]).strip()
    if not sug_body_after:
        sug_body_after = "_(none)_"
    suggested_block_new = f"### Suggested\n\n{sug_body_after}"
    new_up = (
        new_main_block.rstrip()
        + "\n\n"
        + up_next_body[: m.start()].rstrip()
        + (
            "\n\n" + suggested_block_new.rstrip()
            if up_next_body[: m.start()].strip()
            else suggested_block_new.rstrip()
        )
        + up_next_body[m.end():]
    )
    return new_up.strip(), True


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
