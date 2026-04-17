"""Tests for openharness.observability.langfuse."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from openharness.agents.contracts import TaskDefinition
from openharness.observability.langfuse import (
    LangfuseTraceObserver,
    NullObservationHandle,
    NullTraceObserver,
    create_trace_observer,
    trace_agent_run,
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
        self.trace_id_seeds: list[str | None] = []
        self.trace_url_ids: list[str | None] = []

    def create_trace_id(self, *, seed: str) -> str:
        self.trace_id_seeds.append(seed)
        return "trace-123"

    def start_as_current_observation(self, **kwargs: Any):
        obs = _Observation()
        self.started.append({"kwargs": kwargs, "obs": obs})

        @contextmanager
        def _cm():
            yield obs

        return _cm()

    def get_trace_url(self, *, trace_id: str | None = None) -> str:
        self.trace_url_ids.append(trace_id)
        return f"http://localhost:3000/project/demo/traces/{trace_id}"

    def flush(self) -> None:
        self.flush_count += 1


class _FakeClientWithoutTraceUrl:
    def __init__(self) -> None:
        self.started: list[dict] = []

    def create_trace_id(self, *, seed: str) -> str:
        del seed
        return "trace-123"

    def start_as_current_observation(self, **kwargs: Any):
        obs = _Observation()
        self.started.append({"kwargs": kwargs, "obs": obs})

        @contextmanager
        def _cm():
            yield obs

        return _cm()

    def flush(self) -> None:
        pass


@contextmanager
def _fake_propagate(**kwargs: Any):
    del kwargs
    yield


def _make_observer(**overrides: Any) -> LangfuseTraceObserver:
    defaults = dict(
        client=_FakeClient(),
        propagate_fn=_fake_propagate,
        session_id="sess-1",
        interface="interactive",
        cwd="/tmp",
        model="claude-test",
        provider="anthropic",
    )
    defaults.update(overrides)
    return LangfuseTraceObserver(**defaults)


class _PropagateRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @contextmanager
    def __call__(self, **kwargs: Any):
        self.calls.append(kwargs)
        yield


# ---------------------------------------------------------------------------
# NullTraceObserver
# ---------------------------------------------------------------------------


def test_null_observer_is_disabled():
    obs = NullTraceObserver()
    assert obs.enabled is False
    assert obs.trace_id is None
    assert obs.trace_url is None


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
    assert observer.trace_url == "http://localhost:3000/project/demo/traces/trace-123"


def test_required_trace_url_raises_when_sdk_cannot_build_url():
    observer = _make_observer(
        client=_FakeClientWithoutTraceUrl(),
        trace_url_required=True,
    )

    with pytest.raises(RuntimeError, match="get_trace_url"):
        observer.start_session()


def test_start_session_uses_run_id_as_trace_seed_and_name():
    client = _FakeClient()
    propagate = _PropagateRecorder()
    observer = _make_observer(client=client, propagate_fn=propagate, run_id="run-abc123def456")

    observer.start_session()

    assert observer.trace_id == "trace-123"
    assert observer.trace_name == "run-abc123def456"
    assert client.trace_id_seeds == ["run-abc123def456"]
    assert propagate.calls == [
        {
            "user_id": None,
            "session_id": "sess-1",
            "trace_name": "run-abc123def456",
            "tags": ["openharness", "interactive", "anthropic"],
        }
    ]


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


class _DummyRuntime:
    def __init__(self, observer: LangfuseTraceObserver) -> None:
        self.trace_observer = observer


class _DummyConfig:
    name = "demo-agent"
    architecture = "reflection"


class _DummyResult:
    final_text = "done"
    input_tokens = 5
    output_tokens = 2


class _DummyAgent:
    def __init__(self) -> None:
        self._config = _DummyConfig()

    @trace_agent_run
    async def run(self, task: TaskDefinition, runtime: _DummyRuntime) -> _DummyResult:
        del task, runtime
        return _DummyResult()


async def test_trace_agent_run_wraps_agent_boundary():
    client = _FakeClient()
    observer = _make_observer(client=client)
    agent = _DummyAgent()

    result = await agent.run(
        TaskDefinition(instruction="Fix the bug", payload={"path": "main.py"}),
        _DummyRuntime(observer),
    )

    assert result.final_text == "done"
    assert [call["kwargs"]["name"] for call in client.started] == ["session", "agent:demo-agent"]
    assert client.started[-1]["obs"].updates == [
        {
            "output": {"final_text": "done"},
            "metadata": {"input_tokens": 5, "output_tokens": 2},
        }
    ]


# ---------------------------------------------------------------------------
# create_trace_observer
# ---------------------------------------------------------------------------


def test_returns_null_when_keys_missing(monkeypatch):
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "1")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    observer = create_trace_observer(
        session_id="s",
        interface="i",
        cwd="/",
        model="m",
        run_id="run-abc123def456",
    )
    assert isinstance(observer, NullTraceObserver)
    assert observer.run_id == "run-abc123def456"


def test_required_langfuse_raises_when_keys_missing(monkeypatch):
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "1")
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_REQUIRED", "1")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="LANGFUSE_PUBLIC_KEY"):
        create_trace_observer(session_id="s", interface="i", cwd="/", model="m")


def test_returns_null_when_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_ENABLED", "0")
    monkeypatch.setenv("OPENHARNESS_LANGFUSE_REQUIRED", "1")
    observer = create_trace_observer(session_id="s", interface="i", cwd="/", model="m")
    assert isinstance(observer, NullTraceObserver)


# ---------------------------------------------------------------------------
# Public-host trace URL rewrite
# ---------------------------------------------------------------------------


def test_rewrite_trace_url_replaces_host():
    from openharness.observability.langfuse import rewrite_trace_url_for_public

    rewritten = rewrite_trace_url_for_public(
        "http://10.0.0.4:3010/project/demo/traces/abc",
        "http://localhost:3010",
    )
    assert rewritten == "http://localhost:3010/project/demo/traces/abc"


def test_rewrite_trace_url_no_op_when_no_host():
    from openharness.observability.langfuse import rewrite_trace_url_for_public

    url = "http://10.0.0.4:3010/project/demo/traces/abc"
    assert rewrite_trace_url_for_public(url, None) == url


def test_rewrite_trace_url_uses_env(monkeypatch):
    from openharness.observability.langfuse import rewrite_trace_url_for_public

    monkeypatch.setenv("LANGFUSE_PUBLIC_HOST", "http://localhost:3010")
    rewritten = rewrite_trace_url_for_public(
        "http://10.0.0.4:3010/project/demo/traces/abc"
    )
    assert rewritten.startswith("http://localhost:3010/")


# ---------------------------------------------------------------------------
# Native usage_details / cost_details passthrough
# ---------------------------------------------------------------------------


def test_observation_handle_passes_usage_and_cost_details_through():
    client = _FakeClient()
    observer = _make_observer(client=client)
    handle = observer.start_model_call(model="claude-test", input="hi")
    handle.update(
        output="done",
        usage_details={"input": 10, "output": 5, "total": 15},
        cost_details={"total": 0.0042},
        metadata={"turn_index": 0},
    )
    update = client.started[-1]["obs"].updates[0]
    assert update["usage_details"] == {"input": 10, "output": 5, "total": 15}
    assert update["cost_details"] == {"total": 0.0042}
    assert update["output"] == "done"


# ---------------------------------------------------------------------------
# Session metadata + tags
# ---------------------------------------------------------------------------


def test_start_session_includes_extra_tags_and_input():
    client = _FakeClient()
    propagate = _PropagateRecorder()
    observer = _make_observer(
        client=client,
        propagate_fn=propagate,
        run_id="trial-xyz",
        trace_name="fix-bug · basic",
        extra_tags=["experiment:tb2", "task:fix-bug", "model:gemini-flash"],
    )
    observer.start_session(metadata={"input": {"task": "fix-bug"}, "task_name": "fix-bug"})

    assert observer.trace_name == "fix-bug · basic"
    tags = propagate.calls[0]["tags"]
    assert "experiment:tb2" in tags
    assert "task:fix-bug" in tags
    assert "model:gemini-flash" in tags
    session_kwargs = client.started[0]["kwargs"]
    assert session_kwargs["input"] == {"task": "fix-bug"}
    assert session_kwargs["metadata"]["task_name"] == "fix-bug"
