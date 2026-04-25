"""Environment helpers for lab subprocesses."""

from __future__ import annotations

from pathlib import Path


def read_dotenv(path: Path) -> dict[str, str]:
    """Read a simple dotenv file without mutating ``os.environ``."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = _clean_dotenv_value(value.strip())
    return values


def apply_repo_dotenv(env: dict[str, str], dotenv_path: Path) -> dict[str, str]:
    """Merge repo ``.env`` into a subprocess env.

    Most values behave like normal dotenv defaults: inherited process
    variables win. ``GOOGLE_API_KEY`` is intentionally different for the lab:
    the repo-local key is the project identity, so it must override inherited
    Gemini key variables from other machines or shells.
    """
    if not dotenv_path.is_file():
        return env

    dotenv_values = read_dotenv(dotenv_path)
    for key, value in dotenv_values.items():
        env.setdefault(key, value)

    google_api_key = dotenv_values.get("GOOGLE_API_KEY")
    if google_api_key:
        env["GOOGLE_API_KEY"] = google_api_key
        env.pop("GEMINI_API_KEY", None)

    return env


def apply_gemini_run_key(env: dict[str, str], dotenv_path: Path) -> dict[str, str]:
    """Select the Gemini API key used by experiment run subprocesses."""
    return _apply_phase_gemini_key(
        env,
        dotenv_path,
        key_name="GEMINI_API_KEY_RUN",
    )


def apply_gemini_cli_key(env: dict[str, str], dotenv_path: Path) -> dict[str, str]:
    """Select the Gemini API key used by Gemini CLI subprocesses."""
    return _apply_phase_gemini_key(
        env,
        dotenv_path,
        key_name="GEMINI_API_KEY_CLI",
    )


def _apply_phase_gemini_key(
    env: dict[str, str],
    dotenv_path: Path,
    *,
    key_name: str,
) -> dict[str, str]:
    phase_key = _dotenv_value(dotenv_path, key_name) or env.get(key_name)
    if phase_key:
        env["GOOGLE_API_KEY"] = phase_key
        env["GEMINI_API_KEY"] = phase_key
    return env


def _dotenv_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    return read_dotenv(path).get(key)


def _clean_dotenv_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


__all__ = [
    "apply_gemini_cli_key",
    "apply_gemini_run_key",
    "apply_repo_dotenv",
    "read_dotenv",
]
