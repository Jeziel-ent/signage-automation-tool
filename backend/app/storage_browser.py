"""The admin page's file browser: every file the site keeps on this server, by what it is - masters (by brand), designers' output
(per board, by the day it was made), database backups and the rest - with sizes, and the deletes behind its buttons.

What is learned (Corel Intelligence) never depends on these files: corrections are JSON records in the database (table `corrections`,
keyed by shop id), so deleting a board's files - or the board itself - keeps everything it taught."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import db

# the board's own output folder holds these besides its files; they are listed (and deletable) separately
SUBFOLDERS = {"scene": "Editor scene", "exports": "Edited exports", "product_assets": "Product images"}
_SAFE = re.compile(r"^[A-Za-z0-9._ ()&'+,-]+$")


def _size(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def _kind(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if name.endswith("_report.json") or ext == "json":
        return "report"
    return {"cdr": "cdr", "pdf": "pdf", "png": "png", "jpg": "jpg", "jpeg": "jpg", "svg": "svg", "webp": "png"}.get(ext, "other")


def listing(data: Path, label=lambda row: row.get("master_name") or row.get("master_filename") or "") -> dict:
    """Everything on the disk the page can show. `label(job_row)` names a master as the rest of the site does."""
    data = Path(data)
    jobs_root = data / "jobs_v2"
    with db._conn() as conn:
        jobs = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM jobs")}
        shops = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM shops")}
        learned = {r[0] for r in conn.execute("SELECT shop_id FROM corrections")}
        made_by = {r["shop_id"]: dict(r) for r in conn.execute(
            "SELECT j.shop_id, u.email AS user_email, a.name AS pc FROM agent_jobs j LEFT JOIN users u ON u.id = j.user_id "
            "LEFT JOIN agents a ON a.id = j.agent_id WHERE j.kind = 'convert' AND j.state = 'done' "
            "AND j.created_at = (SELECT MAX(created_at) FROM agent_jobs WHERE shop_id = j.shop_id AND kind = 'convert' AND state = 'done')")}
    boards_per_master: dict[str, int] = {}
    for s in shops.values():
        if s["status"] != "done":
            continue
        used = (json.loads(s["report_json"]).get("master_used") or {}).get("job_id") if s.get("report_json") else None
        key = used or s["job_id"]
        boards_per_master[key] = boards_per_master.get(key, 0) + 1

    masters, boards, orphans = [], [], []
    if jobs_root.is_dir():
        for jdir in sorted(jobs_root.iterdir()):
            if not jdir.is_dir():
                continue
            job = jobs.get(jdir.name)
            files = [f for f in jdir.glob("master.cdr*") if f.is_file()] + [f for f in (jdir / "preview.png",) if f.is_file()]
            if files:
                masters.append({
                    "id": jdir.name, "brand": (job or {}).get("brand") or "?", "name": label(job) if job else "(not in the database)",
                    "file_name": (job or {}).get("master_filename") or "master.cdr", "orientation": (job or {}).get("orientation") or "",
                    "bytes": sum(_size(f) for f in files), "created_at": (job or {}).get("created_at") or files[0].stat().st_mtime,
                    "deleted": bool((job or {}).get("deleted_at")) or job is None, "boards": boards_per_master.get(jdir.name, 0),
                    "has_preview": (jdir / "preview.png").is_file(),
                })
            out_root = jdir / "out"
            if not out_root.is_dir():
                continue
            for sdir in sorted(out_root.iterdir()):
                if not sdir.is_dir():
                    continue
                shop = shops.get(sdir.name)
                own = [f for f in sdir.iterdir() if f.is_file()]
                parts = {k: _size(sdir / k) for k in SUBFOLDERS if (sdir / k).is_dir()}
                total = sum(f.stat().st_size for f in own) + sum(parts.values())
                if shop is None:
                    orphans.append({"job_id": jdir.name, "folder": sdir.name, "bytes": total})
                    continue
                by = made_by.get(shop["id"]) or {}
                boards.append({
                    "shop_id": shop["id"], "job_id": shop["job_id"], "brand": (job or {}).get("brand") or "?", "name": shop["name"],
                    "size": f"{shop['width']:g} {shop['width_unit']} x {shop['height']:g} {shop['height_unit']}", "status": shop["status"],
                    "made_at": shop.get("completed_at") or shop["created_at"], "user_email": by.get("user_email"), "pc": by.get("pc"),
                    "files": sorted(({"name": f.name, "kind": _kind(f.name), "bytes": f.stat().st_size} for f in own), key=lambda f: f["name"]),
                    "parts": parts, "bytes": total, "learned": shop["id"] in learned,
                })
    backups = []
    bdir = data / "backups"
    if bdir.is_dir():
        backups = sorted(({"name": f.name, "bytes": f.stat().st_size, "at": f.stat().st_mtime} for f in bdir.iterdir() if f.is_file()),
                         key=lambda b: -b["at"])
    other = {name: _size(data / name) for name in ("shared_zips", "convert_runs", "agent_fonts") if (data / name).is_dir()}
    database = sum(_size(f) for f in data.glob("signage.db*"))
    return {"masters": sorted(masters, key=lambda m: (m["brand"].lower(), -m["created_at"])),
            "boards": sorted(boards, key=lambda b: -b["made_at"]), "orphans": orphans, "backups": backups,
            "other": other, "database_bytes": database, "learned_records": len(learned)}


class Busy(Exception):
    """The thing to delete is in use right now (a board converting, a master a waiting board will use)."""


def _rm(p: Path) -> int:
    n = _size(p)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        p.unlink(missing_ok=True)
    return n


def delete_master_files(data: Path, job_id: str) -> int:
    """The master .cdr (and its preview) go; the boards made from it stay. The master leaves the registry like the Masters page's
    Delete. Refused while a board that uses it waits or converts."""
    jdir = Path(data) / "jobs_v2" / job_id
    if not jdir.is_dir():
        raise FileNotFoundError(job_id)
    with db._conn() as conn:
        busy = conn.execute("SELECT COUNT(*) FROM shops WHERE status IN ('queued', 'converting') AND (job_id = ? OR master_id = ? "
                            "OR landscape_master_id = ? OR portrait_master_id = ?)", (job_id,) * 4).fetchone()[0]
    if busy:
        raise Busy("a board that uses this master is waiting or converting")
    freed = sum(_rm(f) for f in jdir.glob("master.cdr*")) + _rm(jdir / "preview.png")
    if db.get_job(job_id):
        db.delete_master(job_id)
    return freed


def delete_board(data: Path, shop_id: str, part: str = "all") -> int:
    """part = all: the board's whole folder and its row (Recently generated forgets it; its learned corrections stay);
    scene / exports / product_assets: only that subfolder (an editor scene is rebuilt when the board is opened again)."""
    shop = db.get_shop(shop_id)
    if not shop:
        raise FileNotFoundError(shop_id)
    if shop["status"] in ("queued", "converting"):
        raise Busy("the board is converting - stop it first")
    jdir = Path(data) / "jobs_v2" / shop["job_id"]
    sdir = jdir / "out" / shop_id
    if part == "all":
        freed = _rm(sdir) + sum(_rm(f) for f in (jdir / "thumbs").glob(f"{shop_id}*"))
        db.delete_shop(shop_id)                  # editor ops / exports / font rows go with it; the corrections table is not touched
        return freed
    if part not in SUBFOLDERS:
        raise ValueError(part)
    freed = _rm(sdir / part)
    if part == "exports":
        with db._conn() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM exports WHERE shop_id = ?", (shop_id,))]
        db.delete_exports(ids)
    return freed


def delete_orphan(data: Path, job_id: str, folder: str) -> int:
    """A board folder whose shop row is gone (left by an earlier delete)."""
    if db.get_shop(folder):
        raise Busy("this folder still belongs to a board")
    p = Path(data) / "jobs_v2" / job_id / "out" / folder
    if not (_SAFE.match(job_id) and _SAFE.match(folder)) or not p.is_dir():
        raise FileNotFoundError(folder)
    return _rm(p)


def delete_backup(data: Path, name: str) -> int:
    bdir = (Path(data) / "backups").resolve()
    p = (bdir / name).resolve()
    if not _SAFE.match(name) or p.parent != bdir or not p.is_file():
        raise FileNotFoundError(name)
    return _rm(p)
