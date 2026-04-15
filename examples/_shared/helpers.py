import shutil
import subprocess
import sys
from pathlib import Path
from openharness.services.runs import generate_run_id


def prepare_bugfix_workspace(run_dir: Path | None = None) -> Path:
    """Create a workspace with the bugfix task. If run_dir is None, creates a temporary one."""
    if run_dir is None:
        openharness_dir = Path(__file__).resolve().parents[2]
        run_id = generate_run_id()
        run_dir = openharness_dir / "runs" / run_id

    workspace = run_dir / "workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    # Copy sum_evens.py from the shared task folder
    task_source = Path(__file__).resolve().parent / "bugfix_task" / "environment" / "sum_evens.py"
    shutil.copy2(task_source, workspace / "sum_evens.py")
    return workspace


def get_bugfix_instruction(local: bool = False) -> str:
    """Read the bugfix instruction from the shared task folder."""
    instruction_path = Path(__file__).resolve().parent / "bugfix_task" / "instruction.md"
    instruction = instruction_path.read_text(encoding="utf-8")
    if local:
        instruction = instruction.replace("/app/sum_evens.py", "sum_evens.py")
    return instruction


def script_prints_twelve(workspace: Path) -> bool:
    """Return True when the bug-fix script prints the expected value."""
    result = subprocess.run(
        [sys.executable, str(workspace / "sum_evens.py")],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return result.stdout.strip() == "12"
