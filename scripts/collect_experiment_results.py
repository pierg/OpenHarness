"""Collect and summarize results from an OpenHarness experiment manifest.

Example:
    uv run python scripts/collect_experiment_results.py runs/experiments/tb2-baseline-*/experiment.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openharness.experiments.results import (
    collect_experiment_results,
    summarize_experiment_results,
    write_results_csv,
    write_results_json,
    write_summary_markdown,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Experiment manifest JSON.")
    parser.add_argument("--out-dir", type=Path, help="Output directory. Defaults to manifest dir.")
    args = parser.parse_args()

    out_dir = args.out_dir or args.manifest.parent
    rows = collect_experiment_results(args.manifest)
    summary = summarize_experiment_results(rows)

    write_results_json(rows, out_dir / "results.json")
    write_results_csv(rows, out_dir / "results.csv")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_summary_markdown(summary, out_dir / "summary.md")

    print(f"Rows:       {len(rows)}")
    print(f"Results:    {out_dir / 'results.json'}")
    print(f"CSV:        {out_dir / 'results.csv'}")
    print(f"Summary:    {out_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
