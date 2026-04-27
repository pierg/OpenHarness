"""Unit tests for :mod:`openharness.lab.web.markdown`.

Covers the rewrite policy (lab-relative cross-file refs and the
``runs/experiments/<id>`` shortcut) plus a few sanity checks: full
markdown render including a code fence and a link, and that absolute
URLs / fragment-only / mailto links are passed through unchanged.

The lab markdown files under ``lab/`` are written for a filesystem
viewer (cursor / GitHub preview / mkdocs); the web UI shows the same
content rendered to HTML, so without rewriting almost every link in a
journal or critic body would 404 in the browser. These tests pin the
mapping that prevents that.
"""

from __future__ import annotations

import pytest

from openharness.lab.web.markdown import render, rewrite_href


# ---------------------------------------------------------------------------
# rewrite_href: lab-relative cross-file refs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "href, expected",
    [
        # Empty / passthrough.
        ("", ""),
        ("https://example.com/x", "https://example.com/x"),
        ("http://example.com", "http://example.com"),
        ("/already-absolute", "/already-absolute"),
        ("#anchor-only", "#anchor-only"),
        ("mailto:lab@example.com", "mailto:lab@example.com"),
        ("tel:+1234567890", "tel:+1234567890"),
        # Cross-file refs to lab pages we render in the UI.
        ("ideas.md", "/ideas"),
        ("ideas.md#loop-guard", "/ideas#loop-guard"),
        ("roadmap.md", "/roadmap"),
        ("roadmap.md#tb2-baseline-full-sweep", "/roadmap#tb2-baseline-full-sweep"),
        ("experiments.md", "/experiments"),
        (
            "experiments.md#2026-04-17--tb2-baseline-full-sweep",
            "/experiments#2026-04-17--tb2-baseline-full-sweep",
        ),
        ("components.md", "/components"),
        ("components.md#loop-guard", "/components#loop-guard"),
        ("configs.md", "/tree"),
        ("configs.md#current-best", "/tree#current-best"),
        # ``./`` prefix is stripped before matching.
        ("./ideas.md#loop-guard", "/ideas#loop-guard"),
        # Run-dir shortcut (with and without ``../`` prefix, and
        # tolerating a deeper path that is collapsed onto the experiment
        # detail page).
        (
            "../runs/experiments/tb2-baseline-20260417-234913",
            "/experiments/tb2-baseline-20260417-234913",
        ),
        (
            "runs/experiments/tb2-baseline-20260417-234913",
            "/experiments/tb2-baseline-20260417-234913",
        ),
        (
            "../runs/experiments/tb2-baseline-20260417-234913/critic/critic_summary.md",
            "/experiments/tb2-baseline-20260417-234913",
        ),
        # Unknown lab markdown files (README, OPERATIONS, etc.) are left
        # alone — we deliberately don't invent a URL for them.
        ("README.md", "README.md"),
        ("OPERATIONS.md", "OPERATIONS.md"),
        # Source files / agent skills are likewise untouched (no web
        # route exists for them; an external GitHub link is out of scope
        # for this slice).
        (
            "../src/openharness/agents/configs/basic.yaml",
            "../src/openharness/agents/configs/basic.yaml",
        ),
    ],
)
def test_rewrite_href(href: str, expected: str) -> None:
    assert rewrite_href(href) == expected


# ---------------------------------------------------------------------------
# render: end-to-end markdown -> HTML, with rewrite applied
# ---------------------------------------------------------------------------


def test_render_empty() -> None:
    assert render(None) == ""
    assert render("") == ""


def test_render_passes_through_html_features() -> None:
    out = render("This is **bold** and *italic*.")
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out


def test_render_rewrites_inline_link() -> None:
    out = render("See [loop-guard](ideas.md#loop-guard) for context.")
    assert 'href="/ideas#loop-guard"' in out
    # Original ``ideas.md`` substring should be gone from the href; it
    # may still appear in link text but never in href=.
    assert 'href="ideas.md' not in out


def test_render_rewrites_run_dir_link() -> None:
    md = (
        "Run: [`runs/experiments/tb2-baseline-20260417-234913`]"
        "(../runs/experiments/tb2-baseline-20260417-234913)"
    )
    out = render(md)
    assert 'href="/experiments/tb2-baseline-20260417-234913"' in out


def test_render_leaves_external_links_alone() -> None:
    out = render("See [docs](https://example.com/x).")
    assert 'href="https://example.com/x"' in out


def test_render_leaves_unknown_relative_link_alone() -> None:
    # README.md etc. — we don't pretend to route these. Left untouched
    # so the failure mode (404) matches today's behaviour.
    out = render("See [README](README.md).")
    assert 'href="README.md"' in out


def test_render_table_extension_enabled() -> None:
    md = (
        "| col1 | col2 |\n"
        "| --- | --- |\n"
        "| a | b |\n"
    )
    out = render(md)
    assert "<table" in out
    assert "<th>col1</th>" in out
    assert "<td>a</td>" in out
