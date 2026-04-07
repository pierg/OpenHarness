# Python Engineering Guidelines

---
**name:** python-engineering
**description:** Python conventions focusing on fail-fast principles, strict typing, and robust data modeling. Use for all source and test files.
---

## 1. Fail Fast & Defensive Design
* **No Silent Fallbacks:** Never use "magic" values (e.g., `""`, `-1`, `None`) to mask missing config or broken context. Raise `RuntimeError` or `ValueError` immediately.
* **The `None` Safety Rule:** Avoid returning `None` to signal an error. Use exceptions so the caller isn't forced to write `if x is not None` boilerplate.
* **Validate Boundaries:** Check preconditions at entry points. Fail with descriptive messages before executing expensive logic.

## 2. Precise Exception Handling
* **Narrow Catching:** Catch only specific exceptions (`KeyError`, `OSError`). Avoid bare `except Exception:` unless at the absolute top-level entry point for logging.
* **Maintain Context:** Always use `raise ... from exc` when wrapping exceptions to preserve the original traceback.
* **No Silent Suppression:** Never use `pass` in an `except` block. If an error is truly ignorable, log it with `stack_info=True`.
* **Flat Logic:** One `try/except` level per function. If you need nesting, refactor the inner block into a helper.

## 3. Data Modeling & State
* **Immutability First:** Use `@dataclass(frozen=True)` or Pydantic `frozen=True` by default to prevent state drift and ensure hashability.
* **Validation at Boundaries:** Use Pydantic `BaseModel` for any data entering the system (API, Env, Config). 
* **Functional Updates:** Never mutate in-place. Use `dataclasses.replace()` or `model_copy(update=...)` to return new instances.

## 4. Architecture & Dependencies
* **Dependency Inversion:** Use `typing.Protocol` to define interfaces. Decouple your logic from concrete implementations.
* **Exception Translation:** Wrap third-party SDKs (e.g., `boto3`, `requests`). Catch vendor errors at the boundary and translate them into domain-specific exceptions.
* **Strict Imports:** Use inline lazy imports ONLY for optional external dependencies. Never use them to fix circular dependencies; fix the architecture instead.

## 5. Strict Type Integrity
* **Mandatory Coverage:** Every function must be fully hinted, including `self`, `cls`, and explicit return types (use `-> None` where applicable).
* **Modern Syntax Only:**
    * Use **Standard Collections**: `list[str]`, `dict[str, int]` (not `List`, `Dict`).
    * Use **Union Operator**: `int | None` (not `Optional[int]`).
* **Type Aliases:** Use the `type` statement (3.12+) for complex types: `type UserID = int`.
* **Avoid `Any`:** Use `object` for unknown types or `Generics` for polymorphic ones.
* **The `Self` Type:** Use `typing.Self` for factory methods or fluent interfaces returning the class instance.

## 6. Style & Structure
* **Future-Proofing:** Always include `from __future__ import annotations` at the top of every file.
* **Module-Level Logging:** Define `log = logging.getLogger(__name__)` at the top of modules. Never use the root logger.
* **Clean Organization:** Group imports (Standard, Third-Party, Local). Use `__all__` to explicitly define public package APIs.