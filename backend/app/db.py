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

-- Correction memory (app/corrections.py): what the designer changed in the editor, one record per shop, replaced on every save.
-- every stored correction is used when a board is generated (no approval step); `status` is a legacy column, always `approved`.
CREATE TABLE IF NOT EXISTS corrections (
    shop_id TEXT PRIMARY KEY,
    brand TEXT,
    master_file TEXT,
    page_w_mm REAL NOT NULL,
    page_h_mm REAL NOT NULL,
    board_type TEXT,
    record_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Accounts (app/auth.py): email + password, role admin | designer. Sessions store only a hash of the cookie token.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'designer',
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

-- Windows agents (app/agents.py): one row per designer PC that paired with the site. Only a hash of the agent's token is stored.
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    version TEXT,
    corel_json TEXT,
    state TEXT,
    created_at REAL NOT NULL,
    last_seen REAL,
    revoked_at REAL,
    fonts_hash TEXT,
    fonts_json TEXT
);

-- One-time codes the website hands to a signed-in user to pair an agent with their account (10 minutes, single use).
CREATE TABLE IF NOT EXISTS agent_pairing_codes (
    code_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at REAL NOT NULL
);

-- The job queue between the site and the Windows agents (app/agents.py): one row per piece of CorelDRAW work. A job belongs to the
-- user who asked for it and is claimed by one of THAT user's agents; the claim is a lease that the agent keeps alive with progress
-- calls, and an expired lease puts the job back in the queue (or fails it after MAX_ATTEMPTS).
CREATE TABLE IF NOT EXISTS agent_jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                 -- convert | scene_build | export
    shop_id TEXT,
    ref TEXT,                           -- what the job is for besides the shop: the export id of an export job
    user_id INTEGER NOT NULL,
    agent_id TEXT,
    state TEXT NOT NULL DEFAULT 'queued',   -- queued | claimed | running | done | failed
    payload_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    step TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_until REAL,
    created_at REAL NOT NULL,
    claimed_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS agent_jobs_queue ON agent_jobs (state, user_id, created_at);
CREATE INDEX IF NOT EXISTS agent_jobs_shop ON agent_jobs (shop_id);

-- Who did what (app/audit.py): sign-ins, accounts, masters, corrections, agents, fonts. Never holds a password or token.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    user_id INTEGER,
    user_email TEXT,
    action TEXT NOT NULL,
    target TEXT,
    detail_json TEXT,
    ip TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log (at);

-- Small app-wide switches (Corel Intelligence on/off).
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
    # Shop-name binding: the local-script (Tamil) name from an Excel import, and the text the MASTER itself currently
    # shows as its shop name (how the engine finds the shape to overwrite on an untagged master - see
    # layout.find_shopname_ids). master_shop_name is filled from the uploaded file's designer-style name
    # ("<code> - W X H unit - type - SHOP NAME.cdr") when it has one; either can be set/corrected via the API.
    ("shops", "shop_name_local", "ALTER TABLE shops ADD COLUMN shop_name_local TEXT"),
    ("jobs", "master_shop_name", "ALTER TABLE jobs ADD COLUMN master_shop_name TEXT"),
    ("jobs", "master_shop_name_local", "ALTER TABLE jobs ADD COLUMN master_shop_name_local TEXT"),
    # "Type of board" (Nonlit / Frontlit / Backlit / ...): shown in the Shops table and part of every export's file
    # name (file_naming.signage_basename). NULL = the default type.
    ("shops", "board_type", "ALTER TABLE shops ADD COLUMN board_type TEXT"),
    # 1 = the last conversion applied designers' corrections (Corel Intelligence); the next plain Convert resets it to 0
    ("shops", "use_intelligence", "ALTER TABLE shops ADD COLUMN use_intelligence INTEGER NOT NULL DEFAULT 0"),
    # Fonts for the replaced English / Tamil shop names (NULL = keep the master's font for English, layout.TAMIL_FONT
    # for Tamil) and the Excel sheet a row was imported from (the Shops Queue's sheet tabs).
    ("shops", "font_en", "ALTER TABLE shops ADD COLUMN font_en TEXT"),
    ("shops", "font_ta", "ALTER TABLE shops ADD COLUMN font_ta TEXT"),
    ("shops", "sheet_name", "ALTER TABLE shops ADD COLUMN sheet_name TEXT"),
    # The S.No exactly as the imported sheet wrote it ("76", "SL-01") - what the output files are numbered by; NULL =
    # use seq_no. seq_no still holds it when it is a whole number (it orders the job's shops).
    ("shops", "sno_label", "ALTER TABLE shops ADD COLUMN sno_label TEXT"),
    # which shop-name line(s) the board shows: NULL / 'both' (default), 'en' (English only), 'ta' (Tamil only)
    ("shops", "language", "ALTER TABLE shops ADD COLUMN language TEXT"),
    # Master template registry (GET/POST/DELETE /api/masters): any number of landscape and portrait masters per brand.
    # A master is still its own `jobs` row; these add a display name ("Master 3 - Compact"), an optional default board
    # size, `registered` = 1 for masters uploaded since the registry exists (older uploads stay unlisted - they still
    # work for the shops made from them) and `deleted_at` (soft delete: the master's folder also holds the boards made
    # from it, which Recently generated still shows).
    ("jobs", "master_name", "ALTER TABLE jobs ADD COLUMN master_name TEXT"),
    ("jobs", "default_width", "ALTER TABLE jobs ADD COLUMN default_width REAL"),
    ("jobs", "default_height", "ALTER TABLE jobs ADD COLUMN default_height REAL"),
    ("jobs", "default_unit", "ALTER TABLE jobs ADD COLUMN default_unit TEXT"),
    ("jobs", "registered", "ALTER TABLE jobs ADD COLUMN registered INTEGER"),
    ("jobs", "deleted_at", "ALTER TABLE jobs ADD COLUMN deleted_at REAL"),
    # the master the user picked for this shop in the queue's Master column; NULL = the default master of the board's
    # orientation (landscape_master_id / portrait_master_id). main._select_shop_master falls back when it is unusable.
    ("shops", "master_id", "ALTER TABLE shops ADD COLUMN master_id TEXT"),
    ("agent_jobs", "ref", "ALTER TABLE agent_jobs ADD COLUMN ref TEXT"),
    # the Windows agent (PC) that converted the board: "Recently generated" is per PC (NULL = converted on the server itself)
    ("shops", "agent_id", "ALTER TABLE shops ADD COLUMN agent_id TEXT"),
    # the fonts installed on an agent's PC (reported by the agent: the editor's font pickers and the missing-font checks use THEIR fonts)
    # the master file's SHA-256 (the same file is recognised under any name) and the CorelDRAW version that saved it (25.1 = 2024)
    ("jobs", "master_hash", "ALTER TABLE jobs ADD COLUMN master_hash TEXT"),
    ("jobs", "core_version", "ALTER TABLE jobs ADD COLUMN core_version REAL"),
    ("agents", "fonts_hash", "ALTER TABLE agents ADD COLUMN fonts_hash TEXT"),
    ("agents", "fonts_json", "ALTER TABLE agents ADD COLUMN fonts_json TEXT"),
]


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # write-ahead log: readers never block the writer and several designers can use the site at once (the default rollback journal
    # answers "database is locked" to a second writer); the setting is stored in the file, repeating it per connection is cheap
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
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

def create_job(job_id: str, brand: str, master_filename: str, master_path: str, orientation: str = "landscape",
               master_shop_name: str | None = None, master_shop_name_local: str | None = None,
               master_name: str | None = None, default_size: tuple[float, float, str] | None = None,
               registered: bool = False) -> None:
    dw, dh, du = default_size or (None, None, None)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, brand, master_filename, master_path, orientation, master_shop_name, master_shop_name_local,"
            " master_name, default_width, default_height, default_unit, registered, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, brand, master_filename, master_path, orientation, master_shop_name, master_shop_name_local,
             master_name, dw, dh, du, 1 if registered else None, time.time()),
        )


def set_master_info(job_id: str, master_hash: str | None, core_version: float | None) -> None:
    with _conn() as conn:
        conn.execute("UPDATE jobs SET master_hash = ?, core_version = ? WHERE id = ?", (master_hash, core_version, job_id))


def list_masters(brand: str | None = None, orientation: str | None = None) -> list[dict]:
    """Registered, not deleted masters, oldest first (so "Master 1" is listed first and is each orientation's default)."""
    sql, args = "SELECT * FROM jobs WHERE registered = 1 AND deleted_at IS NULL", []
    if brand is not None:
        sql, args = sql + " AND brand = ?", args + [brand]
    if orientation is not None:
        sql, args = sql + " AND orientation = ?", args + [orientation]
    with _conn() as conn:
        rows = conn.execute(sql + " ORDER BY created_at, rowid", args).fetchall()
    return [dict(r) for r in rows]


def archive_registered_masters() -> int:
    """Soft-delete (the same `deleted_at` the Delete button sets) every registered master that is still listed. Their files and the boards
    already made from them stay. Returns how many were hidden."""
    with _conn() as conn:
        cur = conn.execute("UPDATE jobs SET deleted_at = ? WHERE registered = 1 AND deleted_at IS NULL", (time.time(),))
    return cur.rowcount


_MASTER_FIELDS = ("master_name", "orientation", "default_width", "default_height", "default_unit", "master_filename")


def update_master(job_id: str, fields: dict) -> None:
    """Change the named columns of a master (only the whitelisted ones)."""
    cols = [k for k in fields if k in _MASTER_FIELDS]
    if not cols:
        return
    with _conn() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?", [fields[c] for c in cols] + [job_id])


def count_registered_masters(brand: str, orientation: str) -> int:
    """Every master ever registered for this brand + orientation, deleted ones included - numbers default names so a
    name is never reused after a delete."""
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM jobs WHERE registered = 1 AND brand = ? AND orientation = ?",
                            (brand, orientation)).fetchone()[0]


def delete_master(job_id: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE jobs SET deleted_at = ? WHERE id = ?", (time.time(), job_id))


def count_active_shops_using_master(job_id: str) -> int:
    """Shops queued/converting that are saved on, or may convert from, this master."""
    with _conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM shops WHERE status IN ('queued', 'converting') AND (job_id = ? OR master_id = ?"
            " OR landscape_master_id = ? OR portrait_master_id = ?)", (job_id,) * 4).fetchone()[0]


def set_job_master_shop_names(job_id: str, master_shop_name: str | None, master_shop_name_local: str | None) -> None:
    with _conn() as conn:
        conn.execute("UPDATE jobs SET master_shop_name = ?, master_shop_name_local = ? WHERE id = ?",
                     (master_shop_name, master_shop_name_local, job_id))


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
                 landscape_master_id: str | None = None, portrait_master_id: str | None = None,
                 shop_name_local: str | None = None, board_type: str | None = None,
                 font_en: str | None = None, font_ta: str | None = None, sheet_name: str | None = None,
                 language: str | None = None, master_id: str | None = None) -> None:
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
                shop_name_local, board_type, font_en, font_ta, sheet_name, language, master_id, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)""",
            (shop_id, job_id, seq_no, name, width, width_unit, height, height_unit,
             reference, reference_file_path, phone, gst, address, landscape_master_id, portrait_master_id,
             shop_name_local, board_type, font_en, font_ta, sheet_name, language, master_id, time.time()),
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
               phone = ?, gst = ?, address = ?, shop_name_local = ?, board_type = ?, font_en = ?, font_ta = ?,
               sheet_name = ?, language = ? WHERE id = ?""",
            (f["name"], f["width"], f["width_unit"], f["height"], f["height_unit"], f["phone"], f["gst"], f["address"],
             f.get("shop_name_local"), f.get("board_type"), f.get("font_en"), f.get("font_ta"), f.get("sheet_name"),
             f.get("language"), shop_id))


def set_shop_seq_no(shop_id: str, seq_no: int) -> None:
    """The S.no the shop has in the queue table (sent with Convert) - what its output files are numbered by."""
    with _conn() as conn:
        conn.execute("UPDATE shops SET seq_no = ?, sno_label = NULL WHERE id = ?", (int(seq_no), shop_id))


def set_shop_sno_label(shop_id: str, label: str) -> None:
    """A non-numeric S.No from the imported sheet ("SL-01"); seq_no is left as it is (it only orders the shops)."""
    with _conn() as conn:
        conn.execute("UPDATE shops SET sno_label = ? WHERE id = ?", (label, shop_id))


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


def set_shop_master_id(shop_id: str, master_id: str | None) -> None:
    with _conn() as conn:
        conn.execute("UPDATE shops SET master_id = ? WHERE id = ?", (master_id, shop_id))


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


def fail_interrupted_work(reason: str, keep_agent_jobs: bool = False) -> tuple[int, int]:
    """Marks every shop still queued/converting and every export still queued/running as failed with `reason`.
    Called once at server start: those rows belonged to the previous server process, whose worker queue died with it,
    so nothing will ever finish them - the UI would otherwise poll them forever ("Converting 1 of N"). Returns
    (shops, exports) changed. `keep_agent_jobs`: a shop with a live job in the agent queue (queued / claimed / running) is NOT
    interrupted - the queue lives in the database and survives a server restart."""
    keep = (" AND id NOT IN (SELECT shop_id FROM agent_jobs WHERE kind = 'convert' AND state IN ('queued', 'claimed', 'running')"
            " AND shop_id IS NOT NULL)" if keep_agent_jobs else "")
    keep_exports = (" AND id NOT IN (SELECT ref FROM agent_jobs WHERE kind = 'export' AND state IN ('queued', 'claimed', 'running')"
                    " AND ref IS NOT NULL)" if keep_agent_jobs else "")
    with _conn() as conn:
        shops = conn.execute("UPDATE shops SET status = 'failed', step = NULL, error = ? "
                             "WHERE status IN ('queued', 'converting')" + keep, (reason,)).rowcount
        exports = conn.execute("UPDATE exports SET status = 'failed', error = ?, completed_at = ? "
                               "WHERE status IN ('queued', 'running')" + keep_exports, (reason, time.time())).rowcount
    return shops, exports


def set_shop_agent(shop_id: str, agent_id: str | None) -> None:
    with _conn() as conn:
        conn.execute("UPDATE shops SET agent_id = ? WHERE id = ?", (agent_id, shop_id))


def list_all_shops_with_job() -> list[dict]:
    """Every shop across every job, newest first, joined with its job's
    brand/master filename - for the "Recently generated" page.
    """
    with _conn() as conn:
        rows = conn.execute(
            """SELECT shops.*, jobs.brand AS brand, jobs.master_filename AS master_filename,
                      json_extract(shops.report_json, '$.layout') AS layout_json
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


# ---------------------------------------------------------------- settings

def get_setting(key: str, default: str) -> str:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value))


def set_shop_intelligence(shop_id: str, on: bool) -> None:
    with _conn() as conn:
        conn.execute("UPDATE shops SET use_intelligence = ? WHERE id = ?", (1 if on else 0, shop_id))


# ----------------------------------------------------------- corrections

def save_correction(record: dict) -> None:
    """Store (or replace) a correction record. Every stored record is used - there is no approval step (`status` is a legacy column,
    always written `approved`, never read)."""
    now = time.time()
    status = "approved"
    with _conn() as conn:
        conn.execute(
            """INSERT INTO corrections (shop_id, brand, master_file, page_w_mm, page_h_mm, board_type, record_json, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(shop_id) DO UPDATE SET brand = excluded.brand, master_file = excluded.master_file,
                 page_w_mm = excluded.page_w_mm, page_h_mm = excluded.page_h_mm, board_type = excluded.board_type,
                 record_json = excluded.record_json, status = excluded.status, updated_at = excluded.updated_at""",
            (record["shop_id"], record.get("brand"), record.get("master_file"), record["page_w_mm"], record["page_h_mm"],
             record.get("board_type"), json.dumps(record), status, now, now))


def delete_correction(shop_id: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM corrections WHERE shop_id = ?", (shop_id,))


def get_correction(shop_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM corrections WHERE shop_id = ?", (shop_id,)).fetchone()
    if not row:
        return None
    return {**dict(row), "record": json.loads(row["record_json"])}


def set_correction_feedback(source_shop_id: str, board_shop_id: str, confirmed: int, disputed: int) -> bool:
    """Record what ONE board's save said about a stored record: how many of its lessons the designer kept / corrected on that board.
    Kept per board (`record.feedback.boards[board]`), so saving the same board again replaces its verdict instead of adding to it."""
    with _conn() as conn:
        row = conn.execute("SELECT record_json FROM corrections WHERE shop_id = ?", (source_shop_id,)).fetchone()
        if not row:
            return False
        rec = json.loads(row["record_json"])
        fb = rec.setdefault("feedback", {})
        fb.pop("confirmed", None)
        fb.pop("disputed", None)
        boards = fb.setdefault("boards", {})
        if confirmed == 0 and disputed == 0:
            boards.pop(board_shop_id, None)
        else:
            boards[board_shop_id] = [int(confirmed), int(disputed)]
        conn.execute("UPDATE corrections SET record_json = ? WHERE shop_id = ?", (json.dumps(rec), source_shop_id))
    return True


def list_corrections() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM corrections ORDER BY updated_at DESC").fetchall()
    return [{**dict(r), "record": json.loads(r["record_json"])} for r in rows]


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


# ------------------------------------------------------------ users + sessions (app/auth.py)

_USER_COLS = "id, email, name, role, active, created_at"
_USER_UPDATABLE = ("name", "role", "active", "password_hash")


def count_users() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def create_user(email: str, name: str, password_hash: str, role: str) -> dict:
    """Insert a user (email already lower-cased by the caller). Raises sqlite3.IntegrityError for a duplicate email."""
    with _conn() as conn:
        cur = conn.execute("INSERT INTO users (email, name, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
                           (email, name, password_hash, role, time.time()))
        row = conn.execute(f"SELECT {_USER_COLS} FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def get_user_with_hash(email: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(f"SELECT {_USER_COLS} FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(f"SELECT {_USER_COLS} FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def designer_usage(since: float) -> list[dict]:
    """Every account with what it did since `since` (agent jobs: boards converted / exported / failed) and when it was last seen."""
    now = time.time()
    out = []
    with _conn() as conn:
        for u in conn.execute(f"SELECT {_USER_COLS} FROM users ORDER BY id").fetchall():
            uid = u["id"]
            seen = conn.execute("SELECT MAX(CASE WHEN action = 'login' THEN at END) AS last_login, MAX(at) AS last_active, "
                                "COALESCE(SUM(CASE WHEN action = 'login' AND at >= ? THEN 1 ELSE 0 END), 0) AS logins "
                                "FROM audit_log WHERE user_id = ?", (since, uid)).fetchone()
            jobs = {(r["kind"], r["state"]): r["n"] for r in conn.execute(
                "SELECT kind, state, COUNT(*) AS n FROM agent_jobs WHERE user_id = ? AND created_at >= ? GROUP BY kind, state", (uid, since))}
            last_job = conn.execute("SELECT MAX(created_at) FROM agent_jobs WHERE user_id = ?", (uid,)).fetchone()[0]
            signed_in = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ? AND expires_at > ?", (uid, now)).fetchone()[0]
            out.append({**dict(u), "last_login": seen["last_login"], "last_active": max(filter(None, [seen["last_active"], last_job]), default=None),
                        "logins": seen["logins"], "signed_in": bool(signed_in),
                        "converted": jobs.get(("convert", "done"), 0), "exported": jobs.get(("export", "done"), 0),
                        "failed": sum(n for (k, s), n in jobs.items() if s == "failed")})
    return out


def delete_user(user_id: int) -> None:
    """Remove the account for good: sessions go, its PCs are disconnected, its waiting jobs fail. The activity log keeps the e-mail."""
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE agents SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (time.time(), user_id))
        conn.execute("UPDATE agent_jobs SET state = 'failed', error = 'the account was deleted' WHERE user_id = ? AND state IN ('queued', 'claimed', 'running')", (user_id,))
        conn.execute("DELETE FROM agent_pairing_codes WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def count_active_admins() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1").fetchone()["n"]


def update_user(user_id: int, fields: dict) -> None:
    cols = [k for k in fields if k in _USER_UPDATABLE]
    if not cols:
        return
    with _conn() as conn:
        conn.execute(f"UPDATE users SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                     [fields[c] for c in cols] + [user_id])


def create_session(token_hash: str, user_id: int, ttl_s: float) -> None:
    now = time.time()
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                     (token_hash, user_id, now, now + ttl_s))


def get_session_user(token_hash: str) -> dict | None:
    """The ACTIVE user owning an unexpired session, else None."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT u.id, u.email, u.name, u.role, u.active, u.created_at FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1", (token_hash, time.time())).fetchone()
    return dict(row) if row else None


def delete_session(token_hash: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def delete_user_sessions(user_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# ------------------------------------------------------------ agents (app/agents.py)

def create_pairing_code(code_hash: str, user_id: int, ttl_s: float) -> None:
    now = time.time()
    with _conn() as conn:
        conn.execute("DELETE FROM agent_pairing_codes WHERE expires_at < ?", (now,))
        conn.execute("INSERT INTO agent_pairing_codes (code_hash, user_id, expires_at) VALUES (?, ?, ?)",
                     (code_hash, user_id, now + ttl_s))


def consume_pairing_code(code_hash: str) -> int | None:
    """The user a valid code belongs to (the code is deleted: single use), else None."""
    with _conn() as conn:
        row = conn.execute("SELECT user_id, expires_at FROM agent_pairing_codes WHERE code_hash = ?", (code_hash,)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM agent_pairing_codes WHERE code_hash = ?", (code_hash,))
    return row["user_id"] if row["expires_at"] > time.time() else None


def create_agent(agent_id: str, user_id: int, name: str, token_hash: str, version: str | None, corel_json: str | None) -> None:
    with _conn() as conn:
        conn.execute("INSERT INTO agents (id, user_id, name, token_hash, version, corel_json, state, created_at, last_seen)"
                     " VALUES (?, ?, ?, ?, ?, ?, 'idle', ?, ?)", (agent_id, user_id, name, token_hash, version, corel_json,
                                                                  time.time(), time.time()))


def get_agent_by_token(token_hash: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM agents WHERE token_hash = ? AND revoked_at IS NULL", (token_hash,)).fetchone()
    return dict(row) if row else None


def touch_agent(agent_id: str, version: str | None, corel_json: str | None, state: str | None) -> None:
    with _conn() as conn:
        conn.execute("UPDATE agents SET last_seen = ?, version = COALESCE(?, version), corel_json = COALESCE(?, corel_json),"
                     " state = COALESCE(?, state) WHERE id = ?", (time.time(), version, corel_json, state, agent_id))


def set_agent_fonts(agent_id: str, fonts_hash: str, fonts_json: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE agents SET fonts_hash = ?, fonts_json = ? WHERE id = ?", (fonts_hash, fonts_json, agent_id))


def agent_fonts_for_user(user_id: int) -> tuple[dict, str] | None:
    """(the fonts dict, the PC's name) of the user's most recently seen agent that has reported its fonts, else None."""
    with _conn() as conn:
        row = conn.execute("SELECT name, fonts_json FROM agents WHERE user_id = ? AND revoked_at IS NULL AND fonts_json IS NOT NULL"
                           " ORDER BY last_seen DESC LIMIT 1", (user_id,)).fetchone()
    return (json.loads(row["fonts_json"]), row["name"]) if row else None


def list_agents(user_id: int | None = None) -> list[dict]:
    """Not-revoked agents, newest first; one user's own, or everybody's when user_id is None."""
    sql, args = "SELECT a.*, COALESCE(u.email, '(login off)') AS owner_email FROM agents a LEFT JOIN users u ON u.id = a.user_id WHERE a.revoked_at IS NULL", []
    if user_id is not None:
        sql, args = sql + " AND a.user_id = ?", [user_id]
    with _conn() as conn:
        rows = conn.execute(sql + " ORDER BY a.created_at DESC", args).fetchall()
    return [dict(r) for r in rows]


def get_agent(agent_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    return dict(row) if row else None


def revoke_agent(agent_id: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE agents SET revoked_at = ? WHERE id = ?", (time.time(), agent_id))


# ------------------------------------------------------------ agent job queue (app/agents.py)

MAX_ATTEMPTS = 3


def create_agent_job(job_id: str, kind: str, shop_id: str | None, user_id: int, payload: dict, ref: str | None = None) -> None:
    with _conn() as conn:
        conn.execute("INSERT INTO agent_jobs (id, kind, shop_id, ref, user_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (job_id, kind, shop_id, ref, user_id, json.dumps(payload), time.time()))


def active_agent_job(kind: str, shop_id: str | None = None, ref: str | None = None) -> dict | None:
    """The newest queued / claimed / running job of this kind for a shop (or an export id), else None."""
    sql, args = "SELECT * FROM agent_jobs WHERE kind = ? AND state IN ('queued', 'claimed', 'running')", [kind]
    if shop_id is not None:
        sql, args = sql + " AND shop_id = ?", args + [shop_id]
    if ref is not None:
        sql, args = sql + " AND ref = ?", args + [ref]
    with _conn() as conn:
        row = conn.execute(sql + " ORDER BY created_at DESC LIMIT 1", args).fetchone()
    return dict(row) if row else None


def agent_job_by_ref(kind: str, ref: str) -> dict | None:
    """The newest job of this kind for an export id, in any state."""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM agent_jobs WHERE kind = ? AND ref = ? ORDER BY created_at DESC LIMIT 1", (kind, ref)).fetchone()
    return dict(row) if row else None


def latest_agent_job_for_shop(shop_id: str, kind: str = "convert") -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM agent_jobs WHERE shop_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1", (shop_id, kind)).fetchone()
    return dict(row) if row else None


def get_agent_job(job_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM agent_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def active_agent_job_for_shop(shop_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM agent_jobs WHERE shop_id = ? AND state IN ('queued', 'claimed', 'running')"
                           " ORDER BY created_at DESC LIMIT 1", (shop_id,)).fetchone()
    return dict(row) if row else None


def claim_agent_jobs(agent_id: str, user_id: int, limit: int, lease_s: float, kinds: tuple = ("convert",)) -> list[dict]:
    """Atomically hand queued jobs of this user (oldest first) to one agent. The OLDEST job decides the kind of the round: convert jobs are
    batched (up to `limit`, one CorelDRAW for all), a scene build or an export goes alone. BEGIN IMMEDIATE takes the write lock first, so
    two agents asking at the same moment can never get the same job. `kinds` = what this agent can do (an older agent only converts)."""
    now = time.time()
    marks = ",".join("?" * len(kinds))
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        first = conn.execute(f"SELECT kind FROM agent_jobs WHERE state = 'queued' AND user_id = ? AND kind IN ({marks})"
                             " ORDER BY created_at LIMIT 1", (user_id, *kinds)).fetchone()
        if not first:
            return []
        rows = conn.execute("SELECT id FROM agent_jobs WHERE state = 'queued' AND user_id = ? AND kind = ? ORDER BY created_at LIMIT ?",
                            (user_id, first["kind"], limit if first["kind"] == "convert" else 1)).fetchall()
        ids = [r["id"] for r in rows]
        for jid in ids:
            conn.execute("UPDATE agent_jobs SET state = 'claimed', agent_id = ?, claimed_at = ?, lease_until = ?, attempts = attempts + 1,"
                         " step = NULL WHERE id = ?", (agent_id, now, now + lease_s, jid))
        out = [dict(conn.execute("SELECT * FROM agent_jobs WHERE id = ?", (jid,)).fetchone()) for jid in ids]
    return out


def touch_agent_job(job_id: str, agent_id: str, lease_s: float, step: str | None = None, running: bool = True) -> bool:
    """Extend the lease (and record the step) of a job THIS agent holds. False when it no longer holds it (lease expired, re-queued)."""
    with _conn() as conn:
        cur = conn.execute("UPDATE agent_jobs SET lease_until = ?, step = COALESCE(?, step),"
                           " state = CASE WHEN ? THEN 'running' ELSE state END"
                           " WHERE id = ? AND agent_id = ? AND state IN ('claimed', 'running')",
                           (time.time() + lease_s, step, 1 if running else 0, job_id, agent_id))
    return cur.rowcount > 0


def finish_agent_job(job_id: str, agent_id: str, ok: bool, result: dict | None, error: str | None) -> bool:
    with _conn() as conn:
        cur = conn.execute("UPDATE agent_jobs SET state = ?, result_json = ?, error = ?, finished_at = ?, lease_until = NULL"
                           " WHERE id = ? AND agent_id = ? AND state IN ('claimed', 'running')",
                           ("done" if ok else "failed", json.dumps(result) if result is not None else None, error, time.time(),
                            job_id, agent_id))
    return cur.rowcount > 0


def expire_agent_jobs(now: float | None = None) -> list[dict]:
    """Jobs whose lease ran out (the agent died, lost its connection or was closed): back to the queue, or failed after
    MAX_ATTEMPTS. Returns [{id, shop_id, state}] of what changed, so the caller can fail the shop of a job that gave up."""
    now = now or time.time()
    changed = []
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT id, shop_id, attempts FROM agent_jobs WHERE state IN ('claimed', 'running') AND lease_until < ?",
                            (now,)).fetchall()
        for r in rows:
            if r["attempts"] >= MAX_ATTEMPTS:
                conn.execute("UPDATE agent_jobs SET state = 'failed', error = ?, finished_at = ?, lease_until = NULL WHERE id = ?",
                             ("the agent stopped responding and the job ran out of retries", now, r["id"]))
                changed.append({"id": r["id"], "shop_id": r["shop_id"], "state": "failed"})
            else:
                conn.execute("UPDATE agent_jobs SET state = 'queued', agent_id = NULL, lease_until = NULL, step = NULL WHERE id = ?", (r["id"],))
                changed.append({"id": r["id"], "shop_id": r["shop_id"], "state": "queued"})
    return changed


def queue_font_request(user_id: int, family: str, incoming_base: str) -> str:
    """Ask the user's agent for the font FILE of `family` (the editor needs it to draw text in the board's real font). A family already
    asked for - queued, claimed or running - is not asked twice, and families asked for close together share ONE job (the agent polls every
    few seconds; one job per family would take that long each). Returns the job id."""
    import os
    import uuid

    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for row in conn.execute("SELECT id, state, payload_json FROM agent_jobs WHERE kind = 'font_files' AND user_id = ?"
                                " AND state IN ('queued', 'claimed', 'running') ORDER BY created_at", (user_id,)).fetchall():
            payload = json.loads(row["payload_json"])
            families = payload["spec"]["families"]
            if family in families:
                return row["id"]
            if row["state"] == "queued":
                families.append(family)
                conn.execute("UPDATE agent_jobs SET payload_json = ? WHERE id = ?", (json.dumps(payload), row["id"]))
                return row["id"]
        job_id = uuid.uuid4().hex[:12]
        payload = {"spec": {"families": [family]}, "out_dir": os.path.join(incoming_base, job_id)}
        conn.execute("INSERT INTO agent_jobs (id, kind, user_id, payload_json, created_at) VALUES (?, 'font_files', ?, ?, ?)",
                     (job_id, user_id, json.dumps(payload), time.time()))
    return job_id


def cancel_active_agent_jobs(shop_id: str, reason: str) -> int:
    """The user stopped this shop: its waiting / running agent jobs become `cancelled` (not `failed` - a stop is not an error, and the
    agent that holds one is told so on its next progress call and stops CorelDRAW)."""
    with _conn() as conn:
        return conn.execute("UPDATE agent_jobs SET state = 'cancelled', error = ?, finished_at = ?, lease_until = NULL"
                            " WHERE shop_id = ? AND state IN ('queued', 'claimed', 'running')", (reason, time.time(), shop_id)).rowcount


def fail_active_agent_jobs(shop_id: str, reason: str) -> int:
    with _conn() as conn:
        return conn.execute("UPDATE agent_jobs SET state = 'failed', error = ?, finished_at = ?, lease_until = NULL"
                            " WHERE shop_id = ? AND state IN ('queued', 'claimed', 'running')", (reason, time.time(), shop_id)).rowcount


# ------------------------------------------------------------ audit log (app/audit.py) + export clean-up

def add_audit(user_id: int | None, email: str, action: str, target: str, detail: dict, ip: str) -> None:
    with _conn() as conn:
        conn.execute("INSERT INTO audit_log (at, user_id, user_email, action, target, detail_json, ip) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (time.time(), user_id, email, action, target or "", json.dumps(detail, default=str) if detail else None, ip))


def list_audit(limit: int = 100, offset: int = 0, action: str = "", q: str = "") -> tuple[list[dict], int]:
    """(entries newest first, total matching). `action` = exactly that action; `q` = text in the user, target or details."""
    where, args = [], []
    if action:
        where.append("action = ?")
        args.append(action)
    if q:
        where.append("(user_email LIKE ? OR target LIKE ? OR detail_json LIKE ? OR ip LIKE ?)")
        args += [f"%{q}%"] * 4
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with _conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM audit_log{clause}", args).fetchone()["n"]
        rows = conn.execute(f"SELECT * FROM audit_log{clause} ORDER BY at DESC, id DESC LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d.pop("detail_json")) if d.get("detail_json") else {}
        d.pop("detail_json", None)
        out.append(d)
    return out, total


def audit_actions() -> list[str]:
    with _conn() as conn:
        return [r["action"] for r in conn.execute("SELECT DISTINCT action FROM audit_log ORDER BY action").fetchall()]


def purge_audit(older_than_s: float) -> int:
    with _conn() as conn:
        return conn.execute("DELETE FROM audit_log WHERE at < ?", (time.time() - older_than_s,)).rowcount


def delete_exports(ids: list[str]) -> int:
    """Forget export rows whose files were cleaned up."""
    if not ids:
        return 0
    with _conn() as conn:
        return conn.execute(f"DELETE FROM exports WHERE id IN ({','.join('?' * len(ids))})", ids).rowcount

