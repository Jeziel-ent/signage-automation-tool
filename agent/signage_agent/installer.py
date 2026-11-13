"""Install the agent for the current Windows user (no administrator rights): copy the exe + config.json to
%LOCALAPPDATA%\\AdinnSignageAgent, start with Windows (HKCU Run key) and run once. Double-clicking the downloaded exe does all of it."""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import APP_DIR_NAME, install_dir

log = logging.getLogger("agent.install")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AdinnSignageAgent"


def run_key() -> str:
    """Where the start-with-Windows entry lives (the real Run key). SIGNAGE_AGENT_RUN_KEY points the tests at a scratch key instead."""
    return os.environ.get("SIGNAGE_AGENT_RUN_KEY") or RUN_KEY
EXE_NAME = "SignageAgent.exe"
DETACHED = 0x00000008 | 0x00000200


def _test_mode() -> bool:
    """SIGNAGE_AGENT_HOME set = development / tests: no message boxes and no start-with-Windows registry entry."""
    return bool(os.environ.get("SIGNAGE_AGENT_HOME"))


def message(text: str, title: str = "Adinn Signage Agent", error: bool = False) -> None:
    if sys.platform == "win32" and not _test_mode():
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
    else:
        print(f"{title}: {text}")


def single_instance(name: str | None = None) -> bool:
    """True when this is the only running agent for this install folder (a named mutex, kept alive by the handle we leak on purpose).
    The folder is part of the name so a development copy never collides with the installed one."""
    if sys.platform != "win32":
        return True
    import hashlib

    name = name or "Local\\AdinnSignageAgent-" + hashlib.sha1(str(install_dir().resolve()).lower().encode()).hexdigest()[:8]
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    already = ctypes.windll.kernel32.GetLastError() == 183       # ERROR_ALREADY_EXISTS
    if not already:
        single_instance._handle = handle                          # type: ignore[attr-defined]
    return not already


def is_installed_copy() -> bool:
    return Path(sys.executable).resolve().parent == install_dir().resolve()


def set_autostart(exe: Path) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key()) as k:
        winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, f'"{exe}"')


def remove_autostart() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key(), 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_VALUE)
    except OSError:
        pass


def install_and_launch() -> bool:
    """Called when the downloaded exe is double-clicked from anywhere but the install folder. True when the installed copy was started
    (the caller then exits). Returns False (after telling the user why) when it could not install."""
    src = Path(sys.executable)
    cfg_src = src.with_name("config.json")
    if not cfg_src.is_file():
        message("config.json was not found next to SignageAgent.exe.\n\nExtract the WHOLE downloaded zip into a folder "
                "(do not run the exe from inside the zip) and start it again.", error=True)
        return False
    dest = install_dir()
    try:
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / EXE_NAME
        if target.exists() and _in_use(target):          # an installed agent is already running: it updates itself
            message("The Signage Agent is already installed and running on this computer.\n\nGo back to the website.")
            return True
        try:
            shutil.copy2(src, target)
        except PermissionError:
            if target.exists() and _in_use(target):      # it started running between the check and the copy
                message("The Signage Agent is already installed and running on this computer.\n\nGo back to the website.")
                return True
            raise
        shutil.copy2(cfg_src, dest / "config.json")
        if not _test_mode():
            set_autostart(target)
    except OSError as e:
        message(f"Could not install the agent: {e}", error=True)
        return False
    from .updater import fresh_env
    subprocess.Popen([str(target), "--installed"], close_fds=True, env=fresh_env(), creationflags=DETACHED)  # noqa: S603
    message("The Signage Agent is installed and running.\n\nGo back to the website - it will detect the agent and ask you to "
            "connect this PC.")
    return True


def _in_use(path: Path) -> bool:
    """True when Windows will not let us overwrite `path` because the exe is running. Renaming a file onto itself is NOT a test:
    Windows allows renaming a running exe, so the old check said "free" and the copy then failed with Permission denied
    (seen on a PC where the agent was already installed and running). Opening it for writing fails while it runs."""
    try:
        with open(path, "r+b"):
            return False
    except OSError:
        return True


def uninstall() -> None:
    remove_autostart()
    log.info("autostart removed; delete %s to remove the rest", install_dir())
