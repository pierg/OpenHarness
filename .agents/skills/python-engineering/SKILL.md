---
name: python-engineering
description: >
  Python conventions and engineering patterns specific to this repo. Use when
  writing, reviewing, or editing any Python source or test file in OpenHarness.
---

# Python Engineering Guidelines — OpenHarness

## File header

```python
"""Module docstring."""

from __future__ import annotations  # always — enables X | Y on Python 3.10

# imports ...

log = logging.getLogger(__name__)   # every module that logs
```

Section banners in files longer than ~100 lines:
```python
# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
```

## Data modeling — two tiers, choose deliberately

- **Pydantic `BaseModel`** — settings, messages, anything needing validation or serialization. Use `model_copy(update=...)` as the only mutation primitive (never mutate in place).
- **`@dataclass(frozen=True)`** — small immutable DTOs (events, requests). No validation needed.

## Protocol over ABC for dependency inversion

The query engine binds to `SupportsStreamingMessages` (a `Protocol`), not to any concrete client. Follow this pattern for any new provider or injectable dependency.

## Exception handling

**Fail fast. Never mask errors.**

```python
# ✓ let it propagate — the caller deserves to know
result = some_operation()

# ✗ never do this — hides bugs and config mistakes
try:
    result = some_operation()
except Exception:
    pass
```

**Rules, in order of priority:**

1. **Don't catch what you don't handle.** If you can't meaningfully recover, don't catch.
2. **Catch the narrowest possible type.** `except ValueError`, `except OSError`, never bare `except Exception` unless you re-raise or it's a documented SDK boundary.
3. **Never nest try/except.** One level per function. Extract inner logic to a helper if you need more.
4. **`try/finally` is fine; `try/except/pass` is not.** Cleanup in `finally` always propagates the exception.
5. **`ImportError` is the only valid broad-ish catch** — and only for optional dependencies at the import site.
6. **Optional/background subsystems** (tracing, telemetry) may silently degrade on network errors, but must use `exc_info=True` so the traceback is logged:
   ```python
   try:
       client.auth_check()
   except OSError as exc:
       log.warning("Auth check failed, tracing disabled: %s", exc, exc_info=True)
       return NullTraceObserver()
   ```
7. **Config errors must propagate.** A bad `LANGFUSE_SAMPLE_RATE`, a missing env var, a malformed URL — these are user mistakes. Let them crash with a clear message rather than silently defaulting.
8. **Never re-raise with `raise exc from exc`** — use `raise ... from exc` (original) or `raise` (bare, inside except).

## Error translation

Every API client has a `_translate_<provider>_error(exc) -> OpenHarnessApiError` helper. Map vendor exceptions there; never let them leak out. `OpenHarnessApiError` subclasses are always re-raised immediately — never retried.

## Retry loop shape

Public method delegates to `_stream_once`; the loop lives in the public method only:

```python
for attempt in range(MAX_RETRIES + 1):
    try:
        async for event in self._stream_once(request):
            yield event
        return
    except OpenHarnessApiError:
        raise
    except Exception as exc:
        if attempt >= MAX_RETRIES or not _is_retryable(exc):
            raise _translate_<provider>_error(exc) from exc
        await asyncio.sleep(_backoff_delay(attempt))
```

Backoff: `min(BASE_DELAY * 2**attempt, MAX_DELAY) + uniform(0, delay * 0.25)`.

## Lazy imports for optional dependencies

```python
def __init__(self) -> None:
    from google import genai  # noqa: PLC0415
```

Used for optional extras (e.g. `google-genai`). The `noqa` comment is required.

## Settings: API keys are never stored

`resolve_api_key()` resolves lazily from env at call time. Don't write resolved keys back into the `Settings` object or persist them to disk.

---

## Testing

**Optional SDK stubbing** — inject the entire module tree into `sys.modules` via an `autouse=True` fixture; don't require the optional package to be installed:

```python
@pytest.fixture(autouse=True)
def _stub_genai(monkeypatch):
    genai = MagicMock(name="google.genai")
    google = MagicMock(name="google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return genai
```

**Streaming fakes** — define local `_chunk(...)`, `_Aiter`, and `_setup_stream(client, *chunks)` helpers rather than inline `MagicMock` chains. Keeps test bodies readable.

**`asyncio_mode = "auto"`** is set in `pyproject.toml` — plain `async def test_*` works, no `@pytest.mark.asyncio` needed.

**Test private helpers directly** — import `_build_gemini_tools`, `_backoff_delay`, etc. in tests to cover internals without going through the full public API.
