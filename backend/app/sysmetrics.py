"""How strong and how busy this server is, for the admin page: CPU, memory, disk, load, uptime, this program's own memory, the queue.
psutil gives the numbers; without it the page still shows disk, load (Linux) and the queue instead of failing."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - the page degrades, see snapshot()
    psutil = None

STARTED = time.time()


def _level(pct: float | None, warn: float, bad: float) -> str:
    if pct is None:
        return "unknown"
    return "bad" if pct >= bad else "warn" if pct >= warn else "ok"


def snapshot(data_dir: Path) -> dict:
    """One reading. `level` per resource is ok / warn / bad so the page can colour it; `available` says whether psutil was there."""
    out: dict = {"available": psutil is not None, "cpu_count": os.cpu_count() or 1, "program_uptime_s": int(time.time() - STARTED)}
    if psutil is not None:
        out["cpu_pct"] = psutil.cpu_percent(interval=0.2)
        vm = psutil.virtual_memory()
        out["memory"] = {"total": vm.total, "used": vm.total - vm.available, "pct": vm.percent}
        out["system_uptime_s"] = int(time.time() - psutil.boot_time())
        try:
            out["program_memory"] = psutil.Process().memory_info().rss
        except Exception:  # noqa: BLE001
            out["program_memory"] = None
    else:
        out["cpu_pct"], out["memory"], out["system_uptime_s"], out["program_memory"] = None, None, None, None
    try:
        out["load"] = [round(x, 2) for x in os.getloadavg()]            # not on Windows
    except (AttributeError, OSError):
        out["load"] = None
    d = shutil.disk_usage(data_dir)
    out["disk"] = {"total": d.total, "used": d.used, "free": d.free, "pct": round(d.used / d.total * 100, 1) if d.total else None}
    cpus = out["cpu_count"]
    out["levels"] = {
        "cpu": _level(out["cpu_pct"], 70, 90),
        "memory": _level(out["memory"]["pct"] if out["memory"] else None, 80, 92),
        "disk": _level(out["disk"]["pct"], 80, 92),
        "load": _level(out["load"][0] / cpus * 100 if out["load"] else None, 100, 200),
    }
    return out
