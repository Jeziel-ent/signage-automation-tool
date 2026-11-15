"""Helper programs started by the agent must not flash a console window (CREATE_NO_WINDOW on Windows)."""
from __future__ import annotations

import subprocess

from app import corel_supervisor, corel_util, fonts, winproc


def test_windows_gets_the_flag_and_other_systems_nothing():
    assert winproc.no_window("win32") == {"creationflags": 0x08000000}
    assert winproc.no_window("linux") == {}


def test_the_worker_is_hidden_only_in_the_packaged_agent():
    assert winproc.worker_window_kwargs("win32", frozen=True) == {"creationflags": 0x08000000}
    assert winproc.worker_window_kwargs("win32", frozen=False) == {}          # a developer's terminal keeps the worker's log lines
    assert winproc.worker_window_kwargs("linux", frozen=True) == {}


class _Done:
    returncode = 0
    stdout = ""
    stderr = ""


def _capture(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd[0], kw))
        return _Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_every_helper_program_call_passes_the_no_window_flag(monkeypatch):
    monkeypatch.setattr(winproc.sys, "platform", "win32")
    calls = _capture(monkeypatch)
    corel_util.corel_pids()                       # tasklist
    corel_util.force_kill(1)                      # taskkill
    corel_supervisor._kill_tree(2)                # taskkill /T
    try:
        fonts._from_powershell()                  # powershell
    except Exception:
        pass
    assert {"tasklist", "taskkill", "powershell"} <= {c[0] for c in calls}
    for name, kw in calls:
        assert kw.get("creationflags") == winproc.CREATE_NO_WINDOW, name
