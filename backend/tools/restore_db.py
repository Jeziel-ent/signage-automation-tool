"""Put a database backup back (the site must be STOPPED first).

    python tools/restore_db.py /data/backups/signage-20261108-030000.db --data /data --yes
    python tools/restore_db.py --latest --data /data --yes        # the newest copy in <data>/backups

The backup is checked first (SQLite's own integrity check); the database it replaces is kept next to it as signage.db.pre-restore-<time>, and
the write-ahead files of the old database are moved away so they cannot be replayed onto the restored one.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup_db import verify  # noqa: E402


def restore(backup: Path, data_dir: Path, now: float | None = None) -> dict:
    """Verify `backup` and make it `<data_dir>/signage.db`. Returns {restored, kept_old, tables}."""
    info = verify(backup)                                    # raises when the copy is damaged: nothing is touched
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "signage.db"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    kept = None
    if target.exists():
        kept = data_dir / f"signage.db.pre-restore-{stamp}"
        shutil.move(str(target), str(kept))
    for suffix in ("-wal", "-shm"):
        side = data_dir / f"signage.db{suffix}"
        if side.exists():
            shutil.move(str(side), str(data_dir / f"signage.db{suffix}.pre-restore-{stamp}"))
    shutil.copy2(backup, target)
    after = verify(target)
    return {"restored": str(target), "kept_old": str(kept) if kept else None, "tables": after["tables"], "from": info["tables"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("backup", nargs="?", type=Path)
    ap.add_argument("--latest", action="store_true", help="use the newest backup in <data>/backups")
    ap.add_argument("--data", type=Path, default=Path(os.environ.get("SIGNAGE_DATA", "data")))
    ap.add_argument("--yes", action="store_true", help="really replace the database (the site must be stopped)")
    args = ap.parse_args()
    backup = args.backup
    if args.latest:
        found = sorted((args.data / "backups").glob("signage-*.db"))
        if not found:
            print(f"no backups in {args.data / 'backups'}", file=sys.stderr)
            return 1
        backup = found[-1]
    if backup is None:
        ap.error("name a backup file or use --latest")
    try:
        info = verify(backup)
    except Exception as e:  # noqa: BLE001
        print(f"this backup is not usable: {e}", file=sys.stderr)
        return 1
    print(f"{backup.name}: integrity ok, tables: {info['tables']}")
    if not args.yes:
        print("Dry run. Stop the site, then run again with --yes to replace the database.")
        return 0
    result = restore(backup, args.data)
    print(f"restored to {result['restored']}" + (f"; the old database is kept as {result['kept_old']}" if result["kept_old"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
