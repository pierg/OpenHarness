import json
import logging
from pathlib import Path
from dataclasses import dataclass, field

from openharness.harbor import (
    HarborEnvironmentSpec,
    HarborExistingJobPolicy,
    HarborJobSpec,
    HarborTaskSpec,
    HarborToolSpec,
    OpenHarnessHarborAgentSpec,
)
from openharness.harbor.runner import is_docker_available
from openharness.runs import HarborAgentRunSpec, run_harbor_agent, HarborJobResult
from openharness.observability.logging import setup_logging

log = logging.getLogger(__name__)


@dataclass
class HarborExperiment:
    agent_config: str | Path
    task: str | Path | None = None
    dataset: str | None = None
    model: str = "gemini-2.5-flash"
    max_turns: int = 10
    n_concurrent: int = 1
    n_tasks: int | None = None
    include_tasks: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def run(self) -> HarborJobResult | None:
        setup_logging()

        if not is_docker_available():
            log.info("Docker daemon is not running. Start Docker to run Harbor experiments.")
            return None

        config_path = Path(self.agent_config)

        agent_config_yaml = (
            config_path.read_text(encoding="utf-8")
            if config_path.exists()
            else str(self.agent_config)
        )
        agent_name = config_path.stem if config_path.exists() else "harbor_agent"

        task_spec = HarborTaskSpec()
        if self.dataset is not None:
            task_spec = HarborTaskSpec(
                dataset=self.dataset,
                include_task_names=tuple(self.include_tasks),
                n_tasks=self.n_tasks,
            )
            task_name = self.dataset
        elif self.task is not None:
            is_local_path = isinstance(self.task, Path) or (
                isinstance(self.task, str) and Path(self.task).exists()
            )
            if is_local_path:
                task_spec = HarborTaskSpec(
                    path=Path(self.task).expanduser().resolve(),
                    include_task_names=tuple(self.include_tasks),
                    n_tasks=self.n_tasks,
                )
            else:
                task_spec = HarborTaskSpec(registry_task=str(self.task))
            task_name = str(self.task)
        else:
            raise ValueError("Either task or dataset must be provided.")

        openharness_dir = Path(__file__).resolve().parents[3]

        job_spec = HarborJobSpec(
            existing_job_policy=HarborExistingJobPolicy.ERROR,
            tool=HarborToolSpec(editable_openharness_dir=openharness_dir),
            agent=OpenHarnessHarborAgentSpec(
                agent_name=agent_name,
                model=self.model,
                remote_cwd="/app",
                max_turns=self.max_turns,
                max_tokens=8192,
                agent_config_yaml=agent_config_yaml,
                env=self.env,
            ),
            task=task_spec,
            environment=HarborEnvironmentSpec(type="docker"),
            n_concurrent_trials=self.n_concurrent,
            metadata={"task_or_dataset": task_name},
        )

        from openharness.services.runs import generate_run_id
        from openharness.config.paths import get_project_runs_dir

        job_id = generate_run_id()
        job_dir = get_project_runs_dir(openharness_dir) / job_id

        log.info("")
        log.info("Job ID:    %s", job_id)
        log.info("Job dir:   %s", job_dir)
        log.info("Model:     %s", self.model)
        log.info("Agent:     %s", config_path.name)
        if self.dataset is not None:
            log.info("Dataset:   %s", self.dataset)
        elif self.task is not None:
            log.info("Task:      %s", self.task)
        if self.include_tasks:
            log.info("Filter:    %s", ", ".join(self.include_tasks))
        log.info("Concur:    %d", self.n_concurrent)
        log.info("")

        spec = HarborAgentRunSpec(
            cwd=openharness_dir,
            job=job_spec,
            run_id=job_id,
        )

        result = run_harbor_agent(spec)
        if result is not None:
            self._save_summary(result)
        return result

    def _save_summary(self, result: HarborJobResult) -> None:
        """Write an enriched summary.json alongside Harbor's result.json."""
        total_input = sum(t.input_tokens or 0 for t in result.trials)
        total_output = sum(t.output_tokens or 0 for t in result.trials)
        total_cost = sum(t.cost_usd or 0.0 for t in result.trials)

        summary = {
            "job_id": result.job_id,
            "dataset": self.dataset,
            "task": str(self.task) if self.task else None,
            "model": self.model,
            "agent_config": Path(self.agent_config).name,
            "n_trials": len(result.trials),
            "n_passed": result.n_passed,
            "n_errors": result.n_errors,
            "mean_score": result.mean_score,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_cost_usd": total_cost if total_cost > 0 else None,
            "trials": [
                {
                    "trial_id": t.trial_id,
                    "task_name": t.task_name,
                    "score": t.score,
                    "passed": t.passed,
                    "model": t.model,
                    "input_tokens": t.input_tokens,
                    "output_tokens": t.output_tokens,
                    "total_tokens": t.total_tokens,
                    "cost_usd": t.cost_usd,
                    "duration_sec": t.duration_sec,
                    "agent_duration_sec": t.agent_duration_sec,
                    "env_setup_duration_sec": t.env_setup_duration_sec,
                    "verifier_duration_sec": t.verifier_duration_sec,
                    "trace_id": t.trace_id,
                    "trace_url": t.trace_url,
                    "error": t.error,
                    "trial_dir": str(t.trial_dir),
                }
                for t in result.trials
            ],
        }

        summary_path = result.harbor_result_path.parent / "summary.json"
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log.debug("Summary written to %s", summary_path)

    def log_summary(self, result: HarborJobResult | None) -> None:
        if result is None:
            return

        mean_score = result.mean_score
        n_trials = len(result.trials)
        total_input = sum(t.input_tokens or 0 for t in result.trials)
        total_output = sum(t.output_tokens or 0 for t in result.trials)
        total_cost = sum(t.cost_usd or 0.0 for t in result.trials)

        log.info("")
        log.info("=" * 80)
        log.info("Job ID:    %s", result.job_id)
        if self.dataset is not None:
            log.info("Dataset:   %s", self.dataset)
        elif self.task is not None:
            log.info("Task:      %s", self.task)
        log.info("Model:     %s", self.model)
        log.info("Agent:     %s", Path(self.agent_config).name)
        log.info("Trials:    %d", n_trials)
        log.info("Passed:    %d / %d", result.n_passed, n_trials)
        if mean_score is not None:
            log.info("Mean:      %.3f", mean_score)
        if total_input or total_output:
            log.info("Tokens:    %s in / %s out", f"{total_input:,}", f"{total_output:,}")
        if total_cost > 0:
            log.info("Cost:      $%.4f", total_cost)
        log.info("Results:   %s", result.harbor_result_path)
        log.info("=" * 80)

        if result.trials:
            log.info("")
            header = f"{'Task':<30} {'Score':>6} {'Status':<6} {'Tokens':>10} {'Cost':>8} {'Agent':>7} {'Total':>7}"
            log.info(header)
            log.info("-" * len(header))
            for trial in result.trials:
                score_str = f"{trial.score:.1f}" if trial.score is not None else "n/a"
                status = "PASS" if trial.passed else ("ERR" if trial.error else "FAIL")
                tokens = f"{(trial.total_tokens or 0):,}" if trial.total_tokens else "n/a"
                cost = f"${trial.cost_usd:.4f}" if trial.cost_usd else "n/a"
                agent_t = f"{trial.agent_duration_sec:.0f}s" if trial.agent_duration_sec else "n/a"
                total_t = f"{trial.duration_sec:.0f}s" if trial.duration_sec else "n/a"
                log.info(
                    f"{trial.task_name:<30} {score_str:>6} {status:<6} {tokens:>10} {cost:>8} {agent_t:>7} {total_t:>7}"
                )

            log.info("")
            for trial in result.trials:
                trace = trial.trace_url or "n/a"
                log.info("  %s  ->  %s", trial.trial_id, trace)
