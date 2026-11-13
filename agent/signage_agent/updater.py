"""Self-update: download the new exe, verify it, swap it in while idle, restart, and roll back if the new one cannot reach the site.

A running exe cannot be overwritten on Windows but it CAN be renamed, so the swap is: SignageAgent.exe -> SignageAgent.old.exe,
SignageAgent.new.exe -> SignageAgent.exe, start the new one, exit. `update_pending.json` records the swap; the new process deletes it (and
the old exe) after its first good heartbeat, or - when it has not had one within GRACE_S - puts the old exe back and starts it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import client

log = logging.getLogger("agent.update")
GRACE_S = 120
DETACHED = 0x00000008 | 0x00000200       # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def version_key(v: str | None) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")) or (0,)


def exe_path() -> Path | None:
    """The running exe, or None when running from source (an update then only reports)."""
    return Path(sys.executable) if getattr(sys, "frozen", False) else None


def _pending(home: Path) -> Path:
    return home / "update_pending.json"


def _skip_file(home: Path) -> Path:
    return home / "update_skip.json"


def _skipped(home: Path) -> str | None:
    """The version a rollback gave up on (not tried again until a newer one is published)."""
    try:
        return json.loads(_skip_file(home).read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None


def fresh_env() -> dict:
    """The environment for starting the agent exe as a NEW app (after an update, after installing): without
    PYINSTALLER_RESET_ENVIRONMENT=1 the new exe inherits this process's PyInstaller variables and its own CorelDRAW workers hung."""
    return {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}


def start_new(exe: Path) -> None:
    subprocess.Popen([str(exe), "--after-update"], close_fds=True, env=fresh_env(),  # noqa: S603
                     creationflags=DETACHED if sys.platform == "win32" else 0)


def check_and_apply(server: str, token: str, current: str, home: Path, *, idle: bool, force: bool = False, swap=None) -> str:
    """One update check. Returns 'current' | 'available' | 'busy' | 'updated' | 'failed: why'. `swap` is injectable for tests."""
    try:
        latest = client.latest_version(server)
    except client.ApiError as e:
        return f"failed: {e.detail}"
    newer = version_key(latest["version"]) > version_key(current)
    if not newer:
        return "current"
    if _skipped(home) == latest["version"]:
        return "failed: this version was installed before and could not reach the site; waiting for a newer one"
    exe = exe_path()
    if exe is None:
        return "available"
    if not idle and not force:
        return "busy"
    new = exe.with_name("SignageAgent.new.exe")
    try:
        client.download_exe(server, token, new, latest["sha256"], latest.get("size"))
    except client.ApiError as e:
        return f"failed: {e.detail}"
    except OSError as e:
        return f"failed: {e}"
    try:
        (swap or swap_in)(exe, new, home, previous_version=current, target_version=latest["version"])
    except OSError as e:
        new.unlink(missing_ok=True)
        return f"failed: {e}"
    return "updated"


def swap_in(exe: Path, new: Path, home: Path, previous_version: str, target_version: str = "") -> None:
    old = exe.with_name("SignageAgent.old.exe")
    try:
        old.unlink(missing_ok=True)
    except OSError:               # an older copy is still running from it (seen live after a restart that kept its launcher alive)
        old = exe.with_name(f"SignageAgent.old-{int(time.time())}.exe")
    exe.rename(old)
    try:
        new.rename(exe)
    except OSError:
        old.rename(exe)           # put the working one back
        raise
    _pending(home).write_text(json.dumps({"old": str(old), "previous_version": previous_version, "target": target_version, "since": time.time()}), encoding="utf-8")
    log.info("updated %s -> new exe in place; restarting", previous_version)
    start_new(exe)
    os._exit(0)                   # the new process takes over


def confirm_after_update(home: Path) -> None:
    """Called after the first good heartbeat: the update worked, so the old exe is not needed any more."""
    p = _pending(home)
    if not p.exists():
        return
    try:
        old = Path(json.loads(p.read_text(encoding="utf-8"))["old"])
        old.unlink(missing_ok=True)
    except (OSError, ValueError, KeyError):
        pass
    p.unlink(missing_ok=True)
    log.info("update confirmed")


def cleanup_leftovers(home: Path) -> None:
    """Delete exes an update or rollback left behind. The old process may still hold its file for a moment after the swap, so this
    runs after every good heartbeat until nothing is left. Never touches them while an update is waiting to be confirmed."""
    exe = exe_path()
    if exe is None or _pending(home).exists():
        return
    leftovers = [exe.with_name(n) for n in ("SignageAgent.old.exe", "SignageAgent.bad.exe", "SignageAgent.new.exe")]
    for f in leftovers + sorted(exe.parent.glob("SignageAgent.old-*.exe")):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass                      # still in use: next heartbeat


def rollback_due(home: Path, now: float | None = None) -> bool:
    """True when an update is pending and the grace period has run out without a good heartbeat."""
    try:
        since = json.loads(_pending(home).read_text(encoding="utf-8"))["since"]
    except (OSError, ValueError, KeyError):
        return False
    return (now or time.time()) - since > GRACE_S


def rollback(home: Path) -> bool:
    """Put the previous exe back and start it. Returns False when there is nothing to roll back to."""
    exe = exe_path()
    p = _pending(home)
    try:
        info = json.loads(p.read_text(encoding="utf-8"))
        old = Path(info["old"])
    except (OSError, ValueError, KeyError):
        return False
    if exe is None or not old.exists():
        p.unlink(missing_ok=True)
        return False
    bad = exe.with_name("SignageAgent.bad.exe")
    bad.unlink(missing_ok=True)
    exe.rename(bad)
    old.rename(exe)
    p.unlink(missing_ok=True)
    if info.get("target"):
        _skip_file(home).write_text(json.dumps({"version": info["target"]}), encoding="utf-8")
    log.error("the updated agent could not reach the site - rolled back")
    start_new(exe)
    os._exit(0)
