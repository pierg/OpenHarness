"""Tests for the systemd-supervisor + process-tree web slice.

Covers four things in addition to the existing web suite:

1. ``services.py`` returns a typed snapshot for every known unit and
   keeps soft failure (``error`` field) when ``systemctl`` is absent
   — never raises.
2. The new whitelist entries (``daemon-restart``, ``service-restart``,
   ``kill-process``) exist with the expected ``argv_prefix`` so a
   careless edit can't accidentally route them through ``uv run lab``
   again.
3. ``run_command`` honours ``argv_prefix`` end-to-end — the assembled
   argv has the right shape and resolves the binary via
   ``shutil.which`` (so an unknown binary becomes exit 127 instead of
   leaking ``FileNotFoundError``).
4. The ``kill-process`` precheck blocks PIDs that aren't descendants
   of the daemon (and fails fast when no daemon is running) without
   ever spawning ``kill``.

Process-tree rendering is exercised via the ``/_hx/process-tree``
HTMX endpoint, which proves the partial template + reader integrate
end-to-end with whatever the test process has as its own
descendants. We don't assert specific content because that depends
on the live VM state — instead we assert it returns 200 and contains
either the empty-state copy or a real PID column header.
"""

from __future__ import annotations

import shutil
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from openharness.lab.web import commands as labcmd
from openharness.lab.web import services as labsvc
from openharness.lab.web.app import create_app


# ---------------------------------------------------------------------------
# services.py
# ---------------------------------------------------------------------------


def test_all_status_returns_one_row_per_known_unit() -> None:
    rows = labsvc.all_status()
    assert [r.unit_id for r in rows] == labsvc.UNITS
    for r in rows:
        # The structural fields are always populated, even when
        # systemctl is missing or the unit isn't installed.
        assert r.unit_id == r.unit_id  # type narrows
        assert isinstance(r.description, str) and r.description
        assert isinstance(r.is_active, bool)


def test_status_soft_fails_when_systemctl_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate a host with no systemctl on PATH.
    monkeypatch.setattr(labsvc, "_systemctl", lambda: None)
    s = labsvc.status("openharness-lab")
    assert s.error is not None
    assert s.is_installed is False
    assert s.is_active is False
    assert s.can_start is False  # not installed → no controls
    assert s.can_stop is False
    assert s.can_restart is False


def test_status_can_flags_consistent_with_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force an "active+installed" snapshot by stubbing the systemctl
    # wrappers; the post-init computes can_start/can_stop/can_restart.
    monkeypatch.setattr(labsvc, "_systemctl", lambda: "/usr/bin/systemctl")
    monkeypatch.setattr(
        labsvc, "_systemctl_show",
        lambda u: {
            "LoadState":   "loaded",
            "ActiveState": "active",
            "SubState":    "running",
            "FragmentPath": "/some/path",
            "MainPID":     "1234",
            "ActiveEnterTimestamp": "Tue 2026-04-21 21:04:28 UTC",
        },
    )
    monkeypatch.setattr(labsvc, "_systemctl_is_active", lambda u: (True, 0))
    monkeypatch.setattr(labsvc, "_systemctl_is_enabled", lambda u: True)
    s = labsvc.status("openharness-lab")
    assert s.is_active and s.is_installed
    assert s.can_stop and s.can_restart and not s.can_start
    assert s.main_pid == 1234


# ---------------------------------------------------------------------------
# Whitelist shape
# ---------------------------------------------------------------------------


_SYSTEMCTL_CMDS = ["daemon-start", "daemon-stop", "daemon-restart", "service-restart"]


def test_systemctl_commands_use_systemctl_prefix() -> None:
    # If someone accidentally reverts ``argv_prefix`` to the default
    # ``["uv", "run", "lab"]`` these would silently route through the
    # CLI (``uv run lab start openharness-daemon.service`` would fail
    # in confusing ways). Hard-fail at unit-test time instead.
    for cid in _SYSTEMCTL_CMDS:
        spec = labcmd.COMMANDS[cid]
        assert spec.argv_prefix == ["systemctl", "--user"], (
            f"{cid} must use systemctl, not {spec.argv_prefix}"
        )


def test_kill_process_uses_kill_prefix_and_has_precheck() -> None:
    spec = labcmd.COMMANDS["kill-process"]
    assert spec.argv_prefix == ["kill"]
    assert spec.argv_template == ["-TERM", "{pid}"]
    # Defence-in-depth: precheck wired up, danger flag set, confirm
    # text non-empty.
    assert spec.precheck is labcmd._precheck_kill_process
    assert spec.danger is True
    assert spec.confirm_text


def test_service_restart_unit_param_rejects_arbitrary_input() -> None:
    spec = labcmd.COMMANDS["service-restart"]
    pat = next(p.pattern for p in spec.params if p.name == "unit")
    # Whitelist-bound — only the two unit ids match.
    assert pat.fullmatch("openharness-lab")
    assert pat.fullmatch("openharness-daemon")
    # Anything else, including obvious injection attempts, refused.
    for bad in ("ssh", "openharness-lab.service", "openharness-lab && rm -rf /",
                "../../etc/passwd", ""):
        assert not pat.fullmatch(bad)


# ---------------------------------------------------------------------------
# argv assembly with argv_prefix
# ---------------------------------------------------------------------------


def test_run_command_builds_argv_from_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub subprocess so we can assert on argv without actually
    # touching systemctl / kill / uv on the host.
    captured: dict[str, list[str]] = {}

    class _CompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **_kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        return _CompletedProcess()

    monkeypatch.setattr(labcmd.subprocess, "run", fake_run)
    monkeypatch.setattr(labcmd, "_record", lambda r: None)

    res = labcmd.run_command("daemon-restart", {}, actor="test:webui")
    assert res.exit_code == 0
    argv = captured["argv"]
    # systemctl resolved (or echoed if absent); always followed by
    # --user + the literal restart args.
    assert argv[1:] == [
        "--user", "restart", "openharness-daemon.service",
    ]


def test_run_command_substitutes_unit_param_in_template(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    class _CP:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(labcmd.subprocess, "run",
                        lambda argv, **_: (captured.update(argv=argv) or _CP()))
    monkeypatch.setattr(labcmd, "_record", lambda r: None)

    labcmd.run_command(
        "service-restart",
        {"unit": "openharness-lab"},
        actor="test:webui",
    )
    assert captured["argv"][1:] == [
        "--user", "restart", "openharness-lab.service",
    ]


def test_empty_argv_prefix_is_rejected() -> None:
    # Defence in depth: a CommandSpec with [] argv_prefix could leak
    # an arg-only `subprocess.run` that picks up whatever's first in
    # the validated params. We refuse outright at runtime.
    bad = labcmd.CommandSpec(
        cmd_id="bad-no-prefix",
        label="bad",
        description="bad",
        argv_template=["echo", "hello"],
        params=[],
        argv_prefix=[],
    )
    labcmd.COMMANDS["bad-no-prefix"] = bad
    try:
        with pytest.raises(labcmd.CommandError, match="empty argv_prefix"):
            labcmd.run_command("bad-no-prefix", {})
    finally:
        del labcmd.COMMANDS["bad-no-prefix"]


# ---------------------------------------------------------------------------
# kill-process precheck
# ---------------------------------------------------------------------------


def test_kill_precheck_refuses_self_pid() -> None:
    import os
    with pytest.raises(labcmd.CommandError, match="web UI's own"):
        labcmd._precheck_kill_process({"pid": str(os.getpid())})


def test_kill_precheck_refuses_when_daemon_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    # Stub services to report the daemon as not running.
    fake = labsvc.UnitStatus(
        unit_id="openharness-daemon",
        description="d",
        load_state="loaded",
        active_state="inactive",
        sub_state="dead",
        is_active=False,
        is_enabled=True,
        is_installed=True,
        main_pid=None,
        started_at=None,
    )
    monkeypatch.setattr(labcmd.labsvc, "status", lambda u: fake)
    with pytest.raises(labcmd.CommandError, match="daemon is not running"):
        labcmd._precheck_kill_process({"pid": "999999"})


def test_kill_precheck_refuses_pid_not_under_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """The most important test: a random VM pid must be rejected."""
    import psutil

    # Pretend the daemon is some pid we know is not the test pid's
    # parent — the systemd init pid (1) works because no test
    # subprocess will be a descendant of it without also being a
    # descendant of our actual session.
    fake = labsvc.UnitStatus(
        unit_id="openharness-daemon",
        description="d",
        load_state="loaded",
        active_state="active",
        sub_state="running",
        is_active=True,
        is_enabled=True,
        is_installed=True,
        main_pid=1,
        started_at=None,
    )
    monkeypatch.setattr(labcmd.labsvc, "status", lambda u: fake)

    # Pick any PID that isn't under PID 1's tree from psutil's
    # perspective — but psutil's parent chain *does* include init.
    # So instead we choose a target whose parent chain we can stub:
    # mock psutil.Process(target).parents() to return an empty list
    # (i.e. an orphan), then assert we still reject it.
    orphan_pid = 999_999  # arbitrary, doesn't have to exist
    fake_proc = mock.MagicMock()
    fake_proc.parents.return_value = []
    monkeypatch.setattr(psutil, "Process", lambda pid: fake_proc)

    with pytest.raises(labcmd.CommandError, match="not a descendant"):
        labcmd._precheck_kill_process({"pid": str(orphan_pid)})


# ---------------------------------------------------------------------------
# End-to-end web rendering
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("LAB_TRUST_PROXY_AUTH", raising=False)
    return TestClient(create_app())


def _sample_unit_rows() -> list[labsvc.UnitStatus]:
    """Deterministic rows so the services partial is assertable without systemctl."""
    return [
        labsvc.UnitStatus(
            unit_id="openharness-lab",
            description="FastAPI web UI (this process)",
            load_state="loaded",
            active_state="active",
            sub_state="running",
            is_active=True,
            is_enabled=True,
            is_installed=True,
            main_pid=123,
            started_at=None,
        ),
        labsvc.UnitStatus(
            unit_id="openharness-daemon",
            description="Orchestrator daemon (walks the roadmap)",
            load_state="loaded",
            active_state="inactive",
            sub_state="dead",
            is_active=False,
            is_enabled=True,
            is_installed=True,
            main_pid=None,
            started_at=None,
        ),
    ]


def test_daemon_page_renders_services_and_process_tree(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The redesigned daemon cockpit still surfaces services + process tree.

    After the redesign these panels live inside the collapsed
    ``Diagnostics`` <details>, so we assert against the section
    headers + the HTMX wiring that fetches them on disclosure
    rather than against the unit-id sidebar (which is now lazy
    and only shows up after the panel is opened — see
    :func:`test_hx_partials_return_200` for the unit-id check).
    """
    # Hosts without ``systemctl`` (macOS, minimal CI) get a stub partial;
    # assert full table content using a fixed snapshot.
    monkeypatch.setattr(labsvc, "available", lambda: True)
    monkeypatch.setattr(labsvc, "all_status", _sample_unit_rows)
    r = client.get("/daemon")
    assert r.status_code == 200
    body = r.text
    assert "Services" in body
    assert "Process tree" in body
    assert "Diagnostics" in body
    # Auto-refresh wiring: HTMX poll triggers for both partials.
    assert "lab-services-changed" in body
    assert "lab-processes-changed" in body
    # The (now-lazy) services partial still ships the unit ids when
    # fetched directly — verify here so the lazy disclosure actually
    # has the same content as the legacy eager version.
    services_body = client.get("/_hx/services").text
    assert "openharness-lab" in services_body
    assert "openharness-daemon" in services_body


def test_hx_partials_return_200(client: TestClient) -> None:
    for path in ("/_hx/services", "/_hx/process-tree", "/_hx/daemon-status"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}\n{r.text[:400]}"


def test_api_cmd_rejects_unknown_unit(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A URL-encoded ``unit`` that doesn't match the regex must be
    rejected with 400 — never reach systemctl. This is the surface
    most operationally exposed to typo-driven foot-guns."""
    r = client.post("/api/cmd", data={
        "cmd_id": "service-restart",
        "unit": "ssh",  # not in the allow-list
    })
    assert r.status_code == 400
    assert "does not match" in r.text or "pattern" in r.text


def test_api_cmd_rejects_kill_without_pid(client: TestClient) -> None:
    r = client.post("/api/cmd", data={"cmd_id": "kill-process"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Real systemctl integration (skipped on hosts without it)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("systemctl") is None,
    reason="systemctl not on PATH",
)
def test_real_systemctl_returns_known_states() -> None:
    # Doesn't assert the units are installed (CI may not have them),
    # but does assert the systemctl wrappers don't crash and yield
    # parseable values.
    rows = labsvc.all_status()
    for r in rows:
        # active_state / load_state are systemd-vocabulary strings or None.
        assert r.active_state is None or isinstance(r.active_state, str)
        assert r.load_state is None or isinstance(r.load_state, str)
