"""Tests for the structured-output parsing helpers in `runtime.session`."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from openharness.runtime.session import _parse_structured_output, _sanitize_json_escapes


class _Thought(BaseModel):
    reasoning: str
    action: str = ""
    is_finished: bool = False


def test_parse_clean_json() -> None:
    text = '{"reasoning": "ok", "action": "ls", "is_finished": false}'
    parsed = _parse_structured_output(text, _Thought)
    assert parsed.reasoning == "ok"
    assert parsed.action == "ls"
    assert parsed.is_finished is False


def test_parse_strips_markdown_fence() -> None:
    text = '```json\n{"reasoning": "fenced"}\n```'
    parsed = _parse_structured_output(text, _Thought)
    assert parsed.reasoning == "fenced"


def test_parse_recovers_from_invalid_backslash_escape() -> None:
    # Real-world Gemini failure: the model writes a regex like \d directly
    # into a string value, producing an invalid JSON escape sequence.
    text = (
        '{"reasoning": "match dates with \\d{4}-\\d{2}-\\d{2}",'
        ' "action": "grep -E \\d+ file.txt", "is_finished": false}'
    )
    parsed = _parse_structured_output(text, _Thought)
    assert "\\d{4}" in parsed.reasoning
    assert parsed.action.startswith("grep")
    assert parsed.is_finished is False


def test_parse_preserves_valid_escape_sequences() -> None:
    text = '{"reasoning": "line1\\nline2 quoted \\"x\\" tab\\there", "is_finished": true}'
    parsed = _parse_structured_output(text, _Thought)
    assert "line1\nline2" in parsed.reasoning
    assert '"x"' in parsed.reasoning
    assert "\there" in parsed.reasoning
    assert parsed.is_finished is True


def test_parse_raises_for_truly_malformed_json() -> None:
    text = '{"reasoning": "missing end quote, '
    with pytest.raises(ValidationError):
        _parse_structured_output(text, _Thought)


def test_sanitize_leaves_already_valid_json_untouched() -> None:
    text = '{"a": "b\\n", "c": "d\\u00e9"}'
    assert _sanitize_json_escapes(text) == text


def test_sanitize_does_not_touch_backslashes_outside_strings() -> None:
    # Backslashes between tokens (rare in real JSON) are left alone since
    # the function only mutates content inside strings.
    text = '{"a": "x"} \\ trailing'
    assert _sanitize_json_escapes(text) == text


def test_sanitize_doubles_invalid_path_escapes() -> None:
    # Windows-style paths often produce invalid JSON escapes such as \U
    # or \D. Note that \b is a *valid* JSON escape (backspace) so it
    # would be left untouched, hence the deliberately chosen segments.
    text = '{"path": "C:\\Users\\Docs"}'
    sanitized = _sanitize_json_escapes(text)
    assert sanitized == '{"path": "C:\\\\Users\\\\Docs"}'
