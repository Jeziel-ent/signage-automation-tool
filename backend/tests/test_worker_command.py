"""corel_supervisor.worker_command: `python -m app.corel_worker ...` from source, the exe in worker mode when frozen (agent/)."""
import sys
from pathlib import Path

from app import corel_supervisor


def test_from_source_the_worker_is_started_with_dash_m():
    cmd = corel_supervisor.worker_command("app.corel_worker", Path("jobs.json"), Path("results.json"))
    assert cmd == [sys.executable, "-m", "app.corel_worker", "jobs.json", "results.json"]


def test_frozen_the_exe_is_started_again_in_worker_mode(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = corel_supervisor.worker_command("app.corel_worker", Path("jobs.json"), Path("results.json"))
    assert cmd == [sys.executable, "--corel-worker", "app.corel_worker", "jobs.json", "results.json"]
