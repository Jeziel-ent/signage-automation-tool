"""The "already installed and running" check of the installer (double-clicking the downloaded exe a second time)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from signage_agent import installer

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows file locking")


@pytest.fixture
def running_exe(tmp_path):
    """A copy of cmd.exe named SignageAgent.exe, running (Windows locks a running image against writing, not against renaming)."""
    exe = tmp_path / "SignageAgent.exe"
    shutil.copy2(Path(r"C:\Windows\System32\cmd.exe"), exe)
    proc = subprocess.Popen([str(exe), "/c", "ping", "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    yield exe
    proc.kill()
    proc.wait()


def test_a_running_exe_is_in_use(running_exe):
    assert installer._in_use(running_exe) is True


def test_a_file_that_is_not_running_is_free(tmp_path):
    f = tmp_path / "SignageAgent.exe"
    f.write_bytes(b"x")
    assert installer._in_use(f) is False


def test_a_second_double_click_reports_already_running(running_exe, tmp_path, monkeypatch):
    """The real failure: the installed agent runs, the downloaded exe is double-clicked again -> no 'Permission denied' error."""
    download = tmp_path / "download"
    download.mkdir()
    (download / "SignageAgent.exe").write_bytes(b"new build")
    (download / "config.json").write_text("{}")
    monkeypatch.setattr(installer.sys, "executable", str(download / "SignageAgent.exe"))
    monkeypatch.setattr(installer, "install_dir", lambda: running_exe.parent)
    said = []
    monkeypatch.setattr(installer, "message", lambda text, *a, **k: said.append((text, k.get("error", False))))
    monkeypatch.setattr(installer.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start a second agent"))
    assert installer.install_and_launch() is True
    assert said and "already installed and running" in said[0][0] and said[0][1] is False


def test_the_updater_and_installer_start_the_agent_as_a_fresh_app(monkeypatch, tmp_path):
    from signage_agent import updater
    started = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw)))
    updater.start_new(tmp_path / "SignageAgent.exe")
    cmd, kw = started[0]
    assert cmd[1] == "--after-update" and kw["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
