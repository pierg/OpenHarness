# INSTRUCTIONS FOR NEXT AGENT

The user just shut down and upgraded this VM's hardware (CPU, RAM, Disk) to run the OpenHarness Terminal-Bench full sweep at maximum parallelism.

**Immediate Actions Required (DO THIS FIRST):**

1. Run `nproc` and `free -h` to determine the new hardware capacity of the VM.
2. Edit `experiments/tb2-baseline.yaml` and update the `n_concurrent` value (under `defaults`) to safely utilize the new capacity (e.g., if there are 32 vCPUs, you can safely set `n_concurrent: 16` to `30` depending on memory/disk IO limits).
3. Ensure no old Docker containers are lingering from previous crashes (`docker ps -a` / `docker container prune`).
4. Kick off the full baseline sweep: `scripts/exp/start.sh exec tb2-baseline`.
5. Update `lab/experiments.md` to link to the new run directory.
6. Delete this `AGENT_HANDOFF.md` file once the experiment is successfully running.
