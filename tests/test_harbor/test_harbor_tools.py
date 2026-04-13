"""Tests for workspace-aware tools operating through HarborWorkspace."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.tools import WorkspaceToolRegistryFactory
from openharness.tools.base import ToolExecutionContext
from openharness.workspace.harbor import HarborWorkspace

pytest.importorskip("harbor", reason="Install Harbor test dependencies with `uv sync --extra harbor`.")
ExecResult = pytest.importorskip("harbor.environments.base").ExecResult


class FakeEnvironment:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.exec_calls: list[dict[str, object]] = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        del env, user
        self.exec_calls.append({"command": command, "cwd": cwd, "timeout_sec": timeout_sec})
        return ExecResult(stdout="ok\n", stderr="", return_code=0)

    async def upload_file(self, source_path, target_path):
        self.files[target_path] = Path(source_path).read_bytes()

    async def download_file(self, source_path, target_path):
        Path(target_path).write_bytes(self.files[source_path])

    async def is_dir(self, path, user=None):
        del user
        prefix = path.rstrip("/") + "/"
        return any(k.startswith(prefix) for k in self.files)

    async def is_file(self, path, user=None):
        del user
        return path in self.files


@pytest.mark.asyncio
async def test_harbor_write_and_read_file(tmp_path: Path) -> None:
    env = FakeEnvironment()
    registry = WorkspaceToolRegistryFactory().build(HarborWorkspace(env, cwd="/app"))
    ctx = ToolExecutionContext(cwd=tmp_path)

    write_tool = registry.get("write_file")
    assert write_tool is not None
    args = write_tool.input_model.model_validate({"path": "hello.txt", "content": "Hello, world!\n"})
    result = await write_tool.execute(args, ctx)
    assert not result.is_error
    assert env.files["/app/hello.txt"] == b"Hello, world!\n"

    read_tool = registry.get("read_file")
    assert read_tool is not None
    args = read_tool.input_model.model_validate({"path": "/app/hello.txt", "offset": 0, "limit": 5})
    result = await read_tool.execute(args, ctx)
    assert not result.is_error
    assert "Hello, world!" in result.output


@pytest.mark.asyncio
async def test_harbor_edit_file(tmp_path: Path) -> None:
    env = FakeEnvironment()
    env.files["/app/hello.txt"] = b"Hello, world!\n"
    registry = WorkspaceToolRegistryFactory().build(HarborWorkspace(env, cwd="/app"))
    ctx = ToolExecutionContext(cwd=tmp_path)

    edit_tool = registry.get("edit_file")
    assert edit_tool is not None
    args = edit_tool.input_model.model_validate(
        {"path": "hello.txt", "old_str": "world", "new_str": "Harbor", "replace_all": False}
    )
    result = await edit_tool.execute(args, ctx)
    assert not result.is_error
    assert env.files["/app/hello.txt"] == b"Hello, Harbor!\n"


@pytest.mark.asyncio
async def test_harbor_bash_executes_in_remote_cwd(tmp_path: Path) -> None:
    env = FakeEnvironment()
    registry = WorkspaceToolRegistryFactory().build(HarborWorkspace(env, cwd="/app"))
    ctx = ToolExecutionContext(cwd=tmp_path)

    bash_tool = registry.get("bash")
    assert bash_tool is not None
    args = bash_tool.input_model.model_validate({"command": "pwd", "cwd": "nested", "timeout_seconds": 12})
    result = await bash_tool.execute(args, ctx)

    assert not result.is_error
    assert result.output == "ok"
    assert env.exec_calls[-1]["cwd"] == "/app/nested"
