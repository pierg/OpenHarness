---
name: python-engineering
description: >
  Python conventions and engineering patterns. Use when writing, reviewing,
  or editing any Python source or test file. Focuses on fail-fast principles,
  strict exception handling, and robust data modeling.
---

# Python Engineering Guidelines

## 1. Fail Fast & Explicitly

- **No Silent Fallbacks:** Never use hardcoded fallback values (e.g., `"default"`, `"coordinator"`, `None`) to mask missing configuration, missing environment variables, or broken context. If a required value is missing, raise an exception immediately.
- **Careful with Default Values:** Only use default arguments if the default is truly universally applicable and safe. Do not use defaults to suppress errors or avoid setting required state.
- **Validate Early:** Check preconditions at the entry points of functions and fail fast with clear, descriptive error messages if they are not met.

## 2. Strict Exception Handling (No Excessive Try/Except)

- **Don't catch what you don't handle:** If you cannot meaningfully recover from an error, let it propagate. The caller deserves to know something failed.
- **Narrow Exceptions:** Catch the narrowest possible exception type (`KeyError`, `ValueError`, `OSError`). Avoid bare `except Exception:` unless it is at a top-level boundary (like a main loop) where you log it and exit/re-raise.
- **No Nested Try/Excepts:** Keep error handling flat. One `try/except` level per function. If you need more, extract the inner logic into a helper function.
- **Do not mask internal architecture issues:** Never use `try...except ImportError` to handle circular dependencies within the same project. Fix the architecture or use local function-level imports instead. `ImportError` catching is ONLY valid for optional *external* dependencies.
- **No `try/except/pass`:** Never swallow errors silently. If an optional subsystem fails (like tracing or telemetry), log the failure with `exc_info=True`.
- **Correct Re-raising:** When wrapping exceptions, always use `raise MyError(...) from exc` to preserve the original traceback, or `raise` (bare) to re-raise the exact same exception.

## 3. Data Modeling & State

- **Immutability First:** Prefer immutable data structures. Use `@dataclass(frozen=True)` for internal data transfer objects (DTOs) and events.
- **Validation at Boundaries:** Use Pydantic `BaseModel` for external data, settings, or anything requiring strict validation and serialization.
- **No In-Place Mutation:** When updating models, return a new copy (e.g., `model_copy(update=...)` for Pydantic or `dataclasses.replace()`) rather than mutating fields in place.

## 4. Architecture & Dependencies

- **Dependency Inversion:** Use `typing.Protocol` instead of concrete classes or `abc.ABC` to define interfaces for injected dependencies. This keeps modules decoupled.
- **Error Translation:** Do not let vendor or third-party SDK exceptions leak through your domain boundary. Catch them at the integration point and translate them into domain-specific exceptions.
- **Lazy Imports for Externals Only:** Only use inline lazy imports (`import foo` inside a function) for truly optional external dependencies.

## 5. Style & Structure

- **Modern Type Hinting:** Always include `from __future__ import annotations` at the top of files to enable modern type hinting syntax (`X | Y`).
- **Clean File Structure:**
  - Define `log = logging.getLogger(__name__)` at the top of every module that needs logging.
  - Use visual section banners (e.g., `# --- Internal helpers ---`) to organize files longer than ~100 lines.
