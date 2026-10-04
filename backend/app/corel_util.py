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
import gc
import json
import logging
import os
import re
import subprocess
import threading
import time
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 300.0  # for a COM call that does real work: OpenDocument, SaveAs, ...
QUIT_TIMEOUT_S = 15.0  # for Quit() actually exiting - this is routine async cleanup, not a hang
QUIT_GRACE_S = 3.0  # pool quits: covers the normal ~1.3 s exit, so the next launch never overlaps an exiting instance
QUIT_CALLER_GRACE_S = 0.5  # quit_corel: its caller still holds a reference, so the exit cannot happen yet - do not block it

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
    tracked = _load_tracked_pids()
    # The pooled instance is tracked and running because it is ours and IN USE, not orphaned: killing it here (as this used to)
    # made every reuse of the pool fail with "Object is not connected to server".
    in_use = {_Pool.pid} if _Pool.app is not None and _Pool.pid else set()
    orphaned = (tracked & running) - in_use
    untracked = running - tracked
    if untracked:
        logger.info("CorelDRAW processes running that we did not launch (left alone): %s", sorted(untracked))
    if orphaned:
        logger.warning("found orphaned CorelDRAW instance(s) from a previous run: %s - killing", sorted(orphaned))
        for pid in orphaned:
            force_kill(pid)
    # Keep only the instance in use. Orphans were just killed, and a pid that is no longer running must be dropped too: Windows
    # reuses pid numbers, so a stale entry could later match a designer's own CorelDRAW and get it killed. (This used to save
    # `tracked - running` - i.e. keep exactly the dead pids and drop the live ones, the opposite of the intent.)
    if tracked != (tracked & in_use):
        _save_tracked_pids(tracked & in_use)


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
        app.EventsEnabled = False  # no document/GMS macro event handlers firing (and possibly prompting) during automation
        applied.append("EventsEnabled=False")
    except Exception as e:
        logger.debug("could not set EventsEnabled: %s", e)
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


# ------------------------------------------------------------- which CorelDRAW (version discovery)
#
# The generic ProgID "CorelDRAW.Application" already resolves (HKCR\CorelDRAW.Application\CurVer) to the newest registered
# install - on this dev machine CorelDRAW.Application.27 (2025), next to .21 (2019). What it cannot survive is a stale
# registration, e.g. CurVer still naming a version that was uninstalled. So the candidates are read from the registry - every
# CorelDRAW.Application[.N] whose LocalServer32 executable actually exists on disk - rather than from a hardcoded version list
# (a list goes stale with the next release: the one proposed for this stopped at .25 and would have missed this machine's .27).
# Each candidate is only ever Dispatch()ed when the previous one failed with a "no such server" error, never probed in a loop:
# every successful Dispatch launches a hidden CorelDRAW. SIGNAGE_COREL_PROGID pins one ProgID (e.g. to use the 2019 install).

GENERIC_PROGID = "CorelDRAW.Application"
_PROGID_RE = re.compile(r"^CorelDRAW\.Application(?:\.(\d+))?$", re.IGNORECASE)
# "this server can't be used at all" - try the next candidate at once instead of the 6 s retry meant for a busy instance
_NO_SERVER_HRESULTS = {
    -2147221164,  # REGDB_E_CLASSNOTREG  class not registered
    -2147221005,  # CO_E_CLASSSTRING     invalid class string (ProgID gone)
    -2147221003,  # CO_E_APPNOTFOUND     the server executable was not found
    -2146959355,  # CO_E_SERVER_EXEC_FAILURE
}
connected: dict = {}  # {"progid", "version"} of the last instance this process launched - for logs and job reports


def _server_exe(local_server32: str | None) -> str | None:
    """The executable path from a LocalServer32 value such as '"c:/.../CorelDRW.exe" /Automation' (quoted or not)."""
    if not local_server32:
        return None
    s = local_server32.strip()
    if s.startswith('"'):
        return s[1:].split('"', 1)[0]
    return s.split(" /", 1)[0].strip()


def rank_progids(entries: list[dict], exists=os.path.isfile) -> list[str]:
    """Order registry `entries` [{progid, clsid, server}] into Dispatch candidates: the generic ProgID first, then numbered
    ones newest first; any whose server executable is missing is dropped, and a numbered ProgID that is the SAME server as the
    generic one is skipped (trying it again after the generic one failed would fail the same way)."""
    usable = []
    for e in entries:
        m = _PROGID_RE.match(e.get("progid") or "")
        exe = _server_exe(e.get("server"))
        if m and exe and exists(exe):
            usable.append((int(m.group(1)) if m.group(1) else None, e))
    generic = [e for v, e in usable if v is None]
    generic_clsid = generic[0].get("clsid") if generic else None
    numbered = sorted(((v, e) for v, e in usable if v is not None), key=lambda x: -x[0])
    out = [e["progid"] for e in generic]
    out += [e["progid"] for v, e in numbered if not (generic_clsid and e.get("clsid") == generic_clsid)]
    return out


def _registry_corel_entries() -> list[dict]:
    import winreg

    entries = []
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "") as root:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if not _PROGID_RE.match(name):
                continue
            try:
                clsid = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, name + r"\CLSID")
                server = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32")
            except OSError:
                continue
            entries.append({"progid": name, "clsid": clsid, "server": server})
    return entries


def progid_candidates() -> list[str]:
    """ProgIDs to Dispatch, in order: SIGNAGE_COREL_PROGID alone if set, else what the registry says is installed, else the
    generic ProgID (the old behaviour) if the registry cannot be read."""
    pinned = os.environ.get("SIGNAGE_COREL_PROGID", "").strip()
    if pinned:
        return [pinned]
    try:
        found = rank_progids(_registry_corel_entries())
    except Exception as e:
        logger.warning("could not read CorelDRAW registrations from the registry (%s); using %s", e, GENERIC_PROGID)
        found = []
    return found or [GENERIC_PROGID]


def _exe_version(exe: str) -> str | None:
    """'27.0.0.121' from the executable's version resource (no CorelDRAW launch), or None."""
    try:
        import win32api

        info = win32api.GetFileVersionInfo(exe, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def corel_installs() -> list[dict]:
    """The CorelDRAW installs a job could launch, in the order dispatch_corel tries them: [{progid, exe, version}]. Read from the
    registry and the executables' version resources only - it never starts CorelDRAW, so it is cheap and safe to call while a
    conversion is running (used by GET /api/corel/health)."""
    try:
        entries = {e["progid"]: e for e in _registry_corel_entries()}
    except Exception:
        return []
    out = []
    for progid in progid_candidates():
        e = entries.get(progid)
        exe = _server_exe(e["server"]) if e else None
        if exe and os.path.isfile(exe):
            out.append({"progid": progid, "exe": exe, "version": _exe_version(exe)})
    return out


def _no_server(e: Exception) -> bool:
    return bool(getattr(e, "args", None)) and e.args[0] in _NO_SERVER_HRESULTS


def _dispatch_with_timeout(timeout_s: float = DISPATCH_TIMEOUT_S, progid: str = GENERIC_PROGID):
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
        app = win32com.client.Dispatch(progid)
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
            return win32com.client.GetActiveObject(progid_candidates()[0]), False, None
        except Exception:
            pass

    visible = os.environ.get("SIGNAGE_COREL_VISIBLE") == "1"
    candidates = progid_candidates()
    unusable: set[str] = set()  # ProgIDs whose server cannot start at all - not retried
    last_err = None
    for attempt in range(10):
        live = [p for p in candidates if p not in unusable]
        if not live:
            raise RuntimeError(f"no usable CorelDRAW installation: tried {', '.join(candidates)} ({last_err})")
        progid = live[0]
        before = corel_pids()
        try:
            try:
                app = _dispatch_with_timeout(progid=progid)
            except Exception as e:
                if _no_server(e):
                    unusable.add(progid)
                    last_err = e
                    logger.warning("CorelDRAW ProgID %s cannot be started (%s); trying the next installed version", progid, e)
                    continue  # straight to the next candidate - the 6 s wait below is for a busy instance, not a missing one
                raise
            try:
                connected.update(progid=progid, version=str(app.Version))
            except Exception:
                connected.update(progid=progid, version=None)
            logger.info("launched CorelDRAW %s via %s", connected.get("version"), progid)
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
    # Verified live: CorelDRAW does NOT exit after Quit() while this process still holds a COM reference to it - it sat there until
    # the old 15 s timeout force-killed it, adding ~15 s to EVERY conversion / scene build / export (a 9 s conversion took 25 s).
    # With the reference released it exits ~1.3 s after Quit(). Drop ours now; a caller that still holds one (its own local
    # variable) releases it when its frame ends, so only wait briefly here and let a background reaper finish the job.
    del app
    _finish_quit(pid, timeout, QUIT_CALLER_GRACE_S)


def _finish_quit(pid: int | None, timeout: float | None, grace: float) -> None:
    """Second half of a quit, after dropping our reference: wait up to `grace`, then leave the rest of the wait to a reaper."""
    gc.collect()
    if pid is None:
        return
    if _wait_exit(pid, grace):
        _untrack(pid)
        return
    t = threading.Thread(target=_reap, args=(pid, timeout if timeout is not None else QUIT_TIMEOUT_S), daemon=True)
    _reapers.append(t)
    t.start()


_reapers: list[threading.Thread] = []


def _wait_exit(pid: int, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if pid not in corel_pids():
            return True
        time.sleep(0.2)
    return pid not in corel_pids()


def _reap(pid: int, timeout: float) -> None:
    """Background end of quit_corel: wait for the process to exit (it does once the last COM reference is gone), else force-kill."""
    if not _wait_exit(pid, timeout):
        logger.warning("CorelDRAW (pid %s) did not exit after Quit(); force-killing", pid)
        force_kill(pid)
    _untrack(pid)


def wait_for_pending_quits(timeout: float | None = None) -> None:
    """Block until every background reaper has finished (CorelDRAW exited or was killed, and its pid untracked). The worker calls
    this before exiting - daemon threads die with the process, which would leave a pid tracked that could later be reused."""
    deadline = time.time() + (timeout if timeout is not None else QUIT_TIMEOUT_S + 5)
    while _reapers:
        t = _reapers.pop()
        t.join(max(0.0, deadline - time.time()))


def _quit_pooled() -> None:
    """Quit the pooled instance, releasing the pool's own reference first (see quit_corel for why that matters)."""
    app, pid = _Pool.app, _Pool.pid
    _Pool.app = _Pool.pid = None
    _Pool.jobs_run = 0
    if app is None:
        return
    try:
        app.Quit()
    except Exception:
        pass
    del app  # the pool's reference is already cleared, so this was the last one we hold
    _finish_quit(pid, None, QUIT_GRACE_S)


_com_thread = threading.local()


def ensure_com() -> None:
    """Initialise COM on the calling thread once, and leave it initialised for the thread's lifetime. The instance pool's proxy is
    bound to this thread's apartment and must outlive any single job (so it can be reused, and Quit() at the end still reaches
    CorelDRAW); the apartment is released when the thread or process ends."""
    if getattr(_com_thread, "ready", False):
        return
    import pythoncom

    pythoncom.CoInitialize()
    _com_thread.ready = True


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
        _quit_pooled()
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
        _quit_pooled()
    elif _Pool.pid == pid and _Pool.jobs_run >= recycle_n():
        # used up: quit now rather than leaving an idle hidden CorelDRAW holding RAM until the next job (with the default
        # SIGNAGE_COREL_RECYCLE_N=1 that is after every job - "always fresh", as documented)
        _quit_pooled()


def will_quit_on_release(pid: int | None, success: bool) -> bool:
    """Whether release_instance(pid, success) is about to quit that instance - a document kept open on it must be closed
    first (quitting with an open, modified document could raise a save-changes prompt)."""
    if os.environ.get("SIGNAGE_REUSE_COREL") == "1" or _Pool.pid != pid:
        return False
    return (not success) or _Pool.jobs_run >= recycle_n()


def _quit_pool_at_exit() -> None:
    if _Pool.app is not None:
        logger.info("process exiting; quitting pooled CorelDRAW instance (pid %s)", _Pool.pid)
        _quit_pooled()
    wait_for_pending_quits()  # daemon reapers die with the process - finish them so no pid stays tracked


atexit.register(_quit_pool_at_exit)


class CorelTimeout(Exception):
    """A COM call didn't return within the configured timeout; the launched
    CorelDRAW instance was force-killed. Usually means a blocked modal
    dialog (missing font, colour profile mismatch, unsaved-changes prompt on
    a corrupt file, ...). Rerun with SIGNAGE_COREL_VISIBLE=1 to see it.
    """


# ---------------------------------------------------------------- saving .cdr files in an older format
# The designers' CorelDRAW is 2019 (v21); this server's is newer (27 = CorelDRAW 2025). A plain `doc.SaveAs(path)` writes
# the NEWEST format, which CorelDRAW 2019 refuses to open. So every .cdr we write is saved through a
# StructSaveAsOptions with `Version` = the enum `cdrFileVersion` value 21 (`cdrVersion21`).
# Verified live against CorelDRAW 27.0.121 (see CLAUDE.md "CDR file version"): the file's `content/root.dat` RIFF form type
# changes CDRU -> CDRM and `META-INF/metadata.xml` `Version` 2700 -> 2100, exactly the markers the designers' own
# 2019-compatible files carry. Note the COM factory is `Application.CreateStructSaveAsOptions()` - `CreateSaveOptions`
# does not exist.
DEFAULT_CDR_VERSION = 21


def cdr_target_version() -> int:
    """The .cdr format version to save (env SIGNAGE_CDR_VERSION; default 21 = CorelDRAW 2019; 0 = whatever the running
    CorelDRAW writes, i.e. no down-save)."""
    try:
        return int(os.environ.get("SIGNAGE_CDR_VERSION", DEFAULT_CDR_VERSION))
    except ValueError:
        return DEFAULT_CDR_VERSION


def save_cdr(doc, path, version: int | None = None) -> int:
    """Save `doc` to `path` as a .cdr in the target format (see above; `version` overrides it for this one save, e.g. the
    export dialog's "v27 native" = 0 or "X7" = 17). Returns the `Version` requested (0 = current).

    A CorelDRAW older than the target cannot write the newer format anyway, so it saves as itself (Version 0). A failure to
    build the options object is NOT swallowed into a plain SaveAs: that would silently write the newest format again, which
    is the exact bug this exists to prevent."""
    target = cdr_target_version() if version is None else version
    app = doc.Application
    if target:
        try:
            if int(app.VersionMajor) < target:
                target = 0
        except Exception:
            pass                      # cannot read the version: still ask for `target`
    opts = app.CreateStructSaveAsOptions()
    opts.Version = target
    opts.Overwrite = True             # the default for a bare SaveAs(path, None); a fresh options object defaults to True too
    doc.SaveAs(str(path), opts)
    return target


DEFAULT_MAX_BITMAP_DPI = 300


def max_bitmap_dpi() -> int:
    """`SIGNAGE_MAX_BITMAP_DPI` (default 300): the most pixels per inch an embedded bitmap keeps at its placed size in an
    output .cdr. 0 disables the cap."""
    try:
        return max(0, int(os.environ.get("SIGNAGE_MAX_BITMAP_DPI", DEFAULT_MAX_BITMAP_DPI)))
    except ValueError:
        return DEFAULT_MAX_BITMAP_DPI


def _iter_bitmaps(shapes):
    """Every bitmap shape under `shapes`: inside groups and PowerClips too."""
    for i in range(1, shapes.Count + 1):
        s = shapes.Item(i)
        t = s.Type
        if t == 5:  # cdrBitmapShape
            yield s
        elif t == 7:  # cdrGroupShape
            yield from _iter_bitmaps(s.Shapes)
        try:
            pc = s.PowerClip
        except Exception:
            pc = None
        if pc is not None:
            yield from _iter_bitmaps(pc.Shapes)


def cap_bitmap_resolution(doc, max_dpi: int) -> dict:
    """Downsample every embedded bitmap whose resolution AT ITS PLACED SIZE exceeds `max_dpi` - never upsample.

    Why: a master's photos are sized for the master's page. Shrinking a 125x48 in board to 10x4 in keeps every pixel, so
    its photos end up at 1,250-3,750 dpi and each output .cdr is a 199 MB rewrite of them: measured live, SaveAs 14.1 s /
    208.7 MB for such a board vs 0.8 s / 10.0 MB with the bitmaps capped at 300 dpi (resampling all 5 took 1.5 s). At
    300 dpi of the size it will actually print at, the difference is not visible in print. A full-size board, whose photos
    are at their designed 100-300 dpi, is left exactly as it is.

    Verified live: `Bitmap.Resample` keeps the image's own dpi, i.e. it SHRINKS and moves the shape - so each bitmap's
    placed box (LeftX/BottomY/SizeWidth/SizeHeight) is recorded first and put back after. Rotated or skewed bitmaps are
    skipped (their bounding box is not the image's size). Any single failure leaves that bitmap untouched. Returns
    {"checked", "resampled", "skipped", "pixels_before", "pixels_after"}."""
    stats = {"checked": 0, "resampled": 0, "skipped": 0, "pixels_before": 0, "pixels_after": 0}
    if not max_dpi:
        return stats
    doc.Unit = 3  # cdrMillimeter: SizeWidth/Height below are mm
    for layer in doc.ActivePage.Layers:
        try:
            if layer.IsSpecialLayer:
                continue
        except Exception:
            pass
        for s in _iter_bitmaps(layer.Shapes):
            stats["checked"] += 1
            try:
                if abs(float(s.RotationAngle or 0)) > 1e-6:
                    stats["skipped"] += 1
                    continue
                b = s.Bitmap
                px_w, px_h = int(b.SizeWidth), int(b.SizeHeight)
                x, y, w, h = s.LeftX, s.BottomY, s.SizeWidth, s.SizeHeight
                if w <= 0 or h <= 0 or px_w <= 0 or px_h <= 0:
                    continue
                tw = max(1, round(w / 25.4 * max_dpi))
                th = max(1, round(h / 25.4 * max_dpi))
                if tw >= px_w or th >= px_h:
                    continue  # already at or under the cap on at least one axis: never upsample or distort
                b.Resample(tw, th, True, 0.0, 0.0)
                s.SetSize(w, h)
                s.LeftX, s.BottomY = x, y
                stats["resampled"] += 1
                stats["pixels_before"] += px_w * px_h
                stats["pixels_after"] += int(b.SizeWidth) * int(b.SizeHeight)
            except Exception as e:
                logger.warning("bitmap resolution cap skipped one bitmap: %s", e)
                stats["skipped"] += 1
    return stats


def cdr_file_format(path) -> dict:
    """What a saved .cdr says about its own format, read from the file (a zip for X4+ files): `form` is the RIFF form type
    of `content/root.dat` (b'CDRM' for a 2019-compatible file, b'CDRU' for the newest format), `version` the
    `cdr:CoreVersion` in `META-INF/metadata.xml` (2100 = CorelDRAW 2019). Missing pieces are None; never raises."""
    out = {"form": None, "version": None}
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "content/root.dat" in names:
                head = z.open("content/root.dat").read(12)
                out["form"] = head[8:12].decode("ascii", "replace") if head[:4] == b"RIFF" else None
            if "META-INF/metadata.xml" in names:
                meta = z.read("META-INF/metadata.xml")
                # `cdr:CoreVersion` is what real files carry (2100 = 2019); a bare `Version` tag is accepted too. Never
                # `AppVersion` - that is the CorelDRAW that wrote the file (2700 for 2025) and is not the format.
                m = re.search(rb"CoreVersion>(\d+)<", meta) or re.search(rb"(?<![A-Za-z])Version>(\d+)<", meta)
                out["version"] = int(m.group(1)) if m else None
    except Exception:
        pass
    return out


_CDR_VERSION_NAMES = {21: "CorelDRAW 2019", 17: "CorelDRAW X7"}


def check_cdr_format(path, warnings: list[str], version: int | None = None) -> dict:
    """Read the saved file back (the same verify-don't-trust pattern as fonts) and warn if it is not the requested format.
    `version` is what this save asked for (None = the configured default)."""
    fmt = cdr_file_format(path)
    want = cdr_target_version() if version is None else version
    name = _CDR_VERSION_NAMES.get(want, f"CorelDRAW v{want}")
    if want and fmt["version"] is not None and fmt["version"] != want * 100:
        warnings.append(f"The saved .cdr says it is format version {fmt['version']} but {want * 100} ({name}) was "
                        f"requested - it may not open in {name}")
    elif want and fmt["version"] is None:
        warnings.append(f"Could not read the saved .cdr's format version back, so {name} compatibility is unverified")
    return {**fmt, "requested": want}


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


# Fonts already verified (see CLAUDE.md "Shop name replacement") to actually
# carry Tamil glyphs and stick when written via COM on this machine - not the
# full installed-font list (querying that per shape would mean a PowerShell
# round-trip per text object), just the ones this codebase already trusts.
_KNOWN_TAMIL_FONTS = {"nirmala ui", "nirmala text"}
_tamil_capable: dict[str, bool] = {}


def font_renders_tamil(name: str | None) -> bool:
    """True when the installed font `name` really contains Unicode Tamil (read from its file's character map, fonts.py), so
    Tamil text set in it prints correctly and the master's choice can be kept. False for fonts without Tamil (e.g. "Arial",
    which prints tofu boxes unless Windows font-linking happens to step in) and for fonts that are not installed."""
    key = (name or "").strip().lower()
    if not key:
        return False
    if key in _KNOWN_TAMIL_FONTS:
        return True
    if key not in _tamil_capable:
        ok = False
        try:
            from . import fonts
            path = fonts.font_file(name)
            ok = bool(path and fonts.font_has_tamil(str(path)))
        except Exception:
            ok = False
        _tamil_capable[key] = ok
    return _tamil_capable[key]


def ensure_tamil_font_renders(shape, text: str | None, warnings: list[str]) -> None:
    """Real masters' own PRE-EXISTING Tamil text (the shop-name/footer lines
    untouched by shop-name/contact replacement - e.g. a v2-UI job that never
    passes shop_name_local, or a PowerClip child's text edited directly in the
    editor) is typically tagged "Arial" in its own COM Font property, relying
    on Windows' automatic font-linking to silently substitute a real
    Tamil-capable font at render time (see CLAUDE.md "Shop name replacement" -
    every cached real dalmia file's Tamil content reports font="Arial" this
    way). That substitution is a Windows/renderer behavior, not a CorelDRAW
    guarantee, and confirmed NOT to happen for this export path on this
    machine: a real Agarpathi job's untouched Tamil shopname text (job
    43ddf0d9e704 / shop 638555963892) rendered as tofu boxes in both the
    exported PNG and the editor's scene-export slice, despite compute_layout
    never touching that shape's text or font because no replacement was
    requested for it.

    Shared by app.engines.CorelEngine (every text shape read off the page
    during generation) and app.export_replay.Replayer (a `text` op's target,
    including one nested inside a PowerClip - see scene_ops.py's narrow
    PowerClip-text exception) - one place to get this fix right for both.

    Fixes this the same verified way replacement text already is: if a
    shape's own text is Tamil and its current font isn't already one this
    codebase has confirmed renders Tamil correctly, force it to
    `layout.TAMIL_FONT` and read the font back to confirm the write actually
    stuck - CorelDRAW does not raise on an unrecognized font name, it
    silently keeps the old one, so trusting the call without reading back
    would hide exactly this failure mode again.
    """
    from .layout import TAMIL_FONT, is_tamil  # local import: layout.py has no COM dependency of its own

    if not is_tamil(text):
        return
    try:
        story = shape.Text.Story
        current = (story.Font or "").strip().lower()
        # the master's own font is kept whenever it can draw Tamil (Nirmala, Latha, Arima Madurai, ...) - only a font that
        # cannot (Arial & co., or one not installed here) is replaced, because it would print tofu boxes
        if font_renders_tamil(story.Font):
            return
        story.Font = TAMIL_FONT
        if story.Font != TAMIL_FONT:
            warnings.append(
                f"Tamil text still tagged {current!r} after trying to set {TAMIL_FONT!r} "
                f"- it may render as tofu boxes; check the font is installed"
            )
    except Exception as e:
        warnings.append(f"could not verify/fix Tamil font on a text shape: {e}")


def cdr_core_version(path) -> float | None:
    """The CorelDRAW version a .cdr was saved by (X4+ files are zip archives: META-INF/metadata.xml `CoreVersion`, e.g. 2510 ->
    25.10, 2100 -> 21.0). None for anything unreadable (an older, pre-X4 file, a damaged zip)."""
    import re
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            m = re.search(r"CoreVersion>\s*(\d+)", z.read("META-INF/metadata.xml").decode("utf-8", "ignore"))
        return int(m.group(1)) / 100 if m else None
    except Exception:
        return None


def check_file_not_newer(path, app) -> None:
    """Raise a clear error when `path` was saved by a newer CorelDRAW than the running one - OpenDocument would only say
    'Failed to open document' (seen with the Hangyo files: saved by CorelDRAW 2024, v25.10, opened with 2019, v21)."""
    saved = cdr_core_version(path)
    if saved is None:
        return
    try:
        running = float(app.VersionMajor)
    except Exception:
        return
    if int(saved) > int(running):
        raise RuntimeError(
            f"this file was saved by CorelDRAW version {saved:g} but the CorelDRAW on this machine is version {running:g}, "
            "which cannot open files from a newer version - open it in the newer CorelDRAW and 'Save As' an older version "
            f"(v{int(running)} or lower), or install a CorelDRAW that is at least version {int(saved)}")
