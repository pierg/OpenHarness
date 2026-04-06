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
        self.flush_count = 0

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
        self.flush_count += 1


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
    handle = obs.start_model_call(model="claude-test", input="hi")
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


def test_start_model_call_implicitly_starts_session():
    client = _FakeClient()
    observer = _make_observer(client=client)
    observer.start_model_call(model="claude-test", input="hello")
    assert observer.trace_id == "trace-123"
    assert len(client.started) == 2  # session + turn


def test_start_model_call_type_is_generation_named_model():
    client = _FakeClient()
    observer = _make_observer(client=client)
    observer.start_model_call(model="claude-test", input="prompt")
    generation = client.started[-1]["kwargs"]
    assert generation["as_type"] == "generation"
    assert generation["name"] == "model"


def test_start_tool_call_type_is_tool_with_prefixed_name():
    client = _FakeClient()
    observer = _make_observer(client=client)
    observer.start_tool_call(tool_name="bash", tool_input={"cmd": "ls"})
    tool = client.started[-1]["kwargs"]
    assert tool["as_type"] == "tool"
    assert tool["name"] == "tool:bash"


def test_start_span_type_is_agent():
    client = _FakeClient()
    observer = _make_observer(client=client)
    observer.start_span(name="planner_phase", input={"step": 1})
    span = client.started[-1]["kwargs"]
    assert span["as_type"] == "agent"
    assert span["name"] == "planner_phase"


def test_end_session_flushes_client():
    client = _FakeClient()
    observer = _make_observer(client=client)
    observer.start_session()
    observer.end_session()
    assert client.flush_count == 1


def test_live_flush_mode_flushes_session_start():
    client = _FakeClient()
    observer = _make_observer(client=client, flush_mode="live")
    observer.start_session()
    assert client.flush_count == 1


def test_live_flush_mode_flushes_on_observation_close():
    client = _FakeClient()
    observer = _make_observer(client=client, flush_mode="live")
    with observer.span(name="outer-step", input={"ok": True}) as span:
        span.update(output="done")

    assert client.flush_count == 2  # session start + span close


def test_observation_handle_update_and_close():
    client = _FakeClient()
    observer = _make_observer(client=client)
    handle = observer.start_model_call(model="claude-test", input="hi")
    handle.update(output="done")
    handle.close()
    assert client.started[-1]["obs"].updates == [{"output": "done"}]


def test_observation_handle_close_is_idempotent():
    client = _FakeClient()
    observer = _make_observer(client=client)
    handle = observer.start_model_call(model="claude-test", input="hi")
    handle.close()
    handle.close()  # should not raise


def test_scope_helpers_close_observations():
    client = _FakeClient()
    observer = _make_observer(client=client)
    with observer.span(name="outer-step", input={"ok": True}) as span:
        span.update(output="done")

    assert client.started[-1]["obs"].updates == [{"output": "done"}]

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
