"""TraceObserver protocol and its Langfuse-backed implementation.

The module exposes two concrete classes:

- ``NullTraceObserver`` — disabled backend; all methods are no-ops.
- ``LangfuseTraceObserver`` — sends structured observations to a Langfuse
  project.  The ``langfuse`` package is imported lazily inside
  ``create_trace_observer`` so it remains an optional dependency.

Use ``create_trace_observer`` to get the right observer from environment
variables without any caller-side branching.
"""

from __future__ import annotations

import getpass
import logging
import os
from contextlib import AbstractContextManager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel

log = logging.getLogger(__name__)


class ObservationHandle(Protocol):
    """Minimal mutable handle for an active trace observation."""

    trace_id: str | None

    def update(self, **kwargs: Any) -> None:
        """Update the current observation."""

    def close(self) -> None:
        """Close the current observation."""


class ObservationScope(AbstractContextManager[ObservationHandle]):
    """Context manager that auto-closes an observation and records exceptions."""

    def __init__(self, handle: ObservationHandle) -> None:
        self._handle = handle

    def __enter__(self) -> ObservationHandle:
        return self._handle

    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> bool:
        del exc_type, tb
        if exc is not None:
            self._handle.update(metadata={"error": str(exc)})
        self._handle.close()
        return False


class TraceObserver(Protocol):
    """Protocol implemented by active and no-op tracing backends."""

    enabled: bool
    trace_id: str | None

    def start_session(self, *, metadata: dict[str, Any] | None = None) -> None:
        """Start the session-level observation."""

    def end_session(self, *, output: Any | None = None, metadata: dict[str, Any] | None = None) -> None:
        """End the session-level observation."""

    def start_model_call(
        self,
        *,
        model: str,
        input: Any,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, str | int | float | bool | list[str] | None] | None = None,
    ) -> ObservationHandle:
        """Start a model-generation observation."""

    def start_tool_call(
        self, *, tool_name: str, tool_input: Any, metadata: dict[str, Any] | None = None
    ) -> ObservationHandle:
        """Start a tool observation."""

    def start_span(
        self,
        *,
        name: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObservationHandle:
        """Start a generic nested observation for higher-level flow stages."""

    def model_call(
        self,
        *,
        model: str,
        input: Any,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, str | int | float | bool | list[str] | None] | None = None,
    ) -> ObservationScope:
        """Return a scope-managed model-generation observation."""

    def tool_call(
        self, *, tool_name: str, tool_input: Any, metadata: dict[str, Any] | None = None
    ) -> ObservationScope:
        """Return a scope-managed tool observation."""

    def span(
        self,
        *,
        name: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObservationScope:
        """Return a scope-managed generic nested observation."""

    def flush(self) -> None:
        """Flush buffered traces."""


# ---------------------------------------------------------------------------
# No-op backend
# ---------------------------------------------------------------------------


class NullObservationHandle:
    """No-op handle returned when tracing is disabled."""

    trace_id: str | None = None

    def update(self, **kwargs: Any) -> None:
        pass

    def close(self) -> None:
        pass


class NullTraceObserver:
    """Disabled tracing backend — all methods are no-ops."""

    enabled = False
    trace_id: str | None = None

    def start_session(self, *, metadata: dict[str, Any] | None = None) -> None:
        pass

    def end_session(self, *, output: Any | None = None, metadata: dict[str, Any] | None = None) -> None:
        pass

    def start_model_call(
        self,
        *,
        model: str,
        input: Any,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, str | int | float | bool | list[str] | None] | None = None,
    ) -> ObservationHandle:
        return NullObservationHandle()

    def start_tool_call(
        self, *, tool_name: str, tool_input: Any, metadata: dict[str, Any] | None = None
    ) -> ObservationHandle:
        return NullObservationHandle()

    def start_span(
        self,
        *,
        name: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObservationHandle:
        del name, input, metadata
        return NullObservationHandle()

    def model_call(
        self,
        *,
        model: str,
        input: Any,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, str | int | float | bool | list[str] | None] | None = None,
    ) -> ObservationScope:
        return ObservationScope(
            self.start_model_call(
                model=model,
                input=input,
                metadata=metadata,
                model_parameters=model_parameters,
            )
        )

    def tool_call(
        self, *, tool_name: str, tool_input: Any, metadata: dict[str, Any] | None = None
    ) -> ObservationScope:
        return ObservationScope(
            self.start_tool_call(tool_name=tool_name, tool_input=tool_input, metadata=metadata)
        )

    def span(
        self,
        *,
        name: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObservationScope:
        return ObservationScope(self.start_span(name=name, input=input, metadata=metadata))

    def flush(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Langfuse backend
# ---------------------------------------------------------------------------


def _env_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _coerce_jsonable(value: Any) -> Any:
    """Recursively coerce a value to a JSON-serialisable type."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): _coerce_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_jsonable(item) for item in value]
    return str(value)


class _LangfuseObservationHandle:
    """Thin wrapper over a Langfuse observation context manager."""

    def __init__(
        self,
        context_manager: Any,
        observation: Any,
        *,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._cm = context_manager
        self._observation = observation
        self._on_close = on_close
        self._closed = False
        self.trace_id: str | None = getattr(observation, "trace_id", None)

    def update(self, **kwargs: Any) -> None:
        if self._closed:
            return
        payload = {k: _coerce_jsonable(v) for k, v in kwargs.items() if v is not None}
        if payload:
            self._observation.update(**payload)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._cm.__exit__(None, None, None)
            if self._on_close is not None:
                self._on_close()


class LangfuseTraceObserver:
    """Langfuse-backed tracing facade used by the runtime and Harbor bridge."""

    enabled = True

    def __init__(
        self,
        *,
        client: Any,
        propagate_fn: Callable[..., Any],
        session_id: str,
        interface: str,
        cwd: str,
        model: str,
        provider: str | None = None,
        user_id: str | None = None,
        flush_mode: str = "session_end",
    ) -> None:
        self._client = client
        self._propagate_fn = propagate_fn
        self._session_id = session_id
        self._interface = interface
        self._cwd = cwd
        self._model = model
        self._provider = provider
        self._user_id = user_id
        self._flush_mode = flush_mode
        self._propagation_context: Any | None = None
        self._session_handle: _LangfuseObservationHandle | None = None
        self.trace_id: str | None = None

    def start_session(self, *, metadata: dict[str, Any] | None = None) -> None:
        if self._session_handle is not None:
            return
        tags = ["openharness", self._interface]
        if self._provider:
            tags.append(self._provider)
        self.trace_id = self._client.create_trace_id(seed=f"{self._interface}:{self._session_id}")
        self._propagation_context = self._propagate_fn(
            user_id=self._user_id,
            session_id=self._session_id,
            trace_name=f"openharness.{self._interface}",
            tags=tags,
        )
        self._propagation_context.__enter__()
        self._session_handle = self._start_observation(
            name="session",
            as_type="agent",
            trace_context={"trace_id": self.trace_id},
            input={"cwd": self._cwd},
            metadata={"interface": self._interface, "cwd": self._cwd, "model": self._model,
                       "provider": self._provider, **(metadata or {})},
        )
        self._flush_if_live()

    def end_session(self, *, output: Any | None = None, metadata: dict[str, Any] | None = None) -> None:
        try:
            if self._session_handle is not None:
                self._session_handle.update(output=output, metadata=metadata)
                self._session_handle.close()
        finally:
            self._session_handle = None
            if self._propagation_context is not None:
                self._propagation_context.__exit__(None, None, None)
            self._propagation_context = None
            self.flush()

    def start_model_call(
        self,
        *,
        model: str,
        input: Any,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, str | int | float | bool | list[str] | None] | None = None,
    ) -> ObservationHandle:
        self.start_session()
        return self._start_observation(name="model", as_type="generation", input=input,
                                       metadata=metadata, model=model, model_parameters=model_parameters)

    def start_tool_call(
        self, *, tool_name: str, tool_input: Any, metadata: dict[str, Any] | None = None
    ) -> ObservationHandle:
        self.start_session()
        return self._start_observation(
            name=f"tool:{tool_name}",
            as_type="tool",
            input=tool_input,
            metadata=metadata,
        )

    def start_span(
        self,
        *,
        name: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObservationHandle:
        self.start_session()
        return self._start_observation(
            name=name,
            as_type="agent",
            input=input,
            metadata=metadata,
        )

    def model_call(
        self,
        *,
        model: str,
        input: Any,
        metadata: dict[str, Any] | None = None,
        model_parameters: dict[str, str | int | float | bool | list[str] | None] | None = None,
    ) -> ObservationScope:
        return ObservationScope(
            self.start_model_call(
                model=model,
                input=input,
                metadata=metadata,
                model_parameters=model_parameters,
            )
        )

    def tool_call(
        self, *, tool_name: str, tool_input: Any, metadata: dict[str, Any] | None = None
    ) -> ObservationScope:
        return ObservationScope(
            self.start_tool_call(tool_name=tool_name, tool_input=tool_input, metadata=metadata)
        )

    def span(
        self,
        *,
        name: str,
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObservationScope:
        return ObservationScope(self.start_span(name=name, input=input, metadata=metadata))

    def flush(self) -> None:
        self._client.flush()

    def _flush_if_live(self) -> None:
        if self._flush_mode == "live":
            self.flush()

    def _start_observation(self, *, name: str, as_type: str, input: Any | None = None,
                           metadata: dict[str, Any] | None = None,
                           trace_context: dict[str, str] | None = None,
                           model: str | None = None,
                           model_parameters: dict[str, str | int | float | bool | list[str] | None] | None = None,
                           ) -> _LangfuseObservationHandle:
        cm = self._client.start_as_current_observation(
            trace_context=trace_context, name=name, as_type=as_type,
            input=_coerce_jsonable(input), metadata=_coerce_jsonable(metadata),
            model=model, model_parameters=model_parameters,
        )
        return _LangfuseObservationHandle(
            cm,
            cm.__enter__(),
            on_close=self._flush_if_live if self._flush_mode == "live" else None,
        )


def create_trace_observer(
    *,
    session_id: str,
    interface: str,
    cwd: str,
    model: str,
    provider: str | None = None,
) -> TraceObserver:
    """Return a ``LangfuseTraceObserver`` when the environment is configured, else a ``NullTraceObserver``.

    Required environment variables:
    - ``LANGFUSE_PUBLIC_KEY`` and ``LANGFUSE_SECRET_KEY`` — always required,
      even for self-hosted deployments (Langfuse uses key-based auth everywhere).

    Optional environment variables:
    - ``LANGFUSE_HOST`` or ``LANGFUSE_BASE_URL`` — override the Langfuse endpoint.
      Set this to point to a local or self-hosted instance, e.g. ``http://localhost:3000``.
      Defaults to the Langfuse cloud endpoint when unset.
    - ``LANGFUSE_ENVIRONMENT``, ``LANGFUSE_RELEASE``, ``LANGFUSE_SAMPLE_RATE`` — forwarded to the SDK.
    - ``OPENHARNESS_LANGFUSE_ENABLED=0`` — force-disable tracing regardless of other variables.
    - ``OPENHARNESS_LANGFUSE_VERIFY=0`` — skip the auth check on startup (useful for CI).
    - ``OPENHARNESS_LANGFUSE_FLUSH_MODE=live`` — flush observations as spans close so
      traces become visible during long-running local runs.
    """
    if not _env_truthy(os.environ.get("OPENHARNESS_LANGFUSE_ENABLED", "1")):
        return NullTraceObserver()
    if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
        return NullTraceObserver()

    try:
        from langfuse import Langfuse, propagate_attributes  # noqa: PLC0415
    except ImportError:
        log.warning("langfuse package not installed, tracing disabled.")
        return NullTraceObserver()

    client = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        base_url=os.environ.get("LANGFUSE_BASE_URL") or None,
        host=os.environ.get("LANGFUSE_HOST") or None,
        environment=os.environ.get("LANGFUSE_ENVIRONMENT") or None,
        release=os.environ.get("LANGFUSE_RELEASE") or None,
        sample_rate=float(s) if (s := os.environ.get("LANGFUSE_SAMPLE_RATE")) else None,
    )

    if _env_truthy(os.environ.get("OPENHARNESS_LANGFUSE_VERIFY", "1")):
        try:
            if not client.auth_check():
                log.warning("Langfuse auth check failed, tracing disabled.")
                return NullTraceObserver()
        except OSError as exc:
            log.warning("Langfuse auth check failed, tracing disabled: %s", exc, exc_info=True)
            return NullTraceObserver()

    try:
        user_id = os.environ.get("OPENHARNESS_USER_ID") or os.environ.get("USER") or getpass.getuser()
    except OSError:
        user_id = None

    return LangfuseTraceObserver(
        client=client, propagate_fn=propagate_attributes,
        session_id=session_id, interface=interface, cwd=cwd,
        model=model,
        provider=provider,
        user_id=user_id,
        flush_mode=os.environ.get("OPENHARNESS_LANGFUSE_FLUSH_MODE", "session_end"),
    )
