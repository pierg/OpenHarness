"""Tests for openharness.observability.langfuse."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from openharness.observability.langfuse import (
    LangfuseTraceObserver,
    NullObservationHandle,
    NullTraceObserver,
    create_trace_observer,
)


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------


class _Observation:
    def __init__(self, trace_id: str = "trace-123") -> None:
        self.trace_id = trace_id
        self.updates: list[dict] = []

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


class _FakeClient:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.flushed = False

    def create_trace_id(self, *, seed: str) -> str:
        return "trace-123"

    def start_as_current_observation(self, **kwargs: Any):
        obs = _Observation()
        self.started.append({"kwargs": kwargs, "obs": obs})

        @contextmanager
        def _cm():
            yield obs

        return _cm()

    def flush(self) -> None:
        self.flushed = True


@contextmanager
def _fake_propagate(**kwargs: Any):
    yield


def _make_observer(**overrides: Any) -> LangfuseTraceObserver:
    defaults = dict(client=_FakeClient(), propagate_fn=_fake_propagate,
                    session_id="sess-1", interface="interactive",
                    cwd="/tmp", model="claude-test", provider="anthropic")
    defaults.update(overrides)
    return LangfuseTraceObserver(**defaults)


# ---------------------------------------------------------------------------
# NullTraceObserver
# ---------------------------------------------------------------------------


def test_null_observer_is_disabled():
    obs = NullTraceObserver()
    assert obs.enabled is False
    assert obs.trace_id is None


def test_null_observer_methods_are_noop():
    obs = NullTraceObserver()
    obs.start_session()
    obs.end_session()
    handle = obs.start_turn(prompt="hi")
    assert isinstance(handle, NullObservationHandle)
    handle.update(output="x")
    handle.close()
    obs.flush()


# ---------------------------------------------------------------------------
# LangfuseTraceObserver
# ---------------------------------------------------------------------------


def test_start_session_sets_trace_id():
    observer = _make_observer()
    observer.start_session()
    assert observer.trace_id == "trace-123"


def test_start_session_is_idempotent():
    client = _FakeClient()
    observer = _make_observer(client=client)
    observer.start_session()
    observer.start_session()
    assert len(client.started) == 1  # only one session observation


def test_start_turn_implicitly_starts_session():
    client = _FakeClient()
    observer = _make_observer(client=client)
    observer.start_turn(prompt="hello")
    assert observer.trace_id == "trace-123"
    assert len(client.started) == 2  # session + turn


def test_start_model_call_type_is_generation():
    client = _FakeClient()
    observer = _make_observer(client=client)
    observer.start_model_call(name="LLM", model="claude-test", input="prompt")
    generation = client.started[-1]["kwargs"]
    assert generation["as_type"] == "generation"


def test_start_tool_call_type_is_tool():
    client = _FakeClient()
    observer = _make_observer(client=client)
    observer.start_tool_call(tool_name="bash", tool_input={"cmd": "ls"})
    tool = client.started[-1]["kwargs"]
    assert tool["as_type"] == "tool"
    assert tool["name"] == "bash"


def test_end_session_flushes_client():
    client = _FakeClient()
    observer = _make_observer(client=client)
    observer.start_session()
    observer.end_session()
    assert client.flushed is True


def test_observation_handle_update_and_close():
    client = _FakeClient()
    observer = _make_observer(client=client)
    handle = observer.start_turn(prompt="hi")
    handle.update(output="done")
    handle.close()
    assert client.started[-1]["obs"].updates == [{"output": "done"}]


def test_observation_handle_close_is_idempotent():
    client = _FakeClient()
    observer = _make_observer(client=client)
    handle = observer.start_turn(prompt="hi")
    handle.close()
    handle.close()  # should not raise


# ---------------------------------------------------------------------------
# create_trace_observer
# ---------------------------------------------------------------------------


def test_returns_null_when_keys_missing(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    observer = create_trace_observer(session_id="s", interface="i", cwd="/", model="m")
    assert isinstance(observer, NullTraceObserver)


def test_returns_null_when_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "0")
    observer = create_trace_observer(session_id="s", interface="i", cwd="/", model="m")
    assert isinstance(observer, NullTraceObserver)
