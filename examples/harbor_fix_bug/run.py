"""End-to-end Harbor example: agent fixes a bug inside a Docker container.

Flow
----
1. Harbor builds a Docker image from harbor_task/environment/ (python:3.12-slim +
   a buggy sum_evens.py).
2. Harbor starts the container and calls OpenHarnessHarborAgent.run() with the
   instruction from harbor_task/instruction.md.
3. The agent reads sum_evens.py via HarborWorkspace, identifies the bug, and
   writes the fix — all tool calls go through the Workspace protocol into the
   live container.
4. Harbor runs harbor_task/tests/test.sh inside the container to verify the fix.
5. run_harbor_job() returns with the job name and result path.

Prerequisites
-------------
- Docker daemon running
- Harbor CLI:  uv tool install harbor==0.3.0
- API key exported, e.g.:
    export ANTHROPIC_API_KEY=sk-ant-...
  or for Gemini:
    export GOOGLE_API_KEY=...
    export OPENHARNESS_MODEL=gemini-2.0-flash

Usage
-----
  cd examples/harbor_fix_bug
  python run.py
"""

from __future__ import annotations

import json
from pathlib import Path

from openharness.harbor import (
    HarborEnvironmentSpec,
    HarborJobSpec,
    HarborTaskSpec,
    HarborToolSpec,
    OpenHarnessHarborAgentSpec,
    run_harbor_job,
)

HERE = Path(__file__).parent
TASK_DIR = HERE / "harbor_task"
JOBS_DIR = HERE / "harbor_jobs"

# Use the local openharness checkout so Harbor installs it as an editable
# dependency alongside the pinned harbor package.  Remove editable_openharness_dir
# if you are running against an installed release of openharness.
OPENHARNESS_DIR = HERE.parent.parent


def main() -> None:
    print(f"Task:  {TASK_DIR}")
    print(f"Jobs:  {JOBS_DIR}")
    print()

    result = run_harbor_job(
        HarborJobSpec(
            jobs_dir=JOBS_DIR,
            tool=HarborToolSpec(
                version="0.3.0",
                editable_openharness_dir=OPENHARNESS_DIR,
            ),
            agent=OpenHarnessHarborAgentSpec(
                remote_cwd="/app",
                max_turns=6,
                max_tokens=4096,
            ),
            task=HarborTaskSpec(path=TASK_DIR),
            environment=HarborEnvironmentSpec(type="docker"),
        )
    )

    print()
    print(f"Job name:    {result.job_name}")
    print(f"Result path: {result.result_path}")

    if result.result_path.exists():
        data = json.loads(result.result_path.read_text())
        score = data.get("score")
        status = data.get("status")
        print(f"Score:       {score}")
        print(f"Status:      {status}")


if __name__ == "__main__":
    main()
