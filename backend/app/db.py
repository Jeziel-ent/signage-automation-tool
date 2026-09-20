"""SQLite persistence for the new UI (Phase A): brands, jobs (one per
uploaded master) and shops (one row per shop added under a job, each with
its own convert status/result). A fresh `sqlite3` connection is opened per
call - simple and safe across the threads this app already uses (the
single-worker conversion pool, the FastAPI request threads), rather than
sharing one connection that isn't thread-safe by default.

Deliberately separate from the old in-memory `_jobs` dict in `main.py`
(kept as-is for the pre-existing batch endpoints) - this is the new UI's
own storage, not a migration of the old one.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("SIGNAGE_DATA", Path(__file__).resolve().parents[1] / "data")) / "signage.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    brand TEXT NOT NULL,
    master_filename TEXT NOT NULL,
    master_path TEXT NOT NULL,
    preview_path TEXT,
    preview_error TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS shops (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    seq_no INTEGER NOT NULL,
    name TEXT NOT NULL,
    width REAL NOT NULL,
    width_unit TEXT NOT NULL,
    height REAL NOT NULL,
    height_unit TEXT NOT NULL,
    reference TEXT,
    reference_file_path TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    step TEXT,
    error TEXT,
    files_json TEXT,
    report_json TEXT,
    created_at REAL NOT NULL,
    completed_at REAL
);
"""

# Columns added after the tables above first shipped - `CREATE TABLE IF NOT
# EXISTS` doesn't retroactively add columns to an existing table, so an
# already-created shops.db needs an explicit ALTER TABLE. Each entry here is
# idempotent (checked against the live schema before running).
_MIGRATIONS = [
    ("shops", "reference_file_path", "ALTER TABLE shops ADD COLUMN reference_file_path TEXT"),
]


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(SCHEMA)
        for table, column, alter_sql in _MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in cols:
                conn.execute(alter_sql)


# ---------------------------------------------------------------- brands

def list_brands() -> list[str]:
    with _conn() as conn:
        rows = conn.execute("SELECT name FROM brands ORDER BY name").fetchall()
    return [r["name"] for r in rows]


def add_brand(name: str) -> list[str]:
    with _conn() as conn:
        conn.execute("INSERT OR IGNORE INTO brands (name) VALUES (?)", (name,))
    return list_brands()


# ------------------------------------------------------------------ jobs

def create_job(job_id: str, brand: str, master_filename: str, master_path: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, brand, master_filename, master_path, created_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, brand, master_filename, master_path, time.time()),
        )


def set_job_preview(job_id: str, preview_path: str | None, preview_error: str | None) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE jobs SET preview_path = ?, preview_error = ? WHERE id = ?",
            (preview_path, preview_error, job_id),
        )


def get_job(job_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------- shops

def create_shop(shop_id: str, job_id: str, seq_no: int, name: str, width: float, width_unit: str,
                 height: float, height_unit: str, reference: str | None,
                 reference_file_path: str | None = None) -> None:
    """`reference` is the free-text note shown in the UI today.
    `reference_file_path`, if given, is a path to an uploaded reference
    file - the data model supports it (per review feedback) ahead of any
    UI for actually uploading one; unused by the current frontend, which
    always passes it as None.
    """
    with _conn() as conn:
        conn.execute(
            """INSERT INTO shops
               (id, job_id, seq_no, name, width, width_unit, height, height_unit,
                reference, reference_file_path, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
            (shop_id, job_id, seq_no, name, width, width_unit, height, height_unit,
             reference, reference_file_path, time.time()),
        )


def list_shops(job_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM shops WHERE job_id = ? ORDER BY seq_no", (job_id,)).fetchall()
    return [dict(r) for r in rows]


def get_shop(shop_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)).fetchone()
    return dict(row) if row else None


def set_shop_status(shop_id: str, status: str, step: str | None = None, error: str | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE shops SET status = ?, step = ?, error = ? WHERE id = ?",
            (status, step, error, shop_id),
        )


def set_shop_result(shop_id: str, files: dict, report: dict) -> None:
    with _conn() as conn:
        conn.execute(
            """UPDATE shops SET status = 'done', step = NULL, error = NULL,
               files_json = ?, report_json = ?, completed_at = ? WHERE id = ?""",
            (json.dumps(files), json.dumps(report), time.time(), shop_id),
        )


def list_all_shops_with_job() -> list[dict]:
    """Every shop across every job, newest first, joined with its job's
    brand/master filename - for the "Recently generated" page.
    """
    with _conn() as conn:
        rows = conn.execute(
            """SELECT shops.*, jobs.brand AS brand, jobs.master_filename AS master_filename
               FROM shops JOIN jobs ON shops.job_id = jobs.id
               ORDER BY shops.created_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_step_timing_estimates() -> dict[str, float]:
    """Average observed duration (seconds) per `CorelEngine` step, across
    every completed shop's own `report.json` `timings_s` (see
    `engines.py`'s `_report`/`step()`) - real, measured numbers the
    frontend uses to pace its smoothed progress animation, rather than a
    guessed constant. Naturally starts empty and gets more representative
    as more real conversions complete; the frontend falls back to its own
    default for any step with no data yet.
    """
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    with _conn() as conn:
        rows = conn.execute("SELECT report_json FROM shops WHERE report_json IS NOT NULL").fetchall()
    for r in rows:
        try:
            timings = json.loads(r["report_json"]).get("timings_s") or {}
        except Exception:
            continue
        for step, seconds in timings.items():
            sums[step] = sums.get(step, 0.0) + float(seconds)
            counts[step] = counts.get(step, 0) + 1
    return {step: round(sums[step] / counts[step], 2) for step in sums}
