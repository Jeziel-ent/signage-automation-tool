"""What is on the server's disk, and what can safely go.

NEVER deleted here: masters, the boards a conversion produced (CDR / PDF / preview / report), the database. Those are the designers' work.
Deletable, because the site can make them again or nobody needs them:
  * temporary leftovers      - half-written `.part` / `.new` files, finished worker files in convert_runs, fetched-font staging, WeTransfer
                               debug screenshots, expired share links (always, once old enough);
  * old exports              - an export folder older than N days, except the NEWEST finished export of each board (it is what the ZIP and
                               the print sheet use);
  * old editor scenes        - the per-board scene cache older than N days (opening the editor builds it again).
Exports and scenes are only touched when a number of days is given (`SIGNAGE_RETENTION_DAYS`, or the Server page's "Clean up" with a
number). `plan` only LISTS; `run` deletes and reports.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

DAY = 86400.0
TEMP_AGE_DAYS = 1.0
DEBUG_AGE_DAYS = 14.0


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return time.time()


def storage_report(data: Path) -> dict:
    """Bytes per category: masters, boards, scenes, exports, product_assets, fonts, shared, runs, backups, database, other."""
    out = {k: 0 for k in ("masters", "boards", "scenes", "exports", "product_assets", "fonts", "shared", "runs", "backups", "database", "other")}
    data = Path(data)
    if not data.is_dir():
        return out
    jobs = data / "jobs_v2"
    claimed: set[Path] = set()
    if jobs.is_dir():
        for job in jobs.iterdir():
            for f in job.glob("master.cdr*"):
                out["masters"] += _size(f)
            out_root = job / "out"
            if not out_root.is_dir():
                continue
            for shop in out_root.iterdir():
                for sub, cat in (("scene", "scenes"), ("exports", "exports"), ("product_assets", "product_assets")):
                    p = shop / sub
                    if p.is_dir():
                        out[cat] += _size(p)
                for f in shop.iterdir():
                    if f.is_file():
                        out["boards"] += f.stat().st_size
        claimed.add(jobs)
    for name, cat in (("agent_fonts", "fonts"), ("shared_zips", "shared"), ("convert_runs", "runs"), ("backups", "backups")):
        p = data / name
        if p.is_dir():
            out[cat] += _size(p)
            claimed.add(p)
    for f in data.glob("signage.db*"):
        out["database"] += f.stat().st_size
    for p in data.iterdir():
        if p in claimed or p.name.startswith("signage.db"):
            continue
        out["other"] += _size(p)
    return out


def plan(data: Path, now: float | None = None, days: float = 0, protected_exports: set[str] | None = None) -> list[dict]:
    """[{category, path, bytes, age_days, export_id?}] of everything that may be deleted now (see the module doc)."""
    now = now or time.time()
    data = Path(data)
    protected = protected_exports or set()
    found: list[dict] = []

    def add(category: str, path: Path, **extra) -> None:
        if path.exists():
            found.append({"category": category, "path": str(path), "bytes": _size(path), "age_days": round((now - _mtime(path)) / DAY, 1), **extra})

    def older(path: Path, age_days: float) -> bool:
        return (now - _mtime(path)) > age_days * DAY

    runs = data / "convert_runs"
    if runs.is_dir():
        for f in runs.iterdir():
            if older(f, TEMP_AGE_DAYS):
                add("temporary", f)
    incoming = data / "agent_fonts" / "_incoming"
    if incoming.is_dir():
        for f in incoming.iterdir():
            if older(f, TEMP_AGE_DAYS):
                add("temporary", f)
    for f in (data / "agent_fonts").glob("*.missing") if (data / "agent_fonts").is_dir() else []:
        if older(f, TEMP_AGE_DAYS):
            add("temporary", f)
    debug = data / "wetransfer_debug"
    if debug.is_dir():
        for f in debug.iterdir():
            if older(f, DEBUG_AGE_DAYS):
                add("temporary", f)
    shared = data / "shared_zips"
    share_days = float(os.environ.get("SIGNAGE_SHARE_DAYS", "3")) + 1
    if shared.is_dir():
        for f in shared.iterdir():
            if older(f, share_days):
                add("temporary", f)
    jobs = data / "jobs_v2"
    if jobs.is_dir():
        for part in list(jobs.rglob("*.part")) + list(jobs.rglob("*.new")):
            if part.is_file() and older(part, TEMP_AGE_DAYS):
                add("temporary", part)
        if days > 0:
            for exp in jobs.glob("*/out/*/exports/*"):
                if exp.is_dir() and exp.name not in protected and older(exp, days):
                    add("exports", exp, export_id=exp.name)
            for scene in jobs.glob("*/out/*/scene"):
                marker = scene / "scene.json"
                if scene.is_dir() and older(marker if marker.exists() else scene, days):
                    add("scenes", scene)
    return found


def run(items: list[dict], data: Path) -> dict:
    """Delete the planned items (each must lie inside `data`). Returns {deleted, bytes, by_category, export_ids}."""
    data = Path(data).resolve()
    summary = {"deleted": 0, "bytes": 0, "by_category": {}, "export_ids": []}
    for it in items:
        p = Path(it["path"]).resolve()
        if data not in p.parents or not p.exists():
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError:
            continue
        summary["deleted"] += 1
        summary["bytes"] += it["bytes"]
        summary["by_category"][it["category"]] = summary["by_category"].get(it["category"], 0) + it["bytes"]
        if it.get("export_id"):
            summary["export_ids"].append(it["export_id"])
    return summary


def summarize(items: list[dict]) -> dict:
    by: dict[str, dict] = {}
    for it in items:
        c = by.setdefault(it["category"], {"count": 0, "bytes": 0})
        c["count"] += 1
        c["bytes"] += it["bytes"]
    return by
