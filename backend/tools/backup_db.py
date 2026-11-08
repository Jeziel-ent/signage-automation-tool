"""A consistent copy of the website's SQLite database (safe while the site is running), keeping the newest few.

    python tools/backup_db.py --out /data/backups --keep 14
    python tools/backup_db.py --db D:\\signage\\data\\signage.db --out D:\\signage\\backups

Uses SQLite's own online backup, so the copy is whole even while designers are saving (the database is in write-ahead mode). Restoring =
stop the site, copy a backup over `<SIGNAGE_DATA>/signage.db` (and delete signage.db-wal / -shm next to it), start the site.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path


def default_db() -> Path:
    return Path(os.environ.get("SIGNAGE_DATA", Path(__file__).resolve().parents[1] / "data")) / "signage.db"


def verify(path: Path) -> dict:
    """SQLite's own integrity check of a database file + the row count of every table. Raises ValueError when the file is damaged."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchall()
        if result != [("ok",)]:
            raise ValueError(f"integrity check failed: {result[:3]}")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        return {"tables": {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}}
    except sqlite3.DatabaseError as e:
        raise ValueError(f"not a usable database: {e}") from None
    finally:
        conn.close()


def backup(db_path: Path, out_dir: Path, keep: int = 14, now: float | None = None) -> Path:
    """Write signage-YYYYmmdd-HHMMSS.db into `out_dir` (checked: a damaged copy is deleted and ValueError raised), delete older backups
    beyond `keep`, return the new file."""
    if not db_path.is_file():
        raise FileNotFoundError(f"no database at {db_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    target = out_dir / f"signage-{stamp}.db"
    tmp = target.with_suffix(".db.part")
    src = sqlite3.connect(db_path, timeout=60)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")      # a plain single-file copy: no -wal/-shm left beside it
        finally:
            dst.close()
    finally:
        src.close()
    try:
        verify(tmp)
    except ValueError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(target)
    for stray in out_dir.glob("*.part-*"):                  # leftovers of earlier versions of this tool
        stray.unlink(missing_ok=True)
    old = sorted(out_dir.glob("signage-*.db"))
    for p in old[:-keep] if keep > 0 else []:
        p.unlink(missing_ok=True)
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=None, help="the database (default: <SIGNAGE_DATA>/signage.db)")
    ap.add_argument("--out", type=Path, required=True, help="folder for the copies")
    ap.add_argument("--keep", type=int, default=14, help="how many copies to keep (default 14)")
    args = ap.parse_args()
    try:
        path = backup(args.db or default_db(), args.out, args.keep)
    except (OSError, sqlite3.Error, ValueError) as e:
        print(f"backup failed: {e}", file=sys.stderr)
        return 1
    print(f"backup written: {path} ({path.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
