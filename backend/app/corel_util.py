"""Shared CorelDRAW COM plumbing for CorelEngine and the dev dump tool.

Handles the parts that are easy to get wrong when automating CorelDRAW
unattended (see CLAUDE.md "CorelEngine COM notes" for how these were found):

- launching vs. reusing an instance, and always cleaning up what we launch
- a hard per-call timeout so a blocked modal dialog (missing font, color
  profile mismatch, ...) fails the job loudly instead of hanging forever
- best-effort suppression of the dialogs that cause that hang in the first
  place, with a log of what was actually set (CorelDRAW doesn't always
  raise when a setting isn't supported by the installed version)

Env vars:
    SIGNAGE_REUSE_COREL=1    dev only: attach to an already-running CorelDRAW
                             (e.g. one you have open) instead of launching a
                             fresh instance. Never used in production - an
                             automated job must never touch a designer's own
                             session, so this instance is also never Quit()-ed,
                             tracked for orphan cleanup, or pooled/recycled.
    SIGNAGE_COREL_VISIBLE=1  launch CorelDRAW visibly, so a blocking dialog
                             can actually be seen (and clicked, or at least
                             screenshotted) instead of hanging invisibly.
    SIGNAGE_COREL_TIMEOUT_S  seconds to wait for a single COM call (Open,
                             SaveAs, PublishToPDF, ExportBitmap, ...) before
                             force-killing the launched instance. Default 300.
    SIGNAGE_COREL_RECYCLE_N  how many jobs to run on one launched instance
                             before quitting it and launching fresh. Default
                             1 (a brand-new instance per job - the safe
                             default; only raise this once reuse-across-jobs
                             has actually been validated in production).
    SIGNAGE_DATA             base data directory; also where the launched
                             -PID tracking file lives (see cleanup_orphans).
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 300.0  # for a COM call that does real work: OpenDocument, SaveAs, ...
QUIT_TIMEOUT_S = 15.0  # for Quit() actually exiting - this is routine async cleanup, not a hang

_DATA_DIR = Path(os.environ.get("SIGNAGE_DATA", Path(__file__).resolve().parents[1] / "data"))
PID_FILE = _DATA_DIR / "corel_launched_pids.json"


def timeout_s() -> float:
    return float(os.environ.get("SIGNAGE_COREL_TIMEOUT_S", DEFAULT_TIMEOUT_S))


def recycle_n() -> int:
    return max(1, int(os.environ.get("SIGNAGE_COREL_RECYCLE_N", 1)))


def check_memory(warn_below_gb: float = 2.0) -> float:
    """Logs current free RAM and returns it in GB. Callers that also want
    to surface a warning in their own job-level output (not just the
    process-wide logger) should compare the returned value themselves.
    """
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    free_gb = stat.ullAvailPhys / (1024 ** 3)
    logger.info("free RAM: %.2f GB (%d%% used)", free_gb, stat.dwMemoryLoad)
    if free_gb < warn_below_gb:
        logger.warning("free RAM (%.2f GB) is below %.1f GB - CorelDRAW may be slow or unstable", free_gb, warn_below_gb)
    return free_gb


def corel_pids() -> set[int]:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq CorelDRW.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        fields = line.strip().strip('"').split('","')
        if len(fields) >= 2 and fields[0].lower() == "coreldrw.exe":
            try:
                pids.add(int(fields[1]))
            except ValueError:
                pass
    return pids


def force_kill(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
    except Exception:
        pass


def _load_tracked_pids() -> set[int]:
    try:
        return set(json.loads(PID_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_tracked_pids(pids: set[int]) -> None:
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(json.dumps(sorted(pids)), encoding="utf-8")
    except Exception as e:
        logger.debug("could not write %s: %s", PID_FILE, e)


def _track_launched(pid: int | None) -> None:
    if pid is None:
        return
    pids = _load_tracked_pids()
    pids.add(pid)
    _save_tracked_pids(pids)


def _untrack(pid: int | None) -> None:
    if pid is None:
        return
    pids = _load_tracked_pids()
    if pid in pids:
        pids.discard(pid)
        _save_tracked_pids(pids)


def cleanup_orphaned_instances() -> None:
    """Call at the start of a job: log every CorelDRW.exe currently running,
    and kill only the ones *we* launched in a previous run and never
    cleaned up (e.g. the process crashed before reaching quit_corel) - never
    a PID we didn't launch ourselves, which could be a designer's own open
    CorelDRAW. Safe to call even if nothing is tracked or running.
    """
    running = corel_pids()
    if not running:
        return
    tracked = _load_tracked_pids()
    orphaned = tracked & running
    untracked = running - tracked
    if untracked:
        logger.info("CorelDRAW processes running that we did not launch (left alone): %s", sorted(untracked))
    if orphaned:
        logger.warning("found orphaned CorelDRAW instance(s) from a previous run: %s - killing", sorted(orphaned))
        for pid in orphaned:
            force_kill(pid)
    # drop anything no longer running (exited normally without reaching _untrack) too
    _save_tracked_pids(tracked - running)


def _suppress_prompts(app) -> None:
    """Best-effort: disable the dialogs most likely to block automation
    (missing-font substitution, colour-profile mismatch on open). Each
    setting is independent and logged, since older/newer CorelDRAW builds
    don't all expose the same properties and a missing one shouldn't stop
    the others from being applied.
    """
    applied = []
    try:
        app.Optimization = True  # skip screen redraws/other interactive-only work
        applied.append("Optimization=True")
    except Exception as e:
        logger.debug("could not set Optimization: %s", e)
    try:
        app.PanoseMatching = 1  # cdrPanoseTemporary: substitute missing fonts silently
        applied.append("PanoseMatching=Temporary")
    except Exception as e:
        logger.debug("could not set PanoseMatching: %s", e)
    for policy_name in ("PolicyForOpen", "PolicyForImport"):
        try:
            policy = getattr(app.ColorManager, policy_name)
            policy.WarnOnMismatchedProfiles = False
            policy.WarnOnMissingProfiles = False
            applied.append(f"ColorManager.{policy_name}.WarnOn*=False")
        except Exception as e:
            logger.debug("could not configure ColorManager.%s: %s", policy_name, e)
    logger.info("CorelDRAW prompt suppression applied: %s", ", ".join(applied) or "(none - COM members not found)")


DISPATCH_TIMEOUT_S = 45.0  # win32com.client.Dispatch() itself has no PID to kill by upfront


def _dispatch_with_timeout(timeout_s: float = DISPATCH_TIMEOUT_S):
    """`win32com.client.Dispatch("CorelDRAW.Application")` has no PID to
    force-kill by until it returns - unlike every other COM call here, which
    already has a launched PID. Observed live: this call itself hung, with
    no exception and no timeout, for 30+ minutes, taking the whole Python
    process to "Not Responding" (nothing after it - including this
    function's own retry loop - ever got a chance to run). This still
    protects against that: a background timer watches for a *new*
    CorelDRW.exe appearing and kills it if Dispatch hasn't returned by the
    deadline, which breaks the pending RPC handshake and unblocks the call
    (COM/Windows calls release the GIL while blocked, so the timer thread
    runs fine even though the main thread is stuck inside Dispatch).
    """
    import win32com.client

    before = corel_pids()
    timed_out = threading.Event()

    def _on_timeout():
        timed_out.set()
        for pid in corel_pids() - before:
            logger.warning("Dispatch() exceeded %.0fs with no return; killing new pid %s", timeout_s, pid)
            force_kill(pid)

    timer = threading.Timer(timeout_s, _on_timeout)
    timer.daemon = True
    timer.start()
    try:
        app = win32com.client.Dispatch("CorelDRAW.Application")
    except Exception:
        if timed_out.is_set():
            raise CorelTimeout(f"Dispatch() did not respond within {timeout_s:.0f}s; instance force-killed") from None
        raise
    finally:
        timer.cancel()
    if timed_out.is_set():
        raise CorelTimeout(f"Dispatch() did not respond within {timeout_s:.0f}s; instance force-killed")
    return app


def dispatch_corel() -> tuple[object, bool, int | None]:
    """Returns (app, we_launched_it, launched_pid)."""
    import win32com.client

    if os.environ.get("SIGNAGE_REUSE_COREL") == "1":
        try:
            return win32com.client.GetActiveObject("CorelDRAW.Application"), False, None
        except Exception:
            pass

    visible = os.environ.get("SIGNAGE_COREL_VISIBLE") == "1"
    last_err = None
    for attempt in range(10):
        before = corel_pids()
        try:
            app = _dispatch_with_timeout()
            # Right after a previous instance quits, a fresh Dispatch can take a
            # few seconds to become interactive; setting .Visible too early raises
            # "Property ... can not be set." Retry briefly rather than failing.
            app.Visible = visible
            pid = next(iter(corel_pids() - before), None)
            _suppress_prompts(app)
            _track_launched(pid)
            return app, True, pid
        except Exception as e:
            last_err = e
            # Dispatch can spawn the process even though the .Visible set
            # right after it fails, leaving a half-initialized instance that
            # was never tracked or killed - left alone, a later GetActiveObject
            # -style lookup or even plain Dispatch can hand this same broken
            # instance back on the next attempt, so every retry fails the same
            # way instead of getting a clean process. Kill it before retrying.
            new_pid = next(iter(corel_pids() - before), None)
            if new_pid is not None:
                logger.warning("Dispatch attempt %d left a half-initialized instance (pid %s); killing it", attempt + 1, new_pid)
                force_kill(new_pid)
            time.sleep(6)
    raise last_err


def quit_corel(app, we_launched_it: bool, pid: int | None, timeout: float | None = None) -> None:
    """Quit an instance we launched and make sure the process is actually
    gone. Quit() is asynchronous, so this polls rather than trusting the
    call to have finished the job. Never touches a reused instance.
    """
    if not we_launched_it:
        return
    try:
        app.Quit()
    except Exception:
        pass
    if pid is None:
        return
    deadline = time.time() + (timeout if timeout is not None else QUIT_TIMEOUT_S)
    while time.time() < deadline:
        if pid not in corel_pids():
            _untrack(pid)
            return
        time.sleep(0.5)
    logger.warning("CorelDRAW (pid %s) did not exit after Quit(); force-killing", pid)
    force_kill(pid)
    _untrack(pid)


class _Pool:
    """Module-level so it's shared across CorelEngine instances within one
    server process (main.py makes a fresh CorelEngine() per job, but they
    all run in the same single-worker process). SIGNAGE_REUSE_COREL bypasses
    this entirely - that instance is never pooled, tracked or recycled.
    """
    app = None
    pid: int | None = None
    jobs_run = 0


def acquire_instance() -> tuple[object, bool, int | None]:
    """Get a CorelDRAW instance for one job: reuses the pooled instance if
    under SIGNAGE_COREL_RECYCLE_N jobs old, otherwise quits it (if any) and
    launches fresh. Pair with release_instance() when the job is done.
    """
    if os.environ.get("SIGNAGE_REUSE_COREL") == "1":
        return dispatch_corel()

    n = recycle_n()
    if _Pool.app is not None and _Pool.jobs_run < n:
        _Pool.jobs_run += 1
        logger.info("reusing pooled CorelDRAW instance (pid %s, job %d/%d)", _Pool.pid, _Pool.jobs_run, n)
        return _Pool.app, True, _Pool.pid

    if _Pool.app is not None:
        quit_corel(_Pool.app, True, _Pool.pid)
    app, launched, pid = dispatch_corel()
    _Pool.app, _Pool.pid, _Pool.jobs_run = app, pid, 1
    return app, launched, pid


def release_instance(pid: int | None, success: bool) -> None:
    """Call after a job finishes. A failed job's instance is never reused -
    whatever went wrong (timeout, force-kill, unknown COM state) makes it
    unsafe to trust for the next job, regardless of SIGNAGE_COREL_RECYCLE_N.
    """
    if os.environ.get("SIGNAGE_REUSE_COREL") == "1":
        return
    if not success and _Pool.pid == pid:
        logger.warning("job failed on pooled instance (pid %s); discarding it instead of reusing", pid)
        quit_corel(_Pool.app, True, _Pool.pid)
        _Pool.app = _Pool.pid = None
        _Pool.jobs_run = 0


def _quit_pool_at_exit() -> None:
    if _Pool.app is not None:
        logger.info("process exiting; quitting pooled CorelDRAW instance (pid %s)", _Pool.pid)
        quit_corel(_Pool.app, True, _Pool.pid)


atexit.register(_quit_pool_at_exit)


class CorelTimeout(Exception):
    """A COM call didn't return within the configured timeout; the launched
    CorelDRAW instance was force-killed. Usually means a blocked modal
    dialog (missing font, colour profile mismatch, unsaved-changes prompt on
    a corrupt file, ...). Rerun with SIGNAGE_COREL_VISIBLE=1 to see it.
    """


def run_with_timeout(fn, pid: int | None, op_name: str, timeout: float | None = None):
    """Run a blocking COM call, killing the launched instance (by PID, from
    outside any COM apartment - safe to do from another thread, unlike
    calling COM methods cross-thread) if it doesn't return in time.

    If `fn` is genuinely stuck behind a modal dialog, killing the process
    usually breaks the RPC channel and `fn` raises shortly after; in the
    rare case the underlying call still never returns, this function itself
    will still block (Python cannot safely force-interrupt an arbitrary
    blocked native call) - the timer guarantees the *process* is killed
    promptly, not that this call returns promptly.
    """
    t = timeout if timeout is not None else timeout_s()
    timed_out = threading.Event()

    def _on_timeout():
        timed_out.set()
        logger.warning("CorelDRAW %s exceeded %.0fs; force-killing pid %s", op_name, t, pid)
        if pid is not None:
            force_kill(pid)

    timer = threading.Timer(t, _on_timeout)
    timer.daemon = True
    timer.start()
    try:
        result = fn()
    except Exception:
        if timed_out.is_set():
            raise CorelTimeout(f"{op_name} did not respond within {t:.0f}s; instance force-killed") from None
        raise
    finally:
        timer.cancel()
    if timed_out.is_set():
        raise CorelTimeout(f"{op_name} did not respond within {t:.0f}s; instance force-killed")
    return result
