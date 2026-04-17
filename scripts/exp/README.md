# scripts/exp/ — background job manager

A simple background job manager for OpenHarness experiments. It uses `tmux` under the hood to ensure long-running experiments survive SSH disconnects and laptop sleep, while perfectly preserving Harbor's rich TUI progress bars so you can attach and check progress interactively.

All scripts are macOS / Linux friendly.

## TL;DR

```bash
# start any OpenHarness command in the background
scripts/exp/start.sh exec tb2-baseline

# see what's running
scripts/exp/list.sh

# view live progress (Ctrl-b d to detach again)
scripts/exp/attach.sh

# stop a job
scripts/exp/stop.sh <job_id>
```

## Scripts

| Script        | Purpose                                                                |
|---------------|------------------------------------------------------------------------|
| `start.sh`    | Pass any `uv run` args. Starts it detached in the background.          |
| `list.sh`     | List all active jobs.                                                  |
| `attach.sh`   | Attach to view live TUI. Auto-selects if only 1 job is active.         |
| `stop.sh`     | Kill a specific background job.                                        |
| `status.sh`   | Print a per-leg summary of OpenHarness run artifacts.                  |
| `_lib.sh`     | Internal helpers: `resolve_run`, `find_latest_run`, env loader.        |

## Workflows

### 1. Launching runs
Anything you would normally type after `uv run ...`, just put it after `start.sh`:

```bash
scripts/exp/start.sh exec tb2-baseline
scripts/exp/start.sh exec tb2-baseline --profile smoke
scripts/exp/start.sh rerun tb2-baseline-smoke-20260416-205703
scripts/exp/start.sh rerun tb2-baseline-smoke-20260416-205703 -l react
```

### 2. Inspecting runs
OpenHarness writes state to disk immediately. You don't need to attach to see what happened:

```bash
# View summary of the latest tb2-baseline run
scripts/exp/status.sh

# View summary of a specific run
scripts/exp/status.sh tb2-baseline-smoke-20260416-205703
```

### 3. Safety

`start.sh` will exit early if you forgot to set a model provider key (like `GEMINI_API_KEY`) in your `.env` or environment, saving you from a round-trip failure.

If your remote machine completely reboots, `tmux` dies, but OpenHarness's granular state (`events.jsonl`, `messages.jsonl`, `result.json`) remains safely on disk. You can just check `status.sh` to see what finished, and `start.sh rerun <instance_id>` to pick up exactly where it left off.
