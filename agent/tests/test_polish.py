"""The tray icon's pure parts, the signing command, and the start-with-Windows entry (on a scratch registry key that is removed again)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from signage_agent import installer, tray


def _status(**kw):
    return {"configured": True, "paired": True, "connected": True, "state": "idle", "corel": [{"major": 27}], **kw}


def test_the_menu_line_says_where_the_agent_stands():
    assert tray.status_text(_status()) == "Connected - CorelDRAW 27"
    assert tray.status_text(_status(corel=[])) == "Connected - CorelDRAW not found on this PC"
    assert tray.status_text(_status(state="busy")) == "Working on a board..."
    assert tray.status_text(_status(connected=False)) == "Reconnecting to the website..."
    assert "Connect this PC" in tray.status_text(_status(paired=False, connected=False))
    assert "config.json" in tray.status_text(_status(configured=False))


def test_the_icon_dot_is_green_amber_or_red():
    assert tray.light(_status()) == "green"
    assert tray.light(_status(state="busy")) == "amber"
    assert tray.light(_status(connected=False)) == "amber"
    assert tray.light(_status(paired=False, connected=False)) == "red"


def test_the_icon_image_is_drawn_for_every_colour():
    for color in tray.COLORS:
        im = tray._image(color)
        assert im.size == (64, 64) and im.getpixel((50, 50))[:3] == tray.COLORS[color]


def test_removal_only_ever_deletes_the_agents_own_folder(tmp_path):
    ours = tmp_path / "AdinnSignageAgent"
    cmd = tray.removal_command(ours)
    assert cmd[:2] == ["cmd", "/c"] and f'rmdir /s /q "{ours}"' in cmd[2]
    assert tray.removal_command(tmp_path / "Documents") is None                     # anything else: refused
    assert tray.removal_command(Path("C:/")) is None


@pytest.mark.skipif(sys.platform != "win32", reason="the start-with-Windows entry is a Windows registry value")
def test_autostart_is_written_and_removed_in_the_registry(monkeypatch, tmp_path):
    import winreg

    scratch = r"Software\AdinnSignageAgentTest\Run"
    monkeypatch.setenv("SIGNAGE_AGENT_RUN_KEY", scratch)
    exe = tmp_path / "SignageAgent.exe"
    try:
        installer.set_autostart(exe)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, scratch) as k:
            value, kind = winreg.QueryValueEx(k, installer.RUN_VALUE)
        assert value == f'"{exe}"' and kind == winreg.REG_SZ                         # quoted: a path with spaces still starts
        installer.set_autostart(exe)                                                 # again: still one entry
        installer.remove_autostart()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, scratch) as k, pytest.raises(FileNotFoundError):
            winreg.QueryValueEx(k, installer.RUN_VALUE)
        installer.remove_autostart()                                                 # nothing there: no error
    finally:
        for key in (scratch, r"Software\AdinnSignageAgentTest"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass


@pytest.mark.skipif(sys.platform != "win32", reason="the real Run key is Windows only")
def test_the_real_run_key_is_the_default(monkeypatch):
    monkeypatch.delenv("SIGNAGE_AGENT_RUN_KEY", raising=False)
    assert installer.run_key() == r"Software\Microsoft\Windows\CurrentVersion\Run"


def _build():
    spec = importlib.util.spec_from_file_location("agent_build", Path(__file__).resolve().parents[1] / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_signing_command_has_a_timestamp_and_sha256(tmp_path):
    b = _build()
    cmd = b.sign_command("signtool.exe", tmp_path / "SignageAgent.exe", "c.pfx", "secret")
    assert cmd[:2] == ["signtool.exe", "sign"] and "/tr" in cmd and "sha256" in cmd and cmd[cmd.index("/f") + 1] == "c.pfx"
    assert cmd[-1].endswith("SignageAgent.exe") and cmd[cmd.index("/tr") + 1] == b.DEFAULT_TIMESTAMP


def test_signing_explains_what_is_missing(tmp_path):
    b = _build()
    with pytest.raises(SystemExit, match="SIGNAGE_SIGN_PFX"):
        b.sign(tmp_path / "x.exe", env={})
    pfx = tmp_path / "c.pfx"
    pfx.write_bytes(b"x")
    with pytest.raises(SystemExit, match="signtool"):
        b.sign(tmp_path / "x.exe", env={"SIGNAGE_SIGN_PFX": str(pfx), "ProgramFiles(x86)": str(tmp_path), "ProgramFiles": str(tmp_path)})


def test_signtool_is_found_from_the_environment_or_the_sdk(tmp_path):
    b = _build()
    tool = tmp_path / "signtool.exe"
    tool.write_bytes(b"x")
    assert b.find_signtool({"SIGNTOOL": str(tool)}) == str(tool)
    sdk = tmp_path / "Windows Kits" / "10" / "bin"
    for v in ("10.0.19041.0", "10.0.22621.0"):
        (sdk / v / "x64").mkdir(parents=True)
        (sdk / v / "x64" / "signtool.exe").write_bytes(b"x")
    found = b.find_signtool({"ProgramFiles(x86)": str(tmp_path), "ProgramFiles": str(tmp_path / "nothing")})
    assert "10.0.22621.0" in found                                                   # the newest SDK
