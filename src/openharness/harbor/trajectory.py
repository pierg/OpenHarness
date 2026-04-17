"""Convert OpenHarness messages.jsonl into Harbor ATIF trajectory.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def messages_to_atif(
    messages_path: Path,
    *,
    session_id: str,
    agent_name: str = "openharness",
    agent_version: str = "0.1.0",
    model_name: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> dict[str, Any]:
    """Read messages.jsonl and produce an ATIF-v1.6 trajectory dict."""
    messages = _read_messages(messages_path)
    steps = _build_steps(messages, model_name=model_name)

    if not steps:
        steps = [{"step_id": 1, "source": "user", "message": "(empty conversation)"}]

    trajectory: dict[str, Any] = {
        "schema_version": "ATIF-v1.6",
        "session_id": session_id,
        "agent": {
            "name": agent_name,
            "version": agent_version,
        },
        "steps": steps,
    }

    if model_name:
        trajectory["agent"]["model_name"] = model_name

    if input_tokens or output_tokens:
        trajectory["final_metrics"] = {
            "total_prompt_tokens": input_tokens,
            "total_completion_tokens": output_tokens,
        }

    return trajectory


def write_atif(
    messages_path: Path,
    output_path: Path,
    **kwargs: Any,
) -> Path:
    """Convert messages.jsonl to ATIF and write to output_path."""
    trajectory = messages_to_atif(messages_path, **kwargs)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(trajectory, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_path


def _read_messages(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    messages = []
    for line in lines:
        if line.strip():
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return messages


def _build_steps(
    messages: list[dict[str, Any]],
    *,
    model_name: str | None = None,
) -> list[dict[str, Any]]:
    """Convert a flat list of role/content messages into ATIF steps.

    Our messages.jsonl uses the Anthropic-style format:
      - role: "user" with content blocks (text or tool_result)
      - role: "assistant" with content blocks (text or tool_use)

    ATIF expects:
      - source: "user" with message text
      - source: "agent" with message text, tool_calls, and observation
    """
    steps: list[dict[str, Any]] = []
    step_id = 1

    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role", "")
        content = msg.get("content", [])

        if role == "user":
            text_parts = _extract_text_parts(content)
            tool_results = _extract_tool_results(content)

            if text_parts:
                steps.append(
                    {
                        "step_id": step_id,
                        "source": "user",
                        "message": "\n\n".join(text_parts),
                    }
                )
                step_id += 1

            if tool_results:
                _attach_observations_to_last_agent_step(steps, tool_results)

        elif role == "assistant":
            text_parts = _extract_text_parts(content)
            tool_calls = _extract_tool_calls(content)
            message_text = "\n\n".join(text_parts) if text_parts else ""

            step: dict[str, Any] = {
                "step_id": step_id,
                "source": "agent",
                "message": message_text or "(tool call)",
            }

            if tool_calls:
                step["tool_calls"] = tool_calls

            steps.append(step)
            step_id += 1

        i += 1

    _renumber_steps(steps)
    return steps


def _extract_text_parts(content: list[dict[str, Any]] | str) -> list[str]:
    if isinstance(content, str):
        return [content] if content.strip() else []
    parts = []
    for block in content:
        if block.get("type") == "text":
            text = block.get("text", "").strip()
            if text:
                parts.append(text)
    return parts


def _extract_tool_calls(content: list[dict[str, Any]] | str) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return []
    calls = []
    for block in content:
        if block.get("type") == "tool_use":
            calls.append(
                {
                    "tool_call_id": block.get("id", ""),
                    "function_name": block.get("name", ""),
                    "arguments": block.get("input", {}),
                }
            )
    return calls


def _extract_tool_results(content: list[dict[str, Any]] | str) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return []
    results = []
    for block in content:
        if block.get("type") == "tool_result":
            result_content = block.get("content", "")
            if isinstance(result_content, list):
                text_parts = _extract_text_parts(result_content)
                result_content = "\n".join(text_parts)
            results.append(
                {
                    "source_call_id": block.get("tool_use_id", ""),
                    "content": str(result_content),
                }
            )
    return results


def _attach_observations_to_last_agent_step(
    steps: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> None:
    for step in reversed(steps):
        if step.get("source") == "agent" and step.get("tool_calls"):
            step["observation"] = {"results": tool_results}
            return


def _renumber_steps(steps: list[dict[str, Any]]) -> None:
    for i, step in enumerate(steps):
        step["step_id"] = i + 1
