"""Runs a batch of CorelDRAW jobs in a separate OS process (corel_worker.py)
so a hang anywhere - even inside win32com.client.Dispatch() itself, which
has no Python-level timeout hook of its own - can be recovered by killing
the whole process from outside. This matters because a hung process can
never reliably do that to itself: observed live, the process running this
code went "Not Responding" for 30+ minutes while stuck inside Dispatch(),
with nothing after that call ever getting a chance to run (see
docs/corel-save-hang-diagnosis.md and CLAUDE.md "Production hardening").

corel_util.py's in-process fixes (per-step run_with_timeout, and now a
timeout around Dispatch() itself) still apply *inside* the worker and stay
the first line of defense; this is the outer safety net for anything that
gets past them.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from . import corel_util

logger = logging.getLogger(__name__)

DEFAULT_OVERALL_TIMEOUT_S = 600.0  # 10 minutes of *no progress* (any heartbeat/job completion resets this)
POLL_INTERVAL_S = 1.0
MIN_FREE_RAM_GB = 1.5


class RefusedToStart(Exception):
    """Raised instead of starting a batch when free RAM is too low."""


SHOP_TIMEOUT_DEFAULT_S = 45.0
# Steps that are not the shop's own work: the worker's "starting" beat and launching CorelDRAW (which has its own 45 s
# Dispatch timeout, corel_util._dispatch_with_timeout). A job's clock starts at its first beat past these.
_UNTIMED_STEPS = ("starting", "launch")


def shop_timeout_s() -> float:
    """Per-shop conversion time limit: SIGNAGE_SHOP_TIMEOUT_S, default 45 s; 0 or less turns it off."""
    import os
    try:
        return float(os.environ.get("SIGNAGE_SHOP_TIMEOUT_S", SHOP_TIMEOUT_DEFAULT_S))
    except ValueError:
        return SHOP_TIMEOUT_DEFAULT_S


def run_batch(jobs: list[dict], results_path: Path, overall_timeout_s: float | None = None,
              worker_module: str = "app.corel_worker", skip_memory_check: bool = False,
              on_progress=None, job_timeout_s: float | None = None) -> list[dict]:
    """jobs: list of {"master_path", "shop", "out_dir"} (JSON-safe strings).
    Returns one result dict per job, in order - any job the worker never
    reached (because it was killed) gets a synthetic error result naming
    the job and, if known from the worker's heartbeat, the step it was
    stuck on.

    `on_progress(index, entry)` is called (from this - the caller's -
    thread, via polling, not a real callback from the worker) as soon as
    each job's result appears in the worker's results file, so a long batch
    can print live progress instead of only reporting everything at once
    when this function finally returns.

    `job_timeout_s` (optional; shop conversions pass `shop_timeout_s()`) is a hard limit per JOB, on top of the
    no-progress timeout: a job still unfinished that long after its first heartbeat past "starting"/"launch" gets the
    worker's whole process tree (and so its CorelDRAW) killed, even if it keeps beating - a hidden modal dialog, a
    file lock or a hung COM call inside one step otherwise holds the queue for the full per-step timeout. The jobs
    the killed worker never reached come back as "not started"; the caller retries them in a fresh worker.

    `worker_module` and `skip_memory_check` exist for tests (a fake worker
    that just hangs, to exercise the timeout/kill path without needing real
    CorelDRAW) - production callers use the defaults.
    """
    overall_timeout_s = overall_timeout_s or DEFAULT_OVERALL_TIMEOUT_S
    if not skip_memory_check:
        free_gb = corel_util.check_memory()
        if free_gb < MIN_FREE_RAM_GB:
            raise RefusedToStart(
                f"refusing to start CorelDRAW batch: only {free_gb:.2f} GB free RAM "
                f"(need >= {MIN_FREE_RAM_GB} GB). Close other programs or restart the machine first."
            )

    results_path = Path(results_path)
    heartbeat_path = results_path.with_suffix(".heartbeat")
    done_path = results_path.with_suffix(".done")
    jobs_path = results_path.with_suffix(".jobs.json")
    for p in (results_path, heartbeat_path, done_path):
        p.unlink(missing_ok=True)
    jobs_path.write_text(json.dumps(jobs), encoding="utf-8")

    backend_dir = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", worker_module, str(jobs_path), str(results_path)],
        cwd=str(backend_dir),
    )
    logger.info("started CorelDRAW worker pid=%s for %d job(s); no-progress timeout=%.0fs",
                proc.pid, len(jobs), overall_timeout_s)

    last_progress_at = time.time()
    progress_state = {"count": 0, "heartbeat_mtime": None, "job_index": None, "job_started_at": None}

    def _check_progress() -> bool:
        """Reads any new results/heartbeat activity, fires on_progress for
        each newly-appeared result, and returns whether anything progressed.
        Called both from the polling loop and once more after it exits, so
        a worker that finishes between two polls (or before the first one)
        still gets its results reported - proc.poll() becoming non-None can
        otherwise short-circuit the loop before this ever runs for the
        final job(s).
        """
        progressed = False
        if results_path.exists():
            try:
                current = json.loads(results_path.read_text(encoding="utf-8"))
            except Exception:
                current = None
            if current is not None and len(current) > progress_state["count"]:
                if on_progress is not None:
                    for idx in range(progress_state["count"], len(current)):
                        try:
                            on_progress(idx, current[idx])
                        except Exception:
                            pass
                progress_state["count"] = len(current)
                progressed = True
        if heartbeat_path.exists():
            mtime = heartbeat_path.stat().st_mtime
            if mtime != progress_state["heartbeat_mtime"]:
                progress_state["heartbeat_mtime"] = mtime
                progressed = True
                hb = _read_json(heartbeat_path) or {}
                idx = hb.get("job_index")
                if (isinstance(idx, int) and idx >= progress_state["count"] and hb.get("step") not in _UNTIMED_STEPS
                        and idx != progress_state["job_index"]):
                    progress_state["job_index"], progress_state["job_started_at"] = idx, time.time()
        return progressed

    killed_for = None
    timed_out = None  # (job index, seconds) when the per-job limit killed the worker
    while True:
        if _check_progress():
            last_progress_at = time.time()

        if proc.poll() is not None:
            break

        idx, started = progress_state["job_index"], progress_state["job_started_at"]
        if (job_timeout_s and job_timeout_s > 0 and started is not None and idx == progress_state["count"]
                and time.time() - started > job_timeout_s):
            killed_for = _read_json(heartbeat_path)
            timed_out = (idx, job_timeout_s)
            logger.warning("worker pid=%s: job %d exceeded its %.0fs limit (at step %s); killing the worker and its CorelDRAW",
                           proc.pid, idx, job_timeout_s, (killed_for or {}).get("step"))
            _kill_tree(proc.pid)
            try:
                proc.wait(timeout=15)
            except Exception:
                pass
            break

        if time.time() - last_progress_at > overall_timeout_s:
            stuck = _read_json(heartbeat_path)
            killed_for = stuck
            logger.warning(
                "worker pid=%s made no progress for %.0fs (stuck on %s); killing process tree",
                proc.pid, overall_timeout_s, stuck or "an unknown step (no heartbeat yet)",
            )
            _kill_tree(proc.pid)
            try:
                proc.wait(timeout=15)
            except Exception:
                pass
            break

        time.sleep(POLL_INTERVAL_S)

    _check_progress()  # final catch-up in case the worker finished between the last poll and exit
    results = _read_json(results_path) or []
    if len(results) < len(jobs):
        stuck_index = len(results)
        for i in range(stuck_index, len(jobs)):
            if i == stuck_index and timed_out:
                step = (killed_for or {}).get("step")
                reason = (f"timed out after {timed_out[1]:.0f}s" + (f" at step '{step}'" if step else "")
                          + " - CorelDRAW was stopped (a hidden dialog, a locked file or a hung COM call); convert again")
            elif i == stuck_index and killed_for:
                reason = (f"worker killed after {overall_timeout_s:.0f}s with no progress "
                          f"(stuck on step '{killed_for.get('step')}')")
            elif i == stuck_index:
                reason = f"worker killed after {overall_timeout_s:.0f}s with no progress"
            else:
                reason = "not started - worker was killed before reaching this job"
            results.append({
                "master_path": jobs[i]["master_path"], "shop": jobs[i]["shop"], "out_dir": jobs[i]["out_dir"],
                "status": "error", "error": reason, "seconds": None,
            })

    for p in (heartbeat_path, done_path, jobs_path):
        p.unlink(missing_ok=True)
    return results


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
    except Exception:
        pass
    # /T kills the worker's direct children, but CorelDRAW is launched via
    # DCOM activation and isn't always a real child process of the worker -
    # corel_util's own PID-file tracking (written by the worker's calls into
    # corel_util) is what reliably finds and kills it regardless.
    corel_util.cleanup_orphaned_instances()
