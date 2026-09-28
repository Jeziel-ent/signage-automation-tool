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

-- Editor edits (Phase C): the replayable operation list per shop, kept apart
-- from `shops` so status/list queries never drag a large JSON blob along.
CREATE TABLE IF NOT EXISTS editor_ops (
    shop_id TEXT PRIMARY KEY,
    ops_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- Permanent font substitutions per shop ("Missing Font Detected" in the editor): text in `original_font`, a font not
-- installed on the server, is set to `substitute_font` in CorelDRAW before every export of that board.
CREATE TABLE IF NOT EXISTS font_substitutions (
    shop_id TEXT NOT NULL,
    original_font TEXT NOT NULL,
    substitute_font TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (shop_id, original_font)
);

-- Phase D exports ("Save and Generate"): one row per export request.
CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL,
    status TEXT NOT NULL,            -- queued | running | done | failed
    formats_json TEXT NOT NULL,
    options_json TEXT NOT NULL,
    ops_json TEXT NOT NULL,          -- the exact operation list that was replayed
    plan_json TEXT NOT NULL,
    error TEXT,
    files_json TEXT,
    report_json TEXT,
    created_at REAL NOT NULL,
    completed_at REAL
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
    phone TEXT,
    gst TEXT,
    address TEXT,
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
    # phone/gst: free-text per-shop contact info, written back via
    # layout.find_contact_ids/_contact_replacement (see CLAUDE.md "Per-shop
    # content replacement") - already supported by the OLD /api/jobs flow,
    # newly wired into the v2 UI/API here. `address` is a single free-text
    # field in the UI (one textarea, not the old flow's address_lines list)
    # - v2_add_shop/`_v2_convert_worker` split it on newlines before handing
    # it to the engine as `address_lines`, so both UIs feed the same
    # underlying engine parameter.
    ("shops", "phone", "ALTER TABLE shops ADD COLUMN phone TEXT"),
    ("shops", "gst", "ALTER TABLE shops ADD COLUMN gst TEXT"),
    ("shops", "address", "ALTER TABLE shops ADD COLUMN address TEXT"),
    # Dual-master templates: each uploaded master is its own `jobs` row and says which orientation it is
    # ('landscape' by default - every job created before this column existed was a single master); a shop
    # may point at a landscape master and/or a portrait master and the convert worker picks by target
    # orientation (orientation_adapter.select_master). NULL = "use the job's own master", as before.
    ("jobs", "orientation", "ALTER TABLE jobs ADD COLUMN orientation TEXT NOT NULL DEFAULT 'landscape'"),
    ("shops", "landscape_master_id", "ALTER TABLE shops ADD COLUMN landscape_master_id TEXT"),
    ("shops", "portrait_master_id", "ALTER TABLE shops ADD COLUMN portrait_master_id TEXT"),
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

def create_job(job_id: str, brand: str, master_filename: str, master_path: str, orientation: str = "landscape") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, brand, master_filename, master_path, orientation, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, brand, master_filename, master_path, orientation, time.time()),
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
                 reference_file_path: str | None = None, phone: str | None = None,
                 gst: str | None = None, address: str | None = None,
                 landscape_master_id: str | None = None, portrait_master_id: str | None = None) -> None:
    """`reference` is the free-text note shown in the UI today.
    `reference_file_path`, if given, is a path to an uploaded reference
    file - the data model supports it (per review feedback) ahead of any
    UI for actually uploading one; unused by the current frontend, which
    always passes it as None.
    `phone`/`gst`/`address` are optional per-shop contact fields written back
    onto the generated board (see CLAUDE.md "Per-shop content replacement");
    None means "leave the master's own text alone", same convention the old
    /api/jobs flow already uses - never pass "" for a field the user left
    blank, since compute_layout treats an empty string as "replace with
    blank", not "don't touch".
    """
    with _conn() as conn:
        conn.execute(
            """INSERT INTO shops
               (id, job_id, seq_no, name, width, width_unit, height, height_unit,
                reference, reference_file_path, phone, gst, address, landscape_master_id, portrait_master_id,
                status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
            (shop_id, job_id, seq_no, name, width, width_unit, height, height_unit,
             reference, reference_file_path, phone, gst, address, landscape_master_id, portrait_master_id,
             time.time()),
        )


def list_shops(job_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM shops WHERE job_id = ? ORDER BY seq_no", (job_id,)).fetchall()
    return [dict(r) for r in rows]


def get_shop(shop_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)).fetchone()
    return dict(row) if row else None


def update_shop_fields(shop_id: str, f: dict) -> None:
    """Overwrite the user-editable columns from a dict already validated by main._parse_shop_payload."""
    with _conn() as conn:
        conn.execute(
            """UPDATE shops SET name = ?, width = ?, width_unit = ?, height = ?, height_unit = ?,
               phone = ?, gst = ?, address = ? WHERE id = ?""",
            (f["name"], f["width"], f["width_unit"], f["height"], f["height_unit"], f["phone"], f["gst"], f["address"],
             shop_id))


def delete_shop(shop_id: str) -> None:
    """Remove a shop row and its editor/export bookkeeping (generated files on disk are left alone)."""
    with _conn() as conn:
        conn.execute("DELETE FROM editor_ops WHERE shop_id = ?", (shop_id,))
        conn.execute("DELETE FROM exports WHERE shop_id = ?", (shop_id,))
        conn.execute("DELETE FROM font_substitutions WHERE shop_id = ?", (shop_id,))
        conn.execute("DELETE FROM shops WHERE id = ?", (shop_id,))


def set_shop_masters(shop_id: str, landscape_master_id: str | None, portrait_master_id: str | None) -> None:
    with _conn() as conn:
        conn.execute("UPDATE shops SET landscape_master_id = ?, portrait_master_id = ? WHERE id = ?",
                     (landscape_master_id, portrait_master_id, shop_id))


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


def fail_interrupted_work(reason: str) -> tuple[int, int]:
    """Marks every shop still queued/converting and every export still queued/running as failed with `reason`.
    Called once at server start: those rows belonged to the previous server process, whose worker queue died with it,
    so nothing will ever finish them - the UI would otherwise poll them forever ("Converting 1 of N"). Returns
    (shops, exports) changed."""
    with _conn() as conn:
        shops = conn.execute("UPDATE shops SET status = 'failed', step = NULL, error = ? "
                             "WHERE status IN ('queued', 'converting')", (reason,)).rowcount
        exports = conn.execute("UPDATE exports SET status = 'failed', error = ?, completed_at = ? "
                               "WHERE status IN ('queued', 'running')", (reason, time.time())).rowcount
    return shops, exports


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
    # Only the timings: extracted by SQLite, not by parsing each whole report (they hold every placed object) in Python -
    # measured 262 ms -> 13 ms with 1000 shops. A malformed report yields NULL and is skipped.
    with _conn() as conn:
        rows = conn.execute("SELECT CASE WHEN json_valid(report_json) THEN json_extract(report_json, '$.timings_s') END AS t "
                            "FROM shops WHERE report_json IS NOT NULL").fetchall()
    for r in rows:
        try:
            timings = json.loads(r["t"]) if r["t"] else {}
        except Exception:
            continue
        if not isinstance(timings, dict):
            continue
        for step, seconds in timings.items():
            sums[step] = sums.get(step, 0.0) + float(seconds)
            counts[step] = counts.get(step, 0) + 1
    return {step: round(sums[step] / counts[step], 2) for step in sums}


# ----------------------------------------------------------- editor edits

def get_editor_ops(shop_id: str) -> list[dict]:
    with _conn() as conn:
        row = conn.execute("SELECT ops_json FROM editor_ops WHERE shop_id = ?", (shop_id,)).fetchone()
    return json.loads(row["ops_json"]) if row else []


def set_editor_ops(shop_id: str, ops: list[dict]) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO editor_ops (shop_id, ops_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(shop_id) DO UPDATE SET ops_json = excluded.ops_json, updated_at = excluded.updated_at""",
            (shop_id, json.dumps(ops), time.time()),
        )


# ---------------------------------------------------------------- exports

def create_export(export_id: str, shop_id: str, formats: list, options: dict, ops: list, plan: list) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO exports (id, shop_id, status, formats_json, options_json, ops_json, plan_json, created_at)
               VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)""",
            (export_id, shop_id, json.dumps(formats), json.dumps(options), json.dumps(ops), json.dumps(plan), time.time()),
        )


def get_export(export_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    return dict(row) if row else None


def list_exports(shop_id: str, limit: int = 10) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, status, formats_json, files_json, error, created_at, completed_at FROM exports "
            "WHERE shop_id = ? ORDER BY created_at DESC LIMIT ?", (shop_id, limit)).fetchall()
    return [dict(r) for r in rows]


def set_export_status(export_id: str, status: str, error: str | None = None) -> None:
    with _conn() as conn:
        conn.execute("UPDATE exports SET status = ?, error = ? WHERE id = ?", (status, error, export_id))
        if status == "failed":
            conn.execute("UPDATE exports SET completed_at = ? WHERE id = ?", (time.time(), export_id))


def set_export_result(export_id: str, files: dict, report: dict) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE exports SET status = 'done', error = NULL, files_json = ?, report_json = ?, completed_at = ? WHERE id = ?",
            (json.dumps(files), json.dumps(report), time.time(), export_id),
        )


def get_export_step_estimates() -> dict[str, float]:
    """Average seconds per export step over finished exports (paces the export progress bar)."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    with _conn() as conn:
        rows = conn.execute("SELECT report_json FROM exports WHERE status = 'done' AND report_json IS NOT NULL").fetchall()
    for r in rows:
        try:
            timings = json.loads(r["report_json"]).get("timings_s") or {}
        except Exception:
            continue
        for k, v in timings.items():
            sums[k] = sums.get(k, 0.0) + float(v)
            counts[k] = counts.get(k, 0) + 1
    return {k: round(sums[k] / counts[k], 2) for k in sums}


# ---------------------------------------------------------------- font substitutions

def get_font_substitutions(shop_id: str) -> dict[str, str]:
    """{original font: substitute font} saved for this shop."""
    with _conn() as conn:
        rows = conn.execute("SELECT original_font, substitute_font FROM font_substitutions WHERE shop_id = ? "
                            "ORDER BY original_font", (shop_id,)).fetchall()
    return {r["original_font"]: r["substitute_font"] for r in rows}


def set_font_substitution(shop_id: str, original_font: str, substitute_font: str) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO font_substitutions (shop_id, original_font, substitute_font, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(shop_id, original_font) DO UPDATE SET substitute_font = excluded.substitute_font,
               updated_at = excluded.updated_at""",
            (shop_id, original_font, substitute_font, time.time()))


def delete_font_substitution(shop_id: str, original_font: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM font_substitutions WHERE shop_id = ? AND original_font = ?", (shop_id, original_font))
    return cur.rowcount > 0
