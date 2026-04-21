"""Markdown rendering for the lab web UI.

Wraps `markdown-it` with a post-processor that rewrites lab-relative
links (e.g. ``[loop-guard](ideas.md#loop-guard)``,
``[run](../runs/experiments/<id>)``) into web-routable URLs. The lab
markdown files are written assuming a filesystem viewer (cursor, GitHub
preview, etc.), so without rewriting almost every link in a journal /
critic body resolves to a 404 in the browser.

Rewrite policy (conservative — only known patterns):

- ``experiments.md[#anchor]``    → ``/experiments[#anchor]``
- ``roadmap.md[#slug]``          → ``/roadmap[#slug]``
- ``ideas.md[#slug]``            → ``/ideas[#slug]``
- ``components.md[#id]``         → ``/components[#id]``
- ``configs.md[#anchor]``        → ``/tree[#anchor]``
- ``../runs/experiments/<id>(/…)``  → ``/experiments/<id>``
- ``runs/experiments/<id>(/…)``     → ``/experiments/<id>``

Anything else is left untouched (absolute URLs, fragment-only links,
``../src/…`` source references, agent skill paths, …). This means an
unknown relative link will still 404 if clicked, but at least the well-
known cross-page references and run-dir links work.

Anchors:

- ``/ideas`` and ``/roadmap`` emit ``id="<slug>"`` on each entry so
  cross-page anchors land on the right item.
- ``/experiments`` does not yet emit per-entry anchors, so the anchor
  in ``experiments.md#<date>--<slug>`` is preserved but won't scroll
  to anything. The journal entry itself still resolves to the
  ``/experiments/{instance_id}`` page via the ``run_link`` parsing in
  ``data.py`` — clicking the slug in the index list goes there.
"""

from __future__ import annotations

import re
from functools import lru_cache

from markdown_it import MarkdownIt

__all__ = ["render", "rewrite_href"]


_PAGE_MAP: dict[str, str] = {
    "experiments.md": "/experiments",
    "roadmap.md": "/roadmap",
    "ideas.md": "/ideas",
    "components.md": "/components",
    "configs.md": "/tree",
}

_RUN_DIR_RE = re.compile(
    r"^(?:\.\./)?runs/experiments/([A-Za-z0-9._-]+)(?:/.*)?$"
)
_LAB_MD_RE = re.compile(r"^([A-Za-z_-]+\.md)(?:#(.+))?$")
_HREF_RE = re.compile(r'href="([^"]*)"')


def rewrite_href(href: str) -> str:
    """Return the web-routable equivalent of a lab-relative href.

    Absolute, anchor-only, mailto/tel and non-matching relative hrefs
    are returned unchanged.
    """

    if not href:
        return href
    if href.startswith(("http://", "https://", "/", "#", "mailto:", "tel:")):
        return href
    h = href.removeprefix("./")

    m = _RUN_DIR_RE.match(h)
    if m:
        return f"/experiments/{m.group(1)}"

    m = _LAB_MD_RE.match(h)
    if m:
        page, anchor = m.group(1), m.group(2)
        target = _PAGE_MAP.get(page)
        if target is None:
            # README.md, OPERATIONS.md, unknown lab docs — leave the
            # original href so the failure mode is identical to today
            # (404 on click) rather than silently inventing a URL.
            return href
        if anchor:
            return f"{target}#{anchor}"
        return target

    return href


@lru_cache(maxsize=1)
def _md() -> MarkdownIt:
    return MarkdownIt(
        "commonmark",
        {"html": False, "linkify": True, "typographer": True},
    ).enable("table")


def _rewrite_html(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        new = rewrite_href(match.group(1))
        return f'href="{new}"'

    return _HREF_RE.sub(repl, html)


def render(text: str | None) -> str:
    """Render `text` as HTML and rewrite known lab-relative links."""

    if not text:
        return ""
    return _rewrite_html(_md().render(text))
