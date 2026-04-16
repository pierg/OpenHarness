"""Shared run lifecycle and artifact persistence."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openharness.engine.messages import ConversationMessage
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.observability import NullTraceObserver, TraceObserver
from openharness.services.runs import RunArtifacts, create_run_artifacts, save_run_manifest

log = logging.getLogger(__name__)


def _now_utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return path


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _serialize_event(event: StreamEvent) -> dict[str, Any]:
    if isinstance(event, AssistantTextDelta):
        return {"type": "assistant_delta", "text": event.text}
    if isinstance(event, AssistantTurnComplete):
        return {
            "type": "assistant_complete",
            "message": event.message.model_dump(mode="json"),
            "usage": event.usage.model_dump(mode="json"),
        }
    if isinstance(event, ToolExecutionStarted):
        return {
            "type": "tool_started",
            "tool_name": event.tool_name,
            "tool_input": event.tool_input,
        }
    if isinstance(event, ToolExecutionCompleted):
        return {
            "type": "tool_completed",
            "tool_name": event.tool_name,
            "output": event.output,
            "is_error": event.is_error,
        }
    if isinstance(event, ErrorEvent):
        return {
            "type": "error",
            "message": event.message,
            "recoverable": event.recoverable,
        }
    if isinstance(event, StatusEvent):
        return {"type": "status", "message": event.message}
    return {"type": type(event).__name__}


@dataclass
class RunContext:
    """Canonical run identity and artifact layout for one OpenHarness run."""

    interface: str
    cwd: str
    artifacts: RunArtifacts
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_observer: TraceObserver = field(default_factory=NullTraceObserver)
    trace_id: str | None = None
    trace_url: str | None = None
    status: str = "created"
    started_at: str | None = None
    ended_at: str | None = None
    error: str | None = None
    _persisted_message_count: int = 0

    @classmethod
    def create(
        cls,
        cwd: str | Path,
        *,
        interface: str,
        run_id: str | None = None,
        with_logs: bool = False,
        with_workspace: bool = False,
        workspace_dir: str | Path | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunContext:
        resolved_cwd = str(Path(cwd).expanduser().resolve())
        artifacts = create_run_artifacts(
            resolved_cwd,
            run_id=run_id,
            with_logs=with_logs,
            with_workspace=with_workspace,
            workspace_dir=workspace_dir,
        )
        return cls(
            interface=interface,
            cwd=resolved_cwd,
            artifacts=artifacts,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_run_root(
        cls,
        run_root: str | Path,
        *,
        interface: str,
        run_id: str,
        cwd: str | Path,
        metadata: dict[str, Any] | None = None,
    ) -> RunContext:
        run_dir = Path(run_root).expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        artifacts = RunArtifacts(
            run_id=run_id,
            run_dir=run_dir,
            messages_path=run_dir / "messages.jsonl",
            events_path=run_dir / "events.jsonl",
            results_path=run_dir / "results.json",
            metrics_path=run_dir / "metrics.json",
            logs_dir=run_dir / "logs" if (run_dir / "logs").exists() else None,
            workspace_dir=run_dir / "workspace" if (run_dir / "workspace").exists() else None,
        )
        return cls(
            interface=interface,
            cwd=str(Path(cwd).expanduser().resolve()),
            artifacts=artifacts,
            metadata=dict(metadata or {}),
        )

    @property
    def run_id(self) -> str:
        return self.artifacts.run_id

    @property
    def run_dir(self) -> Path:
        return self.artifacts.run_dir

    @property
    def manifest_path(self) -> Path:
        return self.artifacts.metadata_path

    def bind_trace_observer(self, observer: TraceObserver | None) -> TraceObserver:
        if observer is not None:
            self.trace_observer = observer
        return self.trace_observer

    def set_trace_identity(
        self,
        *,
        trace_id: str | None = None,
        trace_url: str | None = None,
    ) -> None:
        if trace_id is not None:
            self.trace_id = trace_id
        if trace_url is not None:
            self.trace_url = trace_url

    def resolved_trace_id(self) -> str | None:
        return getattr(self.trace_observer, "trace_id", None) or self.trace_id

    def resolved_trace_url(self) -> str | None:
        return getattr(self.trace_observer, "trace_url", None) or self.trace_url

    def log_start(self) -> None:
        log.info("Run started: %s", self.run_id)
        log.info("Run dir:     %s", self.run_dir)
        if self.artifacts.workspace_dir is not None:
            log.info("Workspace:   %s", self.artifacts.workspace_dir)
        log.info("Trace URL:   %s", self.resolved_trace_url())

    def as_manifest(self) -> dict[str, Any]:
        import os

        run_dir = self.run_dir.resolve()
        anchor_root = run_dir.parents[4] if len(run_dir.parents) >= 5 else None

        def _rel(path: Path | None) -> str | None:
            if path is None:
                return None
            resolved = path.resolve()
            if resolved == run_dir:
                return "."
            try:
                return resolved.relative_to(run_dir).as_posix()
            except ValueError:
                pass
            # Fall back to a ``..``-prefixed path when the target is still on
            # the same logical tree (same anchor root, e.g. the experiment
            # root). Otherwise keep the absolute path so callers can still
            # resolve it.
            if anchor_root is not None:
                try:
                    resolved.relative_to(anchor_root)
                    return os.path.relpath(resolved, run_dir).replace(os.sep, "/")
                except ValueError:
                    pass
            return str(resolved)

        def _rel_meta_path(value: Any) -> Any:
            if isinstance(value, (str, Path)):
                path = Path(value)
                if path.is_absolute():
                    rel = _rel(path)
                    return rel if rel is not None else str(path)
            return value

        metadata = {key: _rel_meta_path(value) for key, value in self.metadata.items()}

        manifest: dict[str, Any] = {
            "schema_version": 1,
            "run_id": self.run_id,
            "interface": self.interface,
            "cwd": self.cwd,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error,
            "trace_id": self.resolved_trace_id(),
            "trace_url": self.resolved_trace_url(),
            "paths": {
                "anchor": "run_dir",
                "run_dir": ".",
                "manifest": _rel(self.manifest_path),
                "messages": _rel(self.artifacts.messages_path),
                "events": _rel(self.artifacts.events_path),
                "results": _rel(self.artifacts.results_path),
                "metrics": _rel(self.artifacts.metrics_path),
                "logs": _rel(self.artifacts.logs_dir),
                "workspace": _rel(self.artifacts.workspace_dir),
            },
            "metadata": metadata,
        }
        return manifest

    def save_manifest(self) -> Path:
        return save_run_manifest(self.artifacts, self.as_manifest())

    def start(self, *, metadata: dict[str, Any] | None = None) -> Path:
        if self.started_at is None:
            self.started_at = _now_utc()
        self.status = "running"
        if metadata:
            self.metadata.update(metadata)
        return self.save_manifest()

    def finish(
        self,
        *,
        status: str = "completed",
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        results: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> Path:
        self.status = status
        self.error = error
        self.ended_at = _now_utc()
        if metadata:
            self.metadata.update(metadata)
        if results is not None:
            self.write_results(results)
        if metrics is not None:
            self.write_metrics(metrics)
        return self.save_manifest()

    def write_results(self, payload: dict[str, Any]) -> Path:
        return _write_json(self.artifacts.results_path, payload)

    def write_metrics(self, payload: dict[str, Any]) -> Path:
        return _write_json(self.artifacts.metrics_path, payload)

    def append_messages(self, messages: list[ConversationMessage]) -> None:
        new_messages = messages[self._persisted_message_count :]
        for message in new_messages:
            _append_jsonl(
                self.artifacts.messages_path,
                message.model_dump(mode="json"),
            )
        self._persisted_message_count = len(messages)

    def log_event(self, event: StreamEvent) -> None:
        _append_jsonl(self.artifacts.events_path, _serialize_event(event))

    def env(self) -> dict[str, str]:
        return {
            "OPENHARNESS_RUN_ID": self.run_id,
            "OPENHARNESS_RUN_ROOT": str(self.run_dir),
        }

    def build_log_paths(self):
        from openharness.runtime.session import AgentLogPaths

        return AgentLogPaths(
            messages_path=str(self.artifacts.messages_path),
            events_path=str(self.artifacts.events_path),
        )
