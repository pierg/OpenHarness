"""Subprocess entry point for swarm teammates."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from openharness.swarm.runner import create_teammate_runner
from openharness.swarm.types import TeammateSpawnConfig


def _parse_message(raw: str) -> str:
    stripped = raw.strip()
    if not stripped:
        return ""
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        return payload["text"].strip()
    return stripped


async def _run(config_path: Path) -> int:
    config = TeammateSpawnConfig(**json.loads(config_path.read_text(encoding="utf-8")))
    runner = await create_teammate_runner(config)
    try:
        received_any = False
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if line == "":
                break
            message = _parse_message(line)
            if not message:
                continue
            received_any = True
            result = await runner.run_turn(message)
            if result.text:
                print(result.text, flush=True)
        if not received_any and config.prompt.strip():
            result = await runner.run_turn(config.prompt)
            if result.text:
                print(result.text, flush=True)
        return 0
    finally:
        await runner.close()


def main() -> int:
    """Run the worker subprocess."""
    parser = argparse.ArgumentParser(description="Run an OpenHarness swarm teammate")
    parser.add_argument("--config", required=True, help="Path to a teammate JSON config file")
    args = parser.parse_args()
    return asyncio.run(_run(Path(args.config)))


if __name__ == "__main__":
    raise SystemExit(main())
