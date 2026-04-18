#!/usr/bin/env python3
"""Validate every `components/<id>.yaml` against the contract schema.

Used as the pre-commit hook and the CI gate. Prints one line per
component plus a final summary, then exits non-zero on any failure.

Usage:

    uv run python scripts/validate_contracts.py
    uv run python scripts/validate_contracts.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENTS_DIR = REPO_ROOT / "components"
SCHEMA_PATH = REPO_ROOT / "schemas" / "component_contract.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _iter_component_files(targets: list[Path]) -> list[Path]:
    if targets:
        return [p.resolve() for p in targets if p.suffix in {".yaml", ".yml"}]
    return sorted(COMPONENTS_DIR.glob("*.yaml"))


def _validate_one(path: Path, validator: Draft202012Validator) -> list[str]:
    """Return list of human-readable error strings for `path`. Empty on success."""
    errors: list[str] = []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{path}: invalid YAML — {exc}"]
    if not isinstance(raw, dict):
        return [f"{path}: top-level must be a mapping"]
    for err in sorted(validator.iter_errors(raw), key=lambda e: list(e.absolute_path)):
        loc = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path.name}::{loc}: {err.message}")
    if (cid := raw.get("id")) and cid != path.stem:
        errors.append(
            f"{path.name}: id {cid!r} must match filename stem {path.stem!r}"
        )
    return errors


def _check_conflict_symmetry(specs: dict[str, dict]) -> list[str]:
    issues: list[str] = []
    for cid, raw in specs.items():
        for other in raw.get("conflicts_with") or []:
            if other not in specs:
                issues.append(f"{cid}: conflicts_with unknown component {other!r}")
                continue
            their = specs[other].get("conflicts_with") or []
            if cid not in their:
                issues.append(
                    f"asymmetric conflict: {cid!r} ↔ {other!r} "
                    f"(declared on {cid}, missing on {other})"
                )
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*", type=Path,
                        help="Optional list of component YAML paths "
                             "(pre-commit passes the changed files).")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON instead of human-readable lines.")
    args = parser.parse_args(argv)

    if not COMPONENTS_DIR.is_dir():
        print(f"OK: no {COMPONENTS_DIR.relative_to(REPO_ROOT)} directory yet.")
        return 0
    if not SCHEMA_PATH.is_file():
        print(f"ERROR: schema missing at {SCHEMA_PATH}", file=sys.stderr)
        return 2

    schema = _load_schema()
    validator = Draft202012Validator(schema)

    files = _iter_component_files(args.targets)
    per_file: dict[str, list[str]] = {}
    parsed: dict[str, dict] = {}

    for path in files:
        errors = _validate_one(path, validator)
        try:
            display = str(path.relative_to(REPO_ROOT))
        except ValueError:
            display = str(path)
        per_file[display] = errors
        if not errors:
            parsed[path.stem] = yaml.safe_load(path.read_text(encoding="utf-8"))

    cross = _check_conflict_symmetry(parsed) if not args.targets else []

    failed = sum(1 for e in per_file.values() if e) + (1 if cross else 0)

    if args.json:
        print(json.dumps(
            {"per_file": per_file, "cross_file": cross, "failed": failed},
            indent=2,
        ))
    else:
        for fname, errs in per_file.items():
            if errs:
                print(f"FAIL {fname}")
                for e in errs:
                    print(f"  - {e}")
            else:
                print(f"ok   {fname}")
        if cross:
            print("FAIL <cross-file>")
            for e in cross:
                print(f"  - {e}")
        print(f"--- {len(files)} file(s), {failed} failure(s) ---")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
