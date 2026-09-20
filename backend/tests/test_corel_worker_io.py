"""The worker's atomic JSON writes must survive a reader briefly holding the target
file open (Windows raises PermissionError from os.replace in that case) - found live:
the editor's progress polling plus per-image heartbeats made a scene export fail."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("win32com", reason="corel_worker imports the COM engine")
from app import corel_worker  # noqa: E402


def test_write_json_retries_when_the_target_is_briefly_locked(tmp_path, monkeypatch):
    target = tmp_path / "beat.heartbeat"
    real_replace = Path.replace
    failures = {"left": 3}

    def flaky(self, dest):
        if failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError(5, "Access is denied")
        return real_replace(self, dest)

    monkeypatch.setattr(Path, "replace", flaky)
    monkeypatch.setattr(corel_worker.time, "sleep", lambda s: None)
    corel_worker._write_json(target, {"step": "images 3/9"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"step": "images 3/9"}
    assert failures["left"] == 0
    assert not (tmp_path / "beat.heartbeat.tmp").exists()


def test_write_json_gives_up_with_the_real_error_after_many_failures(tmp_path, monkeypatch):
    def always(self, dest):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(Path, "replace", always)
    monkeypatch.setattr(corel_worker.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        corel_worker._write_json(tmp_path / "x.json", {"a": 1})
