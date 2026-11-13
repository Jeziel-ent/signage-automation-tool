"""Server messages for the Google Chat space ("Adinn Server Updates"): database backups four times a day and the 7-day retention.

The database holds everything the designers made - Recently generated, users, masters, brands, corrections - so one backup covers
them all. A backup is taken at 12 am, 6 am, 12 pm and 6 pm (India time; `SIGNAGE_BACKUP_HOURS`, `SIGNAGE_BACKUP_TZ` change it), checked
with SQLite's own integrity check, copies older than `SIGNAGE_BACKUP_RETENTION_DAYS` (default 7) are deleted, and a message with the
result goes to `SIGNAGE_ALERT_WEBHOOK` (the same webhook as the problem alerts). Only the COPIES expire: masters, boards and the live
database are never deleted here. The loop runs on a hosted server (`SIGNAGE_ENGINE=remote`) or when `SIGNAGE_BACKUP_HOURS` is set.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import alerts, db

log = logging.getLogger("signage.backup")
DEFAULT_HOURS = (0, 6, 12, 18)
DEFAULT_RETENTION_DAYS = 7
SERVER_LABEL = "Signage Automation"


def enabled() -> bool:
    if os.environ.get("SIGNAGE_BACKUP_SCHEDULE", "").strip().lower() in ("0", "off", "no", "false"):
        return False
    return bool(os.environ.get("SIGNAGE_BACKUP_HOURS", "").strip()) or os.environ.get("SIGNAGE_ENGINE", "").strip().lower() == "remote"


def hours() -> tuple[int, ...]:
    raw = os.environ.get("SIGNAGE_BACKUP_HOURS", "").strip()
    try:
        got = tuple(sorted({int(x) for x in raw.replace(" ", "").split(",") if x != "" and 0 <= int(x) <= 23}))
    except ValueError:
        got = ()
    return got or DEFAULT_HOURS


def retention_days() -> int:
    try:
        return max(1, int(float(os.environ.get("SIGNAGE_BACKUP_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def tz():
    """India time by default; a fixed +05:30 when the tz database is not installed (Windows without tzdata)."""
    name = os.environ.get("SIGNAGE_BACKUP_TZ", "Asia/Kolkata").strip() or "Asia/Kolkata"
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return timezone(timedelta(hours=5, minutes=30), "IST")


def next_run(now: datetime | None = None, slots: tuple[int, ...] | None = None) -> datetime:
    """The next backup time after `now` (timezone-aware)."""
    zone = tz()
    now = (now or datetime.now(zone)).astimezone(zone)
    for day in (0, 1):
        for h in slots or hours():
            t = (now + timedelta(days=day)).replace(hour=h, minute=0, second=0, microsecond=0)
            if t > now:
                return t
    raise RuntimeError("no backup slot")  # pragma: no cover - the loop above always finds one


def _fmt_size(n: int) -> str:
    return f"{n / 1024 ** 2:.1f} MiB" if n >= 1024 ** 2 else f"{max(1, n // 1024)} KiB"


def prune(out_dir: Path, days: int, now: float | None = None) -> int:
    """Delete backup copies older than `days`; the newest copy is always kept. Returns how many were deleted."""
    now = now or time.time()
    files = sorted(out_dir.glob("signage-*.db"), key=lambda p: p.stat().st_mtime)
    deleted = 0
    for p in files[:-1]:
        if now - p.stat().st_mtime > days * 86400:
            p.unlink(missing_ok=True)
            deleted += 1
    return deleted


def counts() -> dict:
    """What the backup holds, in the words of the app: boards made, users, masters."""
    with db._conn() as conn:
        def one(sql: str) -> int:
            try:
                return conn.execute(sql).fetchone()[0]
            except sqlite3.Error:
                return 0
        return {
            "recent": one("SELECT COUNT(*) FROM shops WHERE status = 'done'"),
            "users": one("SELECT COUNT(*) FROM users"),
            "masters": one("SELECT COUNT(*) FROM jobs WHERE registered IS NOT NULL AND deleted_at IS NULL"),
        }


def next_number() -> int:
    n = int(db.get_setting("backup_counter", "0") or 0) + 1
    db.set_setting("backup_counter", str(n))
    return n


def post(text: str, wait: bool = True) -> bool:
    url = alerts.webhook()
    if not url:
        return False
    alerts._post(url, alerts.body_for(url, text))
    return True


def _backup_tool():
    """backend/tools/backup_db.py (a script folder, not a package), loaded by path."""
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "tools" / "backup_db.py"
    spec = importlib.util.spec_from_file_location("signage_backup_db", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_backup(data_dir: Path, now: float | None = None) -> dict:
    """Take one backup, prune, and send the Chat message. Never raises: a failure is reported in the same space."""
    when = datetime.fromtimestamp(now or time.time(), tz())
    stamp = when.strftime("%Y-%m-%d %H:%M:%S IST")
    out_dir = Path(data_dir) / "backups"
    n = next_number()
    try:
        target = _backup_tool().backup(db.DB_PATH, out_dir, keep=0, now=now)         # keep=0: pruning below is by AGE, not count
        deleted = prune(out_dir, retention_days(), now)
        c = counts()
        text = (f"✅ *Database Backup - Signage #{n}* — SUCCESS\n"
                f"Database: SQLite signage.db (recently generated, users, masters)\n"
                f"Backup completed: {stamp}\n"
                f"Backup file: {target.name}\n"
                f"Backup size: {_fmt_size(target.stat().st_size)}\n"
                f"Contents: {c['recent']} generated boards · {c['users']} users · {c['masters']} masters\n"
                f"Storage: server volume ({out_dir.as_posix()})\n"
                f"Retention: OK: {retention_days()}-day retention applied (deleted {deleted} old backup(s))")
        ok = True
    except Exception as e:  # noqa: BLE001
        log.exception("backup failed")
        text = (f"❌ *Database Backup - Signage #{n}* — FAILED\n"
                f"Time: {stamp}\nReason: {e}\nAction: manual investigation required. No data was changed.")
        ok = False
    post(text)
    return {"ok": ok, "text": text}


async def loop(data_dir: Path) -> None:
    """Sleep to the next slot, back up, repeat. Cancelled at shutdown."""
    import asyncio
    from starlette.concurrency import run_in_threadpool
    while True:
        wait = (next_run() - datetime.now(tz())).total_seconds()
        await asyncio.sleep(max(1.0, wait))
        try:
            await run_in_threadpool(run_backup, data_dir)
        except Exception:  # noqa: BLE001 - the loop must survive a bad round
            log.exception("backup round failed")
        await asyncio.sleep(5)          # never twice in the same minute
