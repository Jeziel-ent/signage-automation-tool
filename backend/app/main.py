from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import logging

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import corel_supervisor, corel_util, db, export_replay, fonts, orientation_adapter, scene_export, scene_ops
from . import confidence, corrections, file_naming
from .batch_import import clean_shop_name, parse_shop_lines, shop_name_from_filename, strip_copy_suffix
from .engines import get_engine
from .layout import to_mm

# repeated literals (messages, media types, file names) - one definition each
_SHOP_NOT_FOUND = "shop not found"
_JOB_NOT_FOUND = "job not found"
_MEDIA_ZIP = "application/zip"
_MEDIA_JPEG = "image/jpeg"
_HEARTBEAT_SUFFIX = ".heartbeat"
_DONE_SUFFIX = ".done"
_JPEG_SUFFIX = ".jpeg"
_WEBP_SUFFIX = ".webp"
_SCENE_JSON = "scene.json"
_META_JSON = "meta.json"
_MASTER_CDR = "master.cdr"

DATA = Path(os.environ.get("SIGNAGE_DATA", Path(__file__).resolve().parents[1] / "data"))
JOBS = DATA / "jobs"
BRANDS_FILE = DATA / "brands.json"
JOBS.mkdir(parents=True, exist_ok=True)

# New UI (see CLAUDE.md-adjacent phase plan): SQLite-backed jobs/shops under
# their own directory, separate from the old in-memory `_jobs` dict and its
# JOBS folder below - the old endpoints are left exactly as they were for
# whatever still uses them; this is an addition, not a migration.
JOBS_V2 = DATA / "jobs_v2"
CONVERT_RUNS = DATA / "convert_runs"  # ephemeral corel_supervisor results/heartbeat files, one per shop
JOBS_V2.mkdir(parents=True, exist_ok=True)
CONVERT_RUNS.mkdir(parents=True, exist_ok=True)
db.init_db()

logger = logging.getLogger("signage.convert")

INTERRUPTED_ERROR = "Interrupted: the server was restarted while this was queued or running - convert it again."


def _recover_interrupted_work() -> None:
    """Queued/converting shops and queued/running exports left over from the previous server process can never finish
    (their worker queue died with it): fail them with a clear reason so the page stops polling them, and drop that
    process's leftover supervisor files (a stale heartbeat would otherwise be read as the progress of a new run)."""
    shops, exports = db.fail_interrupted_work(INTERRUPTED_ERROR)
    for p in CONVERT_RUNS.glob("*"):
        try:
            p.unlink()
        except OSError:
            pass
    if shops or exports:
        logger.warning("startup: marked %d shop(s) and %d export(s) interrupted by the previous server run as failed",
                       shops, exports)


def _archive_previous_masters() -> None:
    """Every server start begins with an empty Master Templates list: masters uploaded in an earlier run are hidden (soft-deleted, so the
    boards already made from them and their files stay) and a designer uploads again, starting at Master 1. Set SIGNAGE_KEEP_MASTERS=1 to
    keep them listed across restarts (a hosted install, where masters are shared and long-lived)."""
    if os.environ.get("SIGNAGE_KEEP_MASTERS", "").strip() in ("1", "true", "yes"):
        return
    hidden = db.archive_registered_masters()
    if hidden:
        logger.info("startup: %d master template(s) from the previous run were hidden (SIGNAGE_KEEP_MASTERS=1 keeps them)", hidden)


@asynccontextmanager
async def _lifespan(_app):
    _recover_interrupted_work()
    _archive_previous_masters()
    yield


app = FastAPI(title="signage-automation-tool", lifespan=_lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# CorelDRAW is a single-instance desktop app: process one job at a time.
_pool = ThreadPoolExecutor(max_workers=1)
_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def _brands() -> list[str]:
    if BRANDS_FILE.exists():
        return json.loads(BRANDS_FILE.read_text())
    return []


@app.get("/api/health")
def health():
    return {"ok": True, "engine": get_engine(os.environ.get("SIGNAGE_ENGINE", "auto")).name}


@app.get("/api/brands")
def list_brands():
    return _brands()


@app.post("/api/brands", responses={400: {"description": "Invalid request"}})
def add_brand(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    b = _brands()
    if name not in b:
        b.append(name)
        BRANDS_FILE.write_text(json.dumps(sorted(b), indent=2))
    return sorted(set(b))


@app.post("/api/parse-shops")
def parse_shops(payload: dict):
    """Paste designer filename-style lines, get back shop rows.

    e.g. "16 - 12 X 4 Feet - Nonlit - AL MADEENA POOJA STORE" ->
    {name: "AL MADEENA POOJA STORE", width: 12, height: 4, unit: "ft", type: "Nonlit"}
    """
    text = payload.get("text") or ""
    result = parse_shop_lines(text)
    return {
        "shops": [
            {"name": s.name, "width": s.width, "height": s.height, "unit": s.unit, "type": s.type}
            for s in result.shops
        ],
        "errors": result.errors,
    }


def _run(job_id: str):
    job = _jobs[job_id]
    engine = get_engine(os.environ.get("SIGNAGE_ENGINE", "auto"))
    job["engine"] = engine.name
    job["status"] = "running"
    jdir = JOBS / job_id
    for i, shop in enumerate(job["shops"]):
        res = job["results"][i]
        res["status"] = "running"
        try:
            shop = {**shop, "brand": job["brand"]}  # lets the engine load app/brand_rules/<brand>.json
            out = engine.process(jdir / _MASTER_CDR, shop, jdir / "out" / f"{i+1:02d}")
            res.update(status="done", files=out["files"], report=out["report"], note=out.get("note"))
        except Exception as e:  # keep going with the other shops
            res.update(status="error", error=str(e))
    job["status"] = "done" if all(r["status"] == "done" for r in job["results"]) else "error"


@app.post("/api/jobs", responses={400: {"description": "Invalid request"}})
async def create_job(
    master: UploadFile = File(...),
    brand: str = Form(...),
    shops: str = Form(...),
):
    if not (master.filename or "").lower().endswith(".cdr"):
        raise HTTPException(400, "Master file must be a .cdr")
    try:
        shop_list = json.loads(shops)
        if not (isinstance(shop_list, list) and shop_list):
            raise ValueError("shops must be a non-empty list")
        for s in shop_list:
            # optional per-shop content fields (see CLAUDE.md "Per-shop content
            # replacement"): shop_name_local (Tamil display name), phone, gst,
            # address_lines (list[str]) - all freeform, engine treats missing/empty
            # as "don't touch that line"
            if not (s["name"].strip() and float(s["width"]) > 0 and float(s["height"]) > 0
                    and s["unit"] in ("mm", "cm", "in", "ft", "m")):
                raise ValueError("invalid shop")
    except Exception:
        raise HTTPException(400, "Invalid shops: each needs name, width>0, height>0, unit")

    job_id = uuid.uuid4().hex[:12]
    jdir = JOBS / job_id
    jdir.mkdir(parents=True)
    def _save_master() -> None:
        with open(jdir / _MASTER_CDR, "wb") as f:
            shutil.copyfileobj(master.file, f)

    await asyncio.to_thread(_save_master)  # a 300 MB copy must not block the event loop

    _jobs[job_id] = {
        "id": job_id, "brand": brand, "master": master.filename, "status": "queued",
        "shops": shop_list,
        "results": [{"shop": s["name"], "status": "queued"} for s in shop_list],
    }
    _pool.submit(_run, job_id)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}", responses={404: {"description": "Not found"}})
def get_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, _JOB_NOT_FOUND)
    return _jobs[job_id]


@app.get("/api/jobs/{job_id}/files/{idx}/{filename}", responses={404: {"description": "Not found"}})
def get_file(job_id: str, idx: int, filename: str):
    p = (JOBS / job_id / "out" / f"{idx:02d}" / filename).resolve()
    if JOBS.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/jobs/{job_id}/download.zip", responses={404: {"description": "Not found"}})
def download_zip(job_id: str):
    out = JOBS / job_id / "out"
    if not out.exists():
        raise HTTPException(404)
    job = _jobs.get(job_id, {})
    zpath = JOBS / job_id / f"{job.get('brand', 'output')}_signage.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for sub in sorted(out.iterdir()):
            for f in sub.iterdir():
                z.write(f, f"{sub.name}/{f.name}")
    return FileResponse(zpath, filename=zpath.name)


# ============================================================================
# New UI (Phase A): upload -> instant preview -> add shops one at a time ->
# convert each independently. SQLite-backed (see app/db.py) so the shop list
# and any completed results survive a server restart. Reuses the existing
# CorelEngine/corel_supervisor/single-worker pool unchanged - no engine
# layout logic is touched here.

@app.get("/api/v2/brands")
def v2_list_brands():
    return db.list_brands()


@app.post("/api/v2/brands", responses={400: {"description": "Invalid request"}})
def v2_add_brand(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    return db.add_brand(name)


def _extract_cdr_preview(master_path: Path) -> tuple[Path | None, str | None]:
    """CorelDRAW files from X4 onward are zip archives with a ready-made
    `previews/page1.png` inside - confirmed against a real generated .cdr
    (see CLAUDE.md-adjacent phase notes). Extracting it needs no CorelDRAW
    at all, so the UI can show a preview the instant upload finishes,
    before any conversion job even starts.
    """
    try:
        with zipfile.ZipFile(master_path) as z:
            names = z.namelist()
            candidate = next((n for n in names if n.lower() == "previews/page1.png"), None) \
                or next((n for n in names if n.lower().endswith("page1.png")), None) \
                or next((n for n in names if "preview" in n.lower() and n.lower().endswith(".png")), None)
            if not candidate:
                return None, "no preview image found inside this .cdr"
            data = z.read(candidate)
    except zipfile.BadZipFile:
        return None, "this .cdr isn't a zip-based file (pre-X4 format?) - no instant preview available"
    except Exception as e:
        return None, f"could not read preview: {e}"

    preview_path = master_path.parent / "preview.png"
    preview_path.write_bytes(data)
    return preview_path, None


MASTER_ORIENTATIONS = ("landscape", "portrait")
MASTER_NAME_MAX = 120


def _parse_default_size(raw: str) -> tuple[float, float, str] | None:
    """`dimensions_default` form field: JSON {width, height, unit} or blank. Partial or invalid -> 400."""
    if not (raw or "").strip():
        return None
    try:
        d = json.loads(raw)
        width, height, unit = float(d["width"]), float(d["height"]), str(d.get("unit") or "in")
    except (ValueError, TypeError, KeyError):
        raise HTTPException(400, "dimensions_default must be JSON {width, height, unit}")
    if not (width > 0 and height > 0) or unit not in ("mm", "cm", "in", "ft"):
        raise HTTPException(400, "dimensions_default needs width>0, height>0 and unit in mm/cm/in/ft")
    return width, height, unit


def _save_master_upload(master: UploadFile, brand: str, orientation: str, master_shop_name: str,
                        master_shop_name_local: str, name: str = "",
                        default_size: tuple[float, float, str] | None = None) -> str:
    """Store an uploaded master .cdr as a new registered `jobs` row (+ its instant preview); returns its id."""
    if not (master.filename or "").lower().endswith(".cdr"):
        raise HTTPException(400, "Master file must be a .cdr")
    orientation = orientation.strip().lower()
    if orientation not in MASTER_ORIENTATIONS:
        raise HTTPException(400, "orientation must be 'landscape' or 'portrait'")
    brand = brand.strip()
    if not brand:
        raise HTTPException(400, "brand required")
    name = name.strip()
    if len(name) > MASTER_NAME_MAX:
        raise HTTPException(400, f"name must be at most {MASTER_NAME_MAX} characters")
    # a blank name stays blank: it is shown as "Master 1", "Master 2" ... by position among this brand's live masters of this
    # orientation (_master_label), so every brand + orientation starts at Master 1 and deleting one renumbers the rest

    job_id = uuid.uuid4().hex[:12]
    jdir = JOBS_V2 / job_id
    jdir.mkdir(parents=True)
    master_path = jdir / _MASTER_CDR
    # UploadFile spools large files to disk itself (SpooledTemporaryFile) -
    # this copy is a plain streamed write, fine up to the 300MB target the
    # UI's upload progress bar is sized for.
    with open(master_path, "wb") as f:
        shutil.copyfileobj(master.file, f)

    db.create_job(job_id, brand, master.filename, str(master_path), orientation,
                  master_shop_name.strip() or shop_name_from_filename(master.filename),
                  master_shop_name_local.strip() or None, master_name=name or None, default_size=default_size,
                  registered=True)
    preview_path, preview_error = _extract_cdr_preview(master_path)
    db.set_job_preview(job_id, str(preview_path) if preview_path else None, preview_error)
    return job_id


_AUTO_MASTER_NAME = re.compile(r"^Master \d+$")


def _master_label(row: dict) -> str:
    """A master's display name. A name the designer typed is shown as typed; a blank one - or an automatic "Master 7" stored by an
    earlier version - is numbered by position among the brand's live (not deleted) automatically named masters of the same orientation,
    oldest first: Master 1, Master 2 ... Every brand + orientation starts at 1, and deleting one renumbers the ones after it."""
    stored = (row.get("master_name") or "").strip()
    if stored and not _AUTO_MASTER_NAME.match(stored):
        return stored
    if not row.get("registered"):
        return stored or row.get("master_filename") or ""
    live = [m for m in db.list_masters(row["brand"], row.get("orientation") or "landscape")
            if not (m.get("master_name") or "").strip() or _AUTO_MASTER_NAME.match(m["master_name"].strip())]
    ids = [m["id"] for m in live]
    return f"Master {ids.index(row['id']) + 1 if row['id'] in ids else len(ids) + 1}"


def _master_json(row: dict) -> dict:
    """A master template as the /api/masters routes return it."""
    path = Path(row["master_path"])
    dims = None
    if row.get("default_width") and row.get("default_height"):
        dims = {"width": row["default_width"], "height": row["default_height"], "unit": row.get("default_unit") or "in"}
    return {
        "id": row["id"],
        "name": _master_label(row),
        "orientation": row.get("orientation") or "landscape",
        "brand": row["brand"],
        "file_name": row["master_filename"],
        "file_path": str(path),
        "file_size": path.stat().st_size if path.exists() else None,
        "dimensions_default": dims,
        "master_shop_name": row.get("master_shop_name"),
        "preview_url": f"/api/v2/jobs/{row['id']}/preview" if row.get("preview_path") else None,
        "preview_error": row.get("preview_error"),
        "created_at": datetime.fromtimestamp(row["created_at"], timezone.utc).isoformat(),
    }


@app.post("/api/v2/upload", responses={400: {"description": "Invalid request"}})
async def v2_upload(master: UploadFile = File(...), brand: str = Form(...), orientation: str = Form("landscape"),
                    master_shop_name: str = Form(""), master_shop_name_local: str = Form("")):
    """`master_shop_name` / `master_shop_name_local`: the shop name the master itself shows (English / local script),
    which is how conversion finds the text to overwrite on an untagged master. Optional - the English one defaults to
    the shop name in a designer-style file name ("<code> - W X H unit - type - SHOP NAME.cdr"). The master is also
    added to the registry (/api/masters) under a default "Master N" name."""
    job_id = _save_master_upload(master, brand, orientation, master_shop_name, master_shop_name_local)
    job = db.get_job(job_id)
    return {
        "id": job_id,
        "orientation": job["orientation"],
        "master_shop_name": job.get("master_shop_name"),
        "preview_url": f"/api/v2/jobs/{job_id}/preview" if job.get("preview_path") else None,
        "preview_error": job.get("preview_error"),
    }


@app.get("/api/masters", responses={400: {"description": "Invalid request"}})
def list_masters(brand: str | None = None, orientation: str | None = None):
    """Registered master templates (any number per orientation), oldest first - the first of each orientation is its
    default. Optional `brand` / `orientation` filters. Returns {masters, landscape, portrait}."""
    if orientation is not None and orientation not in MASTER_ORIENTATIONS:
        raise HTTPException(400, "orientation must be 'landscape' or 'portrait'")
    masters = [_master_json(r) for r in db.list_masters(brand, orientation)]
    return {"masters": masters,
            "landscape": [m for m in masters if m["orientation"] == "landscape"],
            "portrait": [m for m in masters if m["orientation"] == "portrait"]}


@app.post("/api/masters/upload", responses={400: {"description": "Invalid request"}})
async def upload_master(master: UploadFile = File(...), brand: str = Form(...), orientation: str = Form(...),
                        name: str = Form(""), dimensions_default: str = Form(""),
                        master_shop_name: str = Form(""), master_shop_name_local: str = Form("")):
    """Register a new master template: the .cdr plus `name` (default "Master N"), `orientation` and an optional
    `dimensions_default` (JSON {width, height, unit})."""
    default_size = _parse_default_size(dimensions_default)
    job_id = _save_master_upload(master, brand, orientation, master_shop_name, master_shop_name_local, name, default_size)
    return _master_json(db.get_job(job_id))


@app.delete("/api/masters/{master_id}", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def delete_master(master_id: str):
    """Remove a master from the registry. Soft delete: the file stays, because the boards already made from it live in
    its folder (Recently generated, the editor). Refused while a shop that uses it is queued or converting."""
    row = db.get_job(master_id)
    if not row or not row.get("registered") or row.get("deleted_at"):
        raise HTTPException(404, "master not found")
    if db.count_active_shops_using_master(master_id):
        raise HTTPException(409, "a shop using this master is converting - try again when it has finished")
    db.delete_master(master_id)
    return {"deleted": master_id}


@app.patch("/api/v2/jobs/{job_id}/master-shop-name", responses={404: {"description": "Not found"}})
def v2_set_master_shop_name(job_id: str, payload: dict):
    """Set / correct the shop name a master shows ({master_shop_name, master_shop_name_local}; blank clears)."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, _JOB_NOT_FOUND)
    val = lambda k: (str(payload.get(k) or "")).strip() or None  # noqa: E731
    db.set_job_master_shop_names(job_id, val("master_shop_name") if "master_shop_name" in payload else job.get("master_shop_name"),
                                 val("master_shop_name_local") if "master_shop_name_local" in payload
                                 else job.get("master_shop_name_local"))
    return db.get_job(job_id)


@app.get("/api/v2/jobs/{job_id}", responses={404: {"description": "Not found"}})
def v2_get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, _JOB_NOT_FOUND)
    job["preview_url"] = f"/api/v2/jobs/{job_id}/preview" if job.get("preview_path") else None
    # without each shop's report_json (every placed object's geometry): 1000 shops made this 9 MB. Reports stay
    # available per shop from /api/v2/shops/{id}/status.
    job["shops"] = [{k: v for k, v in s.items() if k != "report_json"} for s in db.list_shops(job_id)]
    return job


@app.get("/api/v2/jobs/{job_id}/preview", responses={404: {"description": "Not found"}})
def v2_job_preview(job_id: str):
    job = db.get_job(job_id)
    if not job or not job.get("preview_path"):
        raise HTTPException(404, "no preview available")
    return FileResponse(job["preview_path"])


def _validated_master_ids(payload: dict, job: dict) -> dict:
    """`landscape_master_id` / `portrait_master_id` from a request body: each must exist (404), belong to the job's
    brand and have been uploaded as the orientation of its slot (400). Missing/blank -> None."""
    out = {}
    for key, want in (("landscape_master_id", "landscape"), ("portrait_master_id", "portrait")):
        mid = (payload.get(key) or "").strip() or None
        if mid:
            mrow = db.get_job(mid)
            if not mrow:
                raise HTTPException(404, f"{key} not found")
            if mrow["brand"] != job["brand"]:
                raise HTTPException(400, f"{key} belongs to brand {mrow['brand']!r}, not {job['brand']!r}")
            if (mrow.get("orientation") or "landscape") != want:
                raise HTTPException(400, f"{key} was uploaded as a {mrow.get('orientation')} master")
            if mrow.get("deleted_at"):
                raise HTTPException(400, f"{key} was deleted")
        out[key] = mid
    return out


def _validated_master_id(payload: dict, job: dict) -> str | None:
    """`master_id` from a request body - the master chosen in the queue's Master column. It must exist (404), not be
    deleted and belong to the job's brand (400). Its orientation is not checked here: the row's size may still change,
    and _select_shop_master falls back to the default master of the board's orientation when it does not match."""
    mid = (str(payload.get("master_id") or "")).strip() or None
    if mid:
        mrow = db.get_job(mid)
        if not mrow:
            raise HTTPException(404, "master_id not found")
        if mrow.get("deleted_at"):
            raise HTTPException(400, "master_id was deleted")
        if mrow["brand"] != job["brand"]:
            raise HTTPException(400, f"master_id belongs to brand {mrow['brand']!r}, not {job['brand']!r}")
    return mid


SHOP_LANGUAGES = ("both", "en", "ta")


def _parse_shop_payload(payload: dict) -> dict:
    """Validate one shop body (single add and every row of a batch share this). Raises ValueError with a
    readable reason. Optional contact fields go through `.strip() or None`: compute_layout treats None as
    "leave the master's own text alone" but "" as "replace with blank", so a blank must never reach the engine
    as an empty string (see CLAUDE.md "Per-shop content replacement")."""
    try:
        name = str(payload["name"]).strip()
        width = float(payload["width"])
        height = float(payload["height"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("need name, width>0, height>0")
    # `unit` (the Shops table's one shared unit) sets both dimensions; explicit width_unit/height_unit still win.
    shared = payload.get("unit") or "in"
    width_unit = payload.get("width_unit", shared)
    height_unit = payload.get("height_unit", shared)
    if not name or not (width > 0) or not (height > 0):
        raise ValueError("need name, width>0, height>0")
    if width_unit not in ("mm", "cm", "in", "ft") or height_unit not in ("mm", "cm", "in", "ft"):
        raise ValueError("unit must be one of mm/cm/in/ft")

    def opt(key):
        return (str(payload.get(key) or "")).strip() or None

    language = (opt("language") or "both").lower()
    if language not in SHOP_LANGUAGES:
        raise ValueError("language must be one of both / en / ta")

    return {"name": name, "width": width, "width_unit": width_unit, "height": height, "height_unit": height_unit,
            "reference": opt("reference"),
            # No upload UI for this yet (see CLAUDE.md "New UI") - accepted so the data model needs no migration later.
            "reference_file_path": opt("reference_file_path"),
            "phone": opt("phone"), "gst": opt("gst"), "address": opt("address"),
            # the local-script (e.g. Tamil) shop name, from an Excel import's "Shop Name (Tamil)"-style column
            "shop_name_local": opt("shop_name_local"),
            # "Type of board" (Nonlit / Frontlit / ...): shown in the table, part of every export's file name
            "board_type": opt("board_type"),
            # fonts for the replaced English / Tamil shop names (blank = the master's own / layout.TAMIL_FONT)
            "font_en": opt("font_en"), "font_ta": opt("font_ta"),
            # the Excel sheet the row came from (the Shops Queue's sheet tabs)
            "sheet_name": opt("sheet_name"),
            # which shop-name line(s) the board shows: both (default) / en / ta - engines.CorelEngine._apply_language
            "language": None if language == "both" else language}


def _insert_shop(job_id: str, fields: dict, master_ids: dict) -> dict:
    shop_id = uuid.uuid4().hex[:12]
    seq_no = len(db.list_shops(job_id)) + 1
    db.create_shop(shop_id, job_id, seq_no, fields["name"], fields["width"], fields["width_unit"], fields["height"],
                   fields["height_unit"], fields["reference"], fields["reference_file_path"], fields["phone"],
                   fields["gst"], fields["address"], master_ids["landscape_master_id"], master_ids["portrait_master_id"],
                   fields.get("shop_name_local"), fields.get("board_type"), fields.get("font_en"), fields.get("font_ta"),
                   fields.get("sheet_name"), fields.get("language"), master_ids.get("master_id"))
    return db.get_shop(shop_id)


@app.post("/api/v2/jobs/{job_id}/shops", responses={400: {"description": "Invalid request"}, 404: {"description": "Not found"}})
def v2_add_shop(job_id: str, payload: dict):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, _JOB_NOT_FOUND)
    try:
        fields = _parse_shop_payload(payload)
    except ValueError:
        raise HTTPException(400, "invalid shop: need name, width>0, height>0, unit in mm/cm/in/ft")
    # Dual-master templates (optional): the ids of two uploaded masters (each an /api/v2/upload job). They must
    # exist, belong to this job's brand and have been uploaded as the orientation they are used for.
    row = _insert_shop(job_id, fields, {**_validated_master_ids(payload, job),
                                        "master_id": _validated_master_id(payload, job)})
    _apply_sno(row["id"], payload)
    return db.get_shop(row["id"])


EDITABLE_SHOP_KEYS = ("name", "width", "width_unit", "height", "height_unit", "unit", "phone", "gst", "address",
                      "shop_name_local", "board_type", "font_en", "font_ta", "sheet_name", "language")


def _apply_shop_edits(shop_row: dict, payload: dict) -> None:
    """Merge the editable fields present in `payload` over the stored shop, validate the result exactly like an
    add, and save it. Only the keys the payload names change; a blank phone/gst/address clears that field."""
    merged = {k: shop_row.get(k) for k in EDITABLE_SHOP_KEYS if k != "unit"}
    merged.update({k: payload[k] for k in EDITABLE_SHOP_KEYS if k in payload})
    if "unit" in payload and "width_unit" not in payload and "height_unit" not in payload:
        merged["width_unit"] = merged["height_unit"] = payload["unit"] or "in"   # one shared unit
    try:
        fields = _parse_shop_payload(merged)
    except ValueError as e:
        raise HTTPException(400, f"invalid shop: {e}")
    db.update_shop_fields(shop_row["id"], fields)


@app.patch("/api/v2/shops/{shop_id}", responses={400: {"description": "Invalid request"}, 404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def v2_edit_shop(shop_id: str, payload: dict):
    """Inline edit from the Shops table (name, width/height + units, phone, GST, address). Refused while the shop
    is queued/converting - its output would no longer match the row."""
    row = db.get_shop(shop_id)
    if not row:
        raise HTTPException(404, _SHOP_NOT_FOUND)
    if row["status"] in ("queued", "converting"):
        raise HTTPException(409, "shop is converting")
    _apply_shop_edits(row, payload)
    return db.get_shop(shop_id)


@app.delete("/api/v2/shops/{shop_id}", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def v2_delete_shop(shop_id: str):
    row = db.get_shop(shop_id)
    if not row:
        raise HTTPException(404, _SHOP_NOT_FOUND)
    if row["status"] in ("queued", "converting"):
        raise HTTPException(409, "shop is converting")
    db.delete_shop(shop_id)
    return {"deleted": shop_id}


BATCH_MAX_SHOPS = 500


@app.post("/api/v2/jobs/{job_id}/shops/batch", responses={400: {"description": "Invalid request"}, 404: {"description": "Not found"}, 413: {"description": "Too large"}})
def v2_add_shops_batch(job_id: str, payload: dict):
    """Add many shops at once (the Excel/CSV import). Body: `{shops: [<shop body>, ...], landscape_master_id?,
    portrait_master_id?}` - each row is a normal add-shop body (row-level master ids override the batch-level ones).
    Rows are validated independently: valid rows are inserted in order, invalid ones are reported and skipped, so
    one bad row never loses the rest. Returns `{added: [shop rows], errors: [{index, reason}]}` (index is the
    0-based position in the request). A bad batch-level master id fails the whole request (4xx) - nothing is added."""
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, _JOB_NOT_FOUND)
    rows = payload.get("shops")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(400, "shops must be a non-empty array")
    if len(rows) > BATCH_MAX_SHOPS:
        raise HTTPException(413, f"at most {BATCH_MAX_SHOPS} shops per batch")
    batch_ids = {**_validated_master_ids(payload, job), "master_id": _validated_master_id(payload, job)}
    added, errors = [], []
    for i, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise ValueError("row must be an object")
            fields = _parse_shop_payload(row)
            ids = {**batch_ids, **{k: v for k, v in _validated_master_ids(row, job).items() if v}}
            if row.get("master_id"):
                ids["master_id"] = _validated_master_id(row, job)
            added.append(_insert_shop(job_id, fields, ids))
        except ValueError as e:
            errors.append({"index": i, "reason": str(e)})
        except HTTPException as e:
            errors.append({"index": i, "reason": str(e.detail)})
    return {"added": added, "errors": errors}


@app.get("/api/v2/recent")
def v2_recent():
    """Every shop across every job, newest first, for the "Recently
    generated" page. `report_json` is deliberately left out (it holds every
    object's placement - large and not needed for a list view); `files`
    maps output kind -> filename, served via
    /api/v2/shops/{shop_id}/files/{filename}.
    """
    out = []
    for r in db.list_all_shops_with_job():
        files = json.loads(r["files_json"]) if r.get("files_json") else None
        out.append({
            "shop_id": r["id"], "job_id": r["job_id"], "brand": r["brand"],
            "master_filename": r["master_filename"], "name": r["name"],
            "width": r["width"], "width_unit": r["width_unit"],
            "height": r["height"], "height_unit": r["height_unit"],
            "reference": r["reference"], "status": r["status"], "error": r["error"],
            "created_at": r["created_at"], "completed_at": r["completed_at"],
            "files": files,
            "confidence": confidence.layout_confidence(json.loads(r["layout_json"])) if r.get("layout_json") else None,
        })
    return out


@app.get("/api/v2/step-estimates")
def v2_step_estimates():
    """Average measured duration (seconds) per CorelEngine step across every
    completed conversion so far - used by the frontend to pace its smoothed
    per-row progress animation (see CLAUDE.md "New UI"). Fetched once per
    Automation page load, not on every status poll.
    """
    return db.get_step_timing_estimates()


@app.get("/api/v2/jobs/{job_id}/shops", responses={404: {"description": "Not found"}})
def v2_list_shops(job_id: str):
    if not db.get_job(job_id):
        raise HTTPException(404, _JOB_NOT_FOUND)
    return db.list_shops(job_id)


# Discrete CorelEngine steps (see app/engines.py's `step()` closure) mapped to
# a rough completion percentage - the best "real" progress signal available,
# since COM gives step transitions, not byte-level progress within a step.
_STEP_PERCENT = {"launch": 10, "open": 25, "tile_resize": 55, "bitmaps": 60, "saveas": 75, "pdf": 88, "png": 97}
_progress_peak: dict[str, int] = {}  # shop id -> highest progress_pct reported during its current conversion
_progress_lock = threading.Lock()


def _master_record(mrow: dict, orientation: str, reason: str, fallback: bool = False, selected: bool = False) -> dict:
    return {"job_id": mrow["id"], "name": _master_label(mrow) or mrow.get("master_filename"),
            "orientation": orientation, "reason": reason, "fallback": fallback, "selected": selected}


def _example_style_master(brand: str, orientation: str, target_w_mm: float, target_h_mm: float, board_type: str | None = None) -> dict | None:
    """With an example library (app/example_layout.py), the registered master whose file the nearest designer board was built from
    - only when the brand has two or more masters of this orientation to choose between (a single one is the default anyway)."""
    from . import example_layout
    masters = [m for m in db.list_masters(brand, orientation) if not m.get("deleted_at")]
    if len(masters) < 2:
        return None
    wanted = example_layout.preferred_master_file(brand, target_w_mm, target_h_mm,
                                                  available={m.get("master_filename") or "" for m in masters}, board_type=board_type)
    if not wanted:
        return None
    for m in masters:
        if (m.get("master_filename") or "").strip().lower() == wanted.strip().lower():
            return {"id": m["id"], "count": len(masters), "shape": f"the nearest designer board was made from {wanted}"}
    return None


def _nearest_shaped_master(brand: str, orientation: str, target_w_mm: float, target_h_mm: float) -> dict | None:
    """When a brand has several masters of one orientation (e.g. 10x3 ft, 6x3 ft and 4x8 ft creatives) and they carry a default
    size, the one whose width:height ratio is closest to the target is the best starting point - the layout then has the
    least to stretch. Needs at least two masters with a default size; otherwise None (the orientation's default is used)."""
    sized = []
    for m in db.list_masters(brand, orientation):
        if m.get("deleted_at") or not (m.get("default_width") and m.get("default_height")):
            continue
        unit = m.get("default_unit") or "in"
        sized.append((m, to_mm(m["default_width"], unit) / to_mm(m["default_height"], unit)))
    if len(sized) < 2:
        return None
    asp = target_w_mm / target_h_mm
    m, a = min(sized, key=lambda t: abs(math.log(t[1] / asp)))
    return {"id": m["id"], "count": len(sized), "shape": f"{a:.2f}:1 for a {asp:.2f}:1 board"}


def _select_shop_master(shop_row: dict, job_row: dict, target_w_mm: float, target_h_mm: float) -> tuple[Path, dict]:
    """The master file a shop converts from, plus a small record of the choice for the report.

    1. `master_id` (the master picked in the queue's Master column) is used when it still exists, is not deleted,
       belongs to the shop's brand and has the TARGET's orientation (`orientation_adapter.target_orientation`:
       width / height >= 1.25 is landscape; square, near-square and portrait use portrait).
    2. Otherwise - or when none was picked - `landscape_master_id` / `portrait_master_id` (each orientation's default)
       pick by the target's orientation (`orientation_adapter.select_master`), so the layout engine only ever scales
       and pads within one orientation. When a picked master was unusable and no default of the target's orientation
       is set, the brand's first registered master of that orientation is used.
    3. A shop with none of these keeps using its job's own master, as before."""
    want = orientation_adapter.target_orientation(target_w_mm, target_h_mm)
    brand = job_row["brand"]
    note = None
    chosen = shop_row.get("master_id")
    if chosen:
        m = db.get_job(chosen)
        m_orient = (m.get("orientation") or "landscape") if m else None
        if m and not m.get("deleted_at") and m["brand"] == brand and m_orient == want:
            name = _master_label(m) or m.get("master_filename")
            return Path(m["master_path"]), _master_record(m, want, f"selected master '{name}' ({want})", selected=True)
        why = ("no longer exists" if not m else "was deleted" if m.get("deleted_at")
               else f"belongs to brand {m['brand']!r}" if m["brand"] != brand
               else f"is a {m_orient} master and this board is {want}")
        note = f"selected master {chosen} {why} - used the default {want} master"
        logger.warning("Shop %s: %s", shop_row["id"], note)

    def usable(mid):  # a soft-deleted default is skipped; an id that no longer exists still fails loudly below
        m = db.get_job(mid) if mid else None
        return None if (m and m.get("deleted_at")) else mid

    ids = {"landscape": usable(shop_row.get("landscape_master_id")), "portrait": usable(shop_row.get("portrait_master_id"))}
    if note and not ids[want]:
        defaults = db.list_masters(brand, want)
        if defaults:
            ids[want] = defaults[0]["id"]
    nearest = None
    if not shop_row.get("master_id"):
        # 1. the master the nearest designer board of this brand was made from (one master per design style)
        nearest = _example_style_master(brand, want, target_w_mm, target_h_mm, shop_row.get("board_type"))
        # 2. else the master whose own shape is closest to the board
        nearest = nearest or _nearest_shaped_master(brand, want, target_w_mm, target_h_mm)
        if nearest:
            ids[want] = nearest["id"]
    if not (ids["landscape"] or ids["portrait"]):
        reason = "job master (no dual masters set)" if not note else f"{note} (none uploaded - used the job's own master)"
        return Path(job_row["master_path"]), {"job_id": job_row["id"], "orientation": job_row.get("orientation") or "landscape",
                                              "reason": reason}
    orientation, mid, fallback = orientation_adapter.select_master(target_w_mm, target_h_mm, ids["landscape"], ids["portrait"])
    mrow = db.get_job(mid)
    if not mrow:
        raise RuntimeError(f"{orientation} master {mid} no longer exists")
    reason = f"target is {orientation}" + (" (no matching master uploaded - used the other orientation)" if fallback else "")
    if nearest and mid == nearest["id"]:
        reason += f"; picked among {nearest['count']} {want} masters: {nearest['shape']}"
    if note:
        reason = f"{note}; {reason}"
    return Path(mrow["master_path"]), _master_record(mrow, orientation, reason, fallback)


# Consecutive queued shops are converted in ONE corel_worker session that reuses a single CorelDRAW instance (launched once,
# recycled every SIGNAGE_COREL_RECYCLE_N=5 jobs by the worker) instead of a fresh worker + CorelDRAW per shop: measured live,
# 10.1 s per shop on its own vs ~5 s for each further shop in a batch. Still strictly one CorelDRAW job at a time - a batch is
# one task on the same single-worker _pool. Capped so a long Convert All cannot hold the pool (and so an editor's scene build
# or an export waiting behind it) for more than a few batches' worth of time.
CONVERT_BATCH_MAX = 5
_convert_queue: list[str] = []            # shop ids in the order Convert was pressed (only shops still "queued" count)
_convert_queue_lock = threading.Lock()
_batch_slots: dict[str, tuple[Path, int]] = {}  # shop id -> (its batch's heartbeat file, its index in that batch)


def _convert_job(shop_id: str) -> tuple[dict, dict]:
    """(corel_worker job, master_used record) for one shop, from its current database row."""
    shop_row = db.get_shop(shop_id)
    job_row = db.get_job(shop_row["job_id"])
    out_dir = JOBS_V2 / shop_row["job_id"] / "out" / shop_id
    shop_dict = {
        # an imported designer file name ("73 - 60 X 75 Inch - Nonlit - SHOP.cdr") prints as just "SHOP"
        "name": clean_shop_name(shop_row["name"]),
        "width": to_mm(shop_row["width"], shop_row["width_unit"]),
        "height": to_mm(shop_row["height"], shop_row["height_unit"]),
        "unit": "mm",
        "brand": job_row["brand"],
        # every output file is named "<S.no> - <W> X <H> <Unit> - <Type> - <SHOP NAME>.<ext>" (file_naming.py)
        "file_base": file_naming.shop_basename(shop_row),
    }
    # Optional per-shop contact fields (see CLAUDE.md "Per-shop content
    # replacement") - only included when actually set, matching the old
    # /api/jobs flow's convention: compute_layout treats a missing key as
    # "don't touch this field", so a None/blank value must never be sent as
    # an empty string. `address` is one free-text field in the v2 UI (a
    # single textarea, not the old flow's address_lines list) - split into
    # lines here so `layout._contact_replacement` sees the same
    # `address_lines: list[str]` shape either UI produces it from.
    if shop_row.get("phone"):
        shop_dict["phone"] = shop_row["phone"]
    if shop_row.get("gst"):
        shop_dict["gst"] = shop_row["gst"]
    if shop_row.get("address"):
        address_lines = [line.strip() for line in shop_row["address"].splitlines() if line.strip()]
        if address_lines:
            shop_dict["address_lines"] = address_lines
    if shop_row.get("shop_name_local"):
        shop_dict["shop_name_local"] = shop_row["shop_name_local"]
    if shop_row.get("language") in ("en", "ta"):
        shop_dict["language"] = shop_row["language"]
    if shop_row.get("board_type"):
        shop_dict["board_type"] = shop_row["board_type"]       # picks among designer boards of one size (example library)
    # No font_en / font_ta from the queue: conversions keep the master's own fonts (fonts are changed in the Signage Editor).
    # Values a row may still carry from the old queue font pickers are deliberately NOT forwarded.
    master_path, master_used = _select_shop_master(shop_row, job_row, shop_dict["width"], shop_dict["height"])
    if shop_row.get("use_intelligence"):
        chosen = db.get_job(master_used["job_id"]) or job_row
        found = corrections.usable_records(db.list_corrections(), job_row["brand"], chosen.get("master_filename"),
                                           shop_dict["width"], shop_dict["height"], shop_row.get("board_type"))
        if found:
            shop_dict["intelligence"] = found
    # What the CHOSEN master currently shows as its shop name - how the engine finds the text shape to overwrite on an
    # untagged master (layout.find_shopname_ids); a `shopname`-tagged shape is used regardless.
    mrow = db.get_job(master_used["job_id"]) or job_row
    # stored before copy suffixes were stripped ("Sri Sai cafe (1)") - stripped here too
    master_name = strip_copy_suffix(mrow.get("master_shop_name")) or shop_name_from_filename(mrow.get("master_filename"))
    if master_name:
        shop_dict["master_shop_name"] = master_name
    if mrow.get("master_shop_name_local"):
        shop_dict["master_shop_name_local"] = mrow["master_shop_name_local"]
    logger.info("Shop %s (%sx%s mm, %s target): Selected master file path -> %s (%s)", shop_id,
                round(shop_dict["width"], 1), round(shop_dict["height"], 1), master_used.get("orientation"),
                master_path, master_used.get("reason"))
    return {"master_path": str(master_path), "shop": shop_dict, "out_dir": str(out_dir)}, master_used


def _attach_master_used(report: dict, master_used: dict) -> None:
    """Record which master a board came from; a board made from the OTHER orientation's master (a 16x3 ft design in a
    3x6 ft board, because no master of its own orientation was uploaded) does not fit, and the report says so."""
    report["master_used"] = master_used
    if master_used.get("fallback"):
        report.setdefault("warnings", []).append(
            f"made from the {master_used.get('orientation')} master because no master of this board's orientation was "
            "uploaded - the layout will not fit; upload a matching master and convert again")


def _store_convert_result(shop_id: str, entry: dict, master_used: dict) -> None:
    if entry.get("status") == "done":
        out = entry["result"]
        _attach_master_used(out["report"], master_used)
        db.set_shop_result(shop_id, out["files"], out["report"])
    else:
        db.set_shop_status(shop_id, "failed", error=entry.get("error", "unknown error"))


def _take_queued_batch(first: str) -> list[str]:
    """`first` plus up to CONVERT_BATCH_MAX-1 more shops still queued behind it, in Convert order; they leave the queue."""
    with _convert_queue_lock:
        batch = [first]
        for sid in list(_convert_queue):
            if len(batch) >= CONVERT_BATCH_MAX:
                break
            row = db.get_shop(sid)
            if sid != first and row and row["status"] == "queued":
                batch.append(sid)
        for sid in batch:
            while sid in _convert_queue:
                _convert_queue.remove(sid)
    return batch


def _convert_limits() -> dict:
    """Time limits for a conversion worker: SIGNAGE_SHOP_TIMEOUT_S per shop (default 45 s, from its first step after
    launching CorelDRAW), and a 2-minute no-progress limit instead of the general 10 minutes - a conversion beats
    every few seconds, so two silent minutes means a hang (e.g. inside a launch that got past the Dispatch timeout).
    A killed batch member and the shops after it are retried alone in a fresh worker + CorelDRAW (below)."""
    limit = corel_supervisor.shop_timeout_s()
    if limit <= 0:
        return {}
    return {"job_timeout_s": limit, "overall_timeout_s": max(120.0, 2 * limit)}


def _v2_convert_worker(shop_id: str, job_id: str) -> None:
    row = db.get_shop(shop_id)
    if not row or row["status"] != "queued":
        return  # already converted as part of an earlier batch (or removed)
    engine = get_engine(os.environ.get("SIGNAGE_ENGINE", "auto"))
    if engine.name != "corel":
        with _convert_queue_lock:
            while shop_id in _convert_queue:
                _convert_queue.remove(shop_id)
        db.set_shop_status(shop_id, "converting", step="starting")
        try:
            job, master_used = _convert_job(shop_id)
            out = engine.process(Path(job["master_path"]), job["shop"], Path(job["out_dir"]))
            _attach_master_used(out["report"], master_used)
            db.set_shop_result(shop_id, out["files"], out["report"])
        except Exception as e:
            db.set_shop_status(shop_id, "failed", error=str(e))
            return
        _apply_learned_nested(shop_id)
        return

    ids = _take_queued_batch(shop_id)
    prepared: list[tuple[str, dict, dict]] = []
    for sid in ids:
        try:
            job, master_used = _convert_job(sid)
            prepared.append((sid, job, master_used))
        except Exception as e:
            db.set_shop_status(sid, "failed", error=str(e))
    if not prepared:
        return
    results_path = CONVERT_RUNS / f"{prepared[0][0]}.json"
    heartbeat = results_path.with_suffix(_HEARTBEAT_SUFFIX)
    for i, (sid, _, _) in enumerate(prepared):
        _batch_slots[sid] = (heartbeat, i)
    db.set_shop_status(prepared[0][0], "converting", step="starting")

    def on_progress(i, entry):
        sid, _, master_used = prepared[i]
        if entry.get("status") == "done" or i == 0:
            _store_convert_result(sid, entry, master_used)
        # a later shop that failed is retried on its own below (it may have failed only because the reused instance did)
        if i + 1 < len(prepared):
            db.set_shop_status(prepared[i + 1][0], "converting", step="starting")

    try:
        results = corel_supervisor.run_batch([j for _, j, _ in prepared], results_path, on_progress=on_progress,
                                             **_convert_limits())
    except Exception as e:  # e.g. RefusedToStart (not enough free RAM): every shop in the batch fails with the reason
        results = [{"status": "error", "error": str(e)} for _ in prepared]
    finally:
        for sid, _, _ in prepared:
            _batch_slots.pop(sid, None)
        for p in (results_path, heartbeat, results_path.with_suffix(_DONE_SUFFIX)):
            p.unlink(missing_ok=True)

    for i, ((sid, job, master_used), entry) in enumerate(zip(prepared, results)):
        if entry.get("status") == "done":
            if db.get_shop(sid)["status"] != "done":  # normally already stored by on_progress
                _store_convert_result(sid, entry, master_used)
            continue
        if i == 0:  # the first shop ran on a fresh instance, exactly as a single conversion does: its failure is real
            _store_convert_result(sid, entry, master_used)
            continue
        # Retry once in a fresh worker + fresh CorelDRAW: exactly the path a single shop always took.
        logger.warning("shop %s failed in a batch (%s); retrying it on its own", sid, entry.get("error"))
        db.set_shop_status(sid, "converting", step="starting")
        single = CONVERT_RUNS / f"{sid}.json"
        _batch_slots[sid] = (single.with_suffix(_HEARTBEAT_SUFFIX), 0)
        try:
            retry = corel_supervisor.run_batch([job], single, **_convert_limits())[0]
        except Exception as e:
            retry = {"status": "error", "error": str(e)}
        finally:
            _batch_slots.pop(sid, None)
            for p in (single, single.with_suffix(_HEARTBEAT_SUFFIX), single.with_suffix(_DONE_SUFFIX)):
                p.unlink(missing_ok=True)
        _store_convert_result(sid, retry, master_used)
    for sid, _, _ in prepared:
        _apply_learned_nested(sid)


def _apply_learned_nested(shop_id: str) -> None:
    """After a Corel Intelligence conversion: the approved corrections that touch objects INSIDE groups (the shop-name block and its text)
    cannot be placed during the conversion, so they are replayed on the new board as ordinary editor edits - build its scene, work out the
    ops (corrections.nested_ops), save them as the board's edits (the designer sees and can undo them in the editor) and publish the files.
    Never raises: a failure only means the board keeps the top-level learning."""
    try:
        shop = db.get_shop(shop_id)
        if not shop or shop["status"] != "done" or not shop.get("use_intelligence"):
            return
        job = db.get_job(shop["job_id"])
        report = json.loads(shop["report_json"]) if shop.get("report_json") else {}
        chosen = db.get_job((report.get("master_used") or {}).get("job_id")) or job
        found = corrections.usable_records(db.list_corrections(), job["brand"], chosen.get("master_filename"),
                                           to_mm(shop["width"], shop["width_unit"]), to_mm(shop["height"], shop["height_unit"]),
                                           shop.get("board_type"))
        if not any(r["record"].get("nested") for r in found):
            return
        _scene_build_worker(shop["job_id"], shop_id)
        scene = _load_scene(shop["job_id"], shop_id)
        if scene is None:
            logging.getLogger("signage.corrections").warning("shop %s: scene could not be built for the learned nested edits", shop_id)
            return
        ops, summary = corrections.nested_ops(scene, found)
        layout = report.setdefault("layout", {})
        if isinstance(layout, dict):
            layout.setdefault("intelligence", {})["nested"] = summary
        db.set_shop_result(shop_id, json.loads(shop["files_json"]) if shop.get("files_json") else {}, report)
        if ops:
            db.set_editor_ops(shop_id, ops)
            try:
                editor_publish(shop["job_id"], shop_id)
            except HTTPException as e:
                logging.getLogger("signage.corrections").warning("shop %s: learned edits saved, files not rebuilt: %s", shop_id, e.detail)
    except Exception:
        logging.getLogger("signage.corrections").exception("could not apply the learned nested edits for shop %s", shop_id)


def _apply_sno(shop_id: str, payload: dict | None) -> None:
    """`sno` in a body = the row's S.No in the Shops table - the imported sheet's own serial when it had one ("76",
    "SL-01"), else the row's position; output files are numbered by it (file_naming). A whole number also becomes the
    shop's seq_no; any other text is kept as written (`sno_label`)."""
    raw = (payload or {}).get("sno")
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return
    if re.fullmatch(r"\d+(\.0+)?", text):
        if int(float(text)) > 0:
            db.set_shop_seq_no(shop_id, int(float(text)))
        return
    label = file_naming.sno_text(text)
    if not label or len(label) > 20:
        raise HTTPException(400, "sno must be a serial number of at most 20 characters")
    db.set_shop_sno_label(shop_id, label)


@app.post("/api/v2/shops/{shop_id}/convert", responses={400: {"description": "Invalid request"}, 404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def v2_convert_shop(shop_id: str, payload: dict | None = Body(default=None)):
    """Queue a conversion. An optional `master_id` in the body sets the master picked for this shop (see
    _select_shop_master). An optional body `{landscape_master_id, portrait_master_id}` (re)sets the shop's dual
    masters first: a shop row keeps the ids it was created with, so a portrait master uploaded AFTER the shop was
    added would otherwise never be used (the shop row would still say portrait_master_id = NULL and the landscape
    master would win by fallback). A body that names neither id leaves the stored ids alone."""
    row = db.get_shop(shop_id)
    if not row:
        raise HTTPException(404, _SHOP_NOT_FOUND)
    if row["status"] in ("queued", "converting"):
        raise HTTPException(409, "already converting")
    # The current on-screen values: any editable field in the body is saved first, so what the user sees in the
    # table (including edits made after an import) is exactly what gets converted - even if the blur-save PATCH
    # had not landed yet.
    if payload and any(k in payload for k in EDITABLE_SHOP_KEYS):
        _apply_shop_edits(row, payload)
    _apply_sno(shop_id, payload)
    if payload and (payload.get("landscape_master_id") or payload.get("portrait_master_id")):
        ids = _validated_master_ids(payload, db.get_job(row["job_id"]))
        db.set_shop_masters(shop_id, ids["landscape_master_id"], ids["portrait_master_id"])
    # the master picked in the queue's Master column; an explicit null / "" goes back to the orientation's default
    if payload and "master_id" in payload:
        db.set_shop_master_id(shop_id, _validated_master_id(payload, db.get_job(row["job_id"])))
    # Corel Intelligence: only a request that says so applies designers' corrections; a plain Convert is the engine alone
    use_intel = bool(payload and payload.get("use_intelligence"))
    if use_intel and row["status"] == "done":
        _discard_board_edits(row["job_id"], shop_id)         # the saved edits and scene belong to the board being replaced
    db.set_shop_intelligence(shop_id, use_intel)
    db.set_shop_status(shop_id, "queued")
    with _convert_queue_lock:
        _convert_queue.append(shop_id)
    _pool.submit(_v2_convert_worker, shop_id, row["job_id"])
    return {"status": "queued", "use_intelligence": use_intel}


def _discard_board_edits(job_id: str, shop_id: str) -> None:
    """A board is being regenerated: its cached scene and saved editor ops describe the old board (ids would not match)."""
    shutil.rmtree(_scene_dir(job_id, shop_id), ignore_errors=True)
    db.set_editor_ops(shop_id, [])


INTELLIGENCE_KEY = "corel_intelligence"


@app.get("/api/v2/intelligence")
def v2_intelligence_get():
    """The Corel Intelligence switch: while on, designers' editor corrections are collected. Defaults to ON."""
    return {"enabled": db.get_setting(INTELLIGENCE_KEY, "1") == "1"}


@app.put("/api/v2/intelligence", responses={422: {"description": "Validation error"}})
def v2_intelligence_set(payload: dict = Body(...)):
    if not isinstance(payload.get("enabled"), bool):
        raise HTTPException(422, "enabled must be true or false")
    db.set_setting(INTELLIGENCE_KEY, "1" if payload["enabled"] else "0")
    return {"enabled": payload["enabled"]}


@app.get("/api/v2/intelligence/available")
def v2_intelligence_available(ids: str = ""):
    """For each shop id (comma separated): how many learned designer corrections Corel Intelligence could apply to its board -
    same brand, master and page size. Only shops that have a count above zero are listed."""
    rows = db.list_corrections()
    out: dict[str, int] = {}
    for sid in [s for s in ids.split(",") if s]:
        shop = db.get_shop(sid)
        job = db.get_job(shop["job_id"]) if shop else None
        if not shop or not job or shop["status"] != "done":
            continue
        used = ((json.loads(shop["report_json"]) if shop.get("report_json") else {}).get("master_used") or {})
        chosen = db.get_job(used.get("job_id")) if used.get("job_id") else job
        found = corrections.usable_records(rows, job["brand"], (chosen or job).get("master_filename"),
                                           to_mm(shop["width"], shop["width_unit"]), to_mm(shop["height"], shop["height_unit"]),
                                           shop.get("board_type"))
        n = sum(1 for r in found for c in r["record"]["changes"]
                if c["action"] in corrections.APPLIED_ACTIONS and (c.get("signature") or {}).get("kind") != "text")
        n += sum(len(r["record"].get("nested", [])) for r in found)
        if n:
            out[sid] = n
    return {"available": out, "pending": len(db.list_corrections("pending"))}


@app.get("/api/v2/corrections")
def v2_corrections(status: str = ""):
    """The review screen's list: every stored correction (optionally one status), newest first, each with its changes spelled out."""
    if status and status not in corrections.STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(corrections.STATUSES)}")
    out = []
    for row in db.list_corrections(status or None):
        shop = db.get_shop(row["shop_id"])
        out.append(corrections.summarize(row, shop["name"] if shop else None))
    counts = {s: len(db.list_corrections(s)) for s in corrections.STATUSES}
    return {"corrections": out, "counts": counts}


class CorrectionStatusBody(BaseModel):
    status: str


@app.put("/api/v2/corrections/{shop_id}/status", responses={404: {"description": "Not found"}, 422: {"description": "Validation error"}})
def v2_set_correction_status(shop_id: str, body: CorrectionStatusBody):
    """Approve, reject or re-open one correction. Only approved ones are applied to later boards."""
    if body.status not in corrections.STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(corrections.STATUSES)}")
    if not db.set_correction_status(shop_id, body.status):
        raise HTTPException(404, "correction not found")
    return {"id": shop_id, "status": body.status}


@app.get("/api/v2/shops/{shop_id}/status", responses={404: {"description": "Not found"}})
def v2_shop_status(shop_id: str):
    row = db.get_shop(shop_id)
    if not row:
        raise HTTPException(404, _SHOP_NOT_FOUND)

    if row["status"] == "done":
        row["progress_pct"] = 100
        row["files"] = json.loads(row["files_json"]) if row["files_json"] else None
        row["report"] = json.loads(row["report_json"]) if row["report_json"] else None
        row["confidence"] = confidence.layout_confidence((row["report"] or {}).get("layout"))
    elif row["status"] == "failed":
        row["progress_pct"] = 0
    elif row["status"] == "converting":
        step = row["step"]
        pct = _STEP_PERCENT.get(step, 5)
        # In a batch the heartbeat file is the batch's, shared by its shops; only a beat for THIS shop's index is its progress.
        heartbeat_path, index = _batch_slots.get(shop_id, (CONVERT_RUNS / f"{shop_id}.heartbeat", 0))
        if heartbeat_path.exists():
            try:
                hb = json.loads(heartbeat_path.read_text(encoding="utf-8"))
                if hb.get("job_index", 0) == index:
                    step = hb.get("step", step)
                    pct = _STEP_PERCENT.get(step, pct)
                elif hb.get("job_index", 0) > index:
                    # the worker has moved on to a later shop: this one's CorelDRAW work is finished and its result is
                    # being stored - it used to fall back to "starting" (5 %) here for a moment, after showing 97 %
                    step, pct = "saving", 99
            except Exception:
                pass
        # never report less than already reported for this conversion (a failed batch member retried on its own starts
        # its steps again); _progress_peak is cleared when the shop is queued again or leaves "converting"
        with _progress_lock:
            pct = max(pct, _progress_peak.get(shop_id, 0))
            _progress_peak[shop_id] = pct
        row["step"], row["progress_pct"] = step, pct
    else:  # "new" or "queued"
        row["progress_pct"] = 0
    if row["status"] != "converting":
        with _progress_lock:
            _progress_peak.pop(shop_id, None)
    row.pop("report_json", None)  # the parsed `report` above carries the same data; sending both doubled the payload
    return row


@app.get("/api/v2/shop-statuses", responses={404: {"description": "Not found"}})
def v2_shop_statuses(ids: str = ""):
    """Status of several shops in one request ({id: status row}; unknown ids are left out). The Automation page polls every
    converting/queued shop with this one call instead of one request per shop every 800 ms - a 50-shop Convert All used to
    make ~60 requests a second, mostly for shops that were only waiting."""
    out = {}
    for sid in [s for s in ids.split(",") if s][:500]:
        try:
            row = v2_shop_status(sid)
        except HTTPException:
            continue
        row.pop("report", None)   # the page never reads it; with it, 50 done shops made one poll ~750 KB
        out[sid] = row
    return out


@app.get("/api/v2/shops/{shop_id}/files/{filename}", responses={404: {"description": "Not found"}})
def v2_shop_file(shop_id: str, filename: str):
    row = db.get_shop(shop_id)
    if not row:
        raise HTTPException(404, _SHOP_NOT_FOUND)
    out_dir = (JOBS_V2 / row["job_id"] / "out" / shop_id).resolve()
    p = (out_dir / filename).resolve()
    if out_dir not in p.parents or not p.is_file():
        raise HTTPException(404)
    # downloaded under the standard name ("76 - 125 X 48 Inch - Nonlit - SHOP.cdr"), also for boards converted before
    # the on-disk files were named that way; the report JSON keeps its own name
    name = p.name if p.name.endswith("_report.json") else f"{file_naming.shop_basename(row)}{p.suffix}"
    return FileResponse(p, filename=name)


def _single_sources(row: dict, fmt: str) -> dict:
    from . import asset_zip

    out_dir = JOBS_V2 / row["job_id"] / "out" / row["id"]
    exports = [asset_zip.export_record(db.get_export(e["id"])) for e in db.list_exports(row["id"], limit=20)]
    return asset_zip.pick_single(out_dir, json.loads(row["files_json"]) if row.get("files_json") else {}, exports,
                                 db.get_editor_ops(row["id"]), fmt)


@app.get("/api/v2/shops/{shop_id}/downloads", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def v2_shop_downloads(shop_id: str):
    """What the row's quick Download popup can offer: {cdr|jpg|png|pdf: {available, source, note, reason}}."""
    from . import asset_zip

    row = db.get_shop(shop_id)
    if not row:
        raise HTTPException(404, _SHOP_NOT_FOUND)
    if row["status"] != "done":
        raise HTTPException(409, "this shop has not been converted yet")
    out = {}
    for fmt in asset_zip.SINGLE_FORMATS:
        src = _single_sources(row, fmt)
        out[fmt] = {"available": src["path"] is not None, "source": src["source"], "note": src["note"], "reason": src["reason"]}
    return out


@app.get("/api/v2/shops/{shop_id}/download/{fmt}", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 422: {"description": "Validation error"}})
def v2_shop_download(shop_id: str, fmt: str, no: str | None = None):
    """One file of one converted shop, named "<S.no> - <W> X <H> <Unit> - <Type> - <SHOP>.<ext>" (`no` = the row's S.no in
    the queue, default its seq_no). CDR/PDF/PNG are served as they are; a JPG is an editor export's JPEG, else the best PNG
    re-encoded as JPEG quality 95 on white (CorelDRAW's pixels, like the ZIP's JPG). 404 when that format does not exist."""
    from . import asset_zip

    fmt = fmt.lower()
    if fmt not in asset_zip.SINGLE_FORMATS:
        raise HTTPException(422, "format must be one of cdr, jpg, png, pdf")
    row = db.get_shop(shop_id)
    if not row:
        raise HTTPException(404, _SHOP_NOT_FOUND)
    if row["status"] != "done":
        raise HTTPException(409, "this shop has not been converted yet")
    src = _single_sources(row, fmt)
    if src["path"] is None:
        raise HTTPException(404, src["reason"])
    name = f"{file_naming.shop_basename(row, no if no and no.strip() not in ('', '0') else None)}.{fmt}"
    if not src["to_jpeg"]:
        return FileResponse(src["path"], filename=name)
    from PIL import Image                         # lazily, like every other Pillow use in the app

    Image.MAX_IMAGE_PIXELS = None
    tmp = tempfile.NamedTemporaryFile(prefix="shop_jpg_", suffix=".jpg", delete=False)
    tmp.close()
    with Image.open(src["path"]) as im:
        rgba = im.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.split()[-1])
        flat.save(tmp.name, "JPEG", quality=95, subsampling=0, dpi=im.info.get("dpi", (72, 72)))
    return FileResponse(tmp.name, media_type=_MEDIA_JPEG, filename=name,
                        background=BackgroundTask(lambda: Path(tmp.name).unlink(missing_ok=True)))


THUMB_MAX_PX = 240  # long side; the Recently generated thumbnail is 56x36 CSS px (x2 for high-DPI, x1.1 on hover)


def _thumb_format() -> tuple[str, str]:
    """WebP when this Pillow build has it (a 240 px board is ~5-15 KB), else PNG."""
    from PIL import features
    return ("WEBP", _WEBP_SUFFIX) if features.check("webp") else ("PNG", ".png")


@app.get("/api/v2/shops/{shop_id}/thumb", responses={404: {"description": "Not found"}})
def v2_shop_thumb(shop_id: str, size: int | None = None):
    """A small copy of a converted shop's preview for list thumbnails. The full preview is CorelDRAW's 1600 px PNG
    (~1-1.3 MB for a real board), which the Recently generated table used to download and decode for every row just to
    draw it 56 px wide - 17.8 MB for 17 rows, measured. Built once with Pillow (bilinear with a reducing gap: fast, and
    clean at this scale; export-quality rasters stay CorelDRAW's), cached under the job's thumbs/ folder - never in the
    shop's out/ folder, which the ZIP export reads - and rebuilt only when the preview is newer than the thumbnail.
    `size` (long side in px, 120-1400) asks for a bigger copy - the queue gallery uses 720 so a tall portrait board is not a blurry
    sliver; each size is cached separately and the default stays the small list thumbnail."""
    row = db.get_shop(shop_id)
    if not row:
        raise HTTPException(404, _SHOP_NOT_FOUND)
    files = json.loads(row["files_json"]) if row.get("files_json") else {}
    name = (files or {}).get("preview")
    out_dir = (JOBS_V2 / row["job_id"] / "out" / shop_id).resolve()
    src = (out_dir / name).resolve() if name else None
    if not src or out_dir not in src.parents or not src.is_file():
        raise HTTPException(404, "no preview for this shop")
    fmt, ext = _thumb_format()
    long_side = THUMB_MAX_PX if size is None else max(120, min(int(size), 1400))
    thumb = JOBS_V2 / row["job_id"] / "thumbs" / (f"{shop_id}{ext}" if long_side == THUMB_MAX_PX else f"{shop_id}-{long_side}{ext}")
    if not thumb.is_file() or thumb.stat().st_mtime < src.stat().st_mtime:
        if src.suffix.lower() not in (".png", ".jpg", _JPEG_SUFFIX, _WEBP_SUFFIX):
            return FileResponse(src)  # e.g. MockEngine's SVG preview: already tiny, and vector
        from PIL import Image
        thumb.parent.mkdir(parents=True, exist_ok=True)
        tmp = thumb.with_name(f"{thumb.stem}.{os.getpid()}.tmp{ext}")
        try:
            with Image.open(src) as im:
                im.thumbnail((long_side, long_side), Image.BILINEAR, reducing_gap=2.0)
                if im.mode in ("LA", "P"):  # palette / grey+alpha: RGBA so WebP and PNG both keep any transparency
                    im = im.convert("RGBA")
                im.save(tmp, fmt, **({"quality": 82, "method": 4} if fmt == "WEBP" else {"optimize": True}))
        except OSError:  # an empty/corrupt preview (seen live: a 0-byte PNG; PIL's UnidentifiedImageError is an OSError) - no thumbnail
            tmp.unlink(missing_ok=True)
            raise HTTPException(404, "preview is not a readable image")
        try:
            tmp.replace(thumb)
        except OSError:  # another request replaced it at the same moment (Windows): serve that one
            tmp.unlink(missing_ok=True)
    return FileResponse(thumb, headers={"Cache-Control": "no-cache"})  # revalidated by ETag, rebuilt when the board is


# ------------------------------------------------------ print details sheet
#
# "Create Print File" on the Automation page: a one-page (or, for many shops, multi-page) "Print Details" summary of
# the selected converted shops - title, date, project no, location, board type, QTY / Sq.feet and a thumbnail + caption
# per shop - rendered by print_sheet.py with Pillow (no CorelDRAW, so it does not wait behind conversions on _pool).

class PrintSheetRequest(BaseModel):
    title: str = ""
    project_no: str = ""
    date: str = ""                         # YYYY-MM-DD from the date picker (shown DD.MM.YYYY), or any typed text
    location: str = ""
    board_type: str = ""
    shop_ids: list[str]
    numbers: dict[str, int | str] | None = None  # the S.no each shop has in the queue, for the captions (default: its own)
    format: str = "pdf"                    # pdf | jpeg


def _shop_thumbnail(row: dict) -> Path | None:
    """The newest look of a board: its latest finished editor export's PNG/JPEG (it includes the designer's edits),
    else the conversion's preview. SVG previews (MockEngine) are not raster images, so no thumbnail."""
    out_dir = JOBS_V2 / row["job_id"] / "out" / row["id"]
    for ex in db.list_exports(row["id"]):
        if ex["status"] != "done" or not ex["files_json"]:
            continue
        files = json.loads(ex["files_json"])
        for kind in ("png", "jpeg"):
            if files.get(kind):
                p = out_dir / "exports" / ex["id"] / files[kind]
                if p.is_file():
                    return p
    files = json.loads(row["files_json"]) if row.get("files_json") else {}
    name = files.get("preview") or ""
    p = out_dir / name
    if name and p.suffix.lower() in (".png", ".jpg", _JPEG_SUFFIX) and p.is_file():
        return p
    return None


def _safe_file_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_.")


@app.post("/api/print-sheet/generate", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 422: {"description": "Validation error"}, 503: {"description": "Service unavailable"}})
def print_sheet_generate(body: PrintSheetRequest):
    # Imported here, not at module level: print_sheet needs Pillow, and a server started from a Python without it must
    # still start (every other Pillow use in the app is imported lazily too) - this route then says what is missing.
    try:
        from . import print_sheet
    except ImportError as e:
        raise HTTPException(503, f"The print sheet needs Pillow, which is not installed for the Python running this server "
                                 f"({sys.executable}): {e}. Install it with `\"{sys.executable}\" -m pip install Pillow` "
                                 "and restart the server.")
    if body.format not in ("pdf", "jpeg"):
        raise HTTPException(422, "format must be 'pdf' or 'jpeg'")
    ids = list(dict.fromkeys(body.shop_ids))             # de-duplicated, order kept
    if not ids:
        raise HTTPException(422, "select at least one shop")
    if len(ids) > 200:
        raise HTTPException(422, "at most 200 shops per print file")
    numbers = body.numbers or {}
    shops = []
    for sid in ids:
        row = db.get_shop(sid)
        if not row:
            raise HTTPException(404, f"shop {sid} not found")
        if row["status"] != "done":
            raise HTTPException(409, f"shop \"{row['name']}\" has not been converted yet")
        # which source files exist - the same choice as the ZIP (an export of the current edits, else the conversion's)
        from . import asset_zip
        exports = [asset_zip.export_record(db.get_export(e["id"])) for e in db.list_exports(sid, limit=20)]
        src = asset_zip.pick_sources(JOBS_V2 / row["job_id"] / "out" / sid,
                                     json.loads(row["files_json"]) if row.get("files_json") else {}, exports, db.get_editor_ops(sid))
        shops.append(print_sheet.SheetShop(
            no=numbers.get(sid) or file_naming.row_sno(row), name=row["name"],
            width=row["width"], width_unit=row["width_unit"], height=row["height"], height_unit=row["height_unit"],
            image=_shop_thumbnail(row), has_cdr=src["cdr"] is not None, has_pdf=src["pdf"] is not None))
    meta = print_sheet.SheetMeta(title=body.title.strip(), project_no=body.project_no.strip(),
                                 date=print_sheet.format_date(body.date), location=body.location.strip(),
                                 board_type=body.board_type.strip())
    tmp = Path(tempfile.mkdtemp(prefix="print_sheet_"))
    path, media = print_sheet.write_sheet(meta, shops, body.format, tmp / "sheet")
    name = "_".join(p for p in ("Print_Details", _safe_file_part(meta.project_no), _safe_file_part(meta.date)) if p)
    return FileResponse(path, media_type=media, filename=f"{name}{path.suffix}",
                        background=BackgroundTask(lambda: shutil.rmtree(tmp, ignore_errors=True)))


_PRINT_FILE_IMAGES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")
_PRINT_FILE_MAX_BYTES = 400 * 1024 * 1024


def _print_file_preview(upload: UploadFile, folder: Path, n: int) -> Path | None:
    """Save one uploaded CDR / image and return an image path for its card: the image itself, or a .cdr's embedded preview
    (no CorelDRAW; a pre-X4 .cdr has none -> None and the card says "No preview available")."""
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in (".cdr", *_PRINT_FILE_IMAGES):
        raise HTTPException(422, f"\"{upload.filename}\": only .cdr and image files (jpg, png, ...) can be added")
    sub = folder / f"f{n}"
    sub.mkdir()
    dest = sub / f"upload{suffix}"
    size = 0
    with dest.open("wb") as f:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > _PRINT_FILE_MAX_BYTES:
                raise HTTPException(413, f"\"{upload.filename}\" is larger than 400 MB")
            f.write(chunk)
    if suffix == ".cdr":
        return _extract_cdr_preview(dest)[0]
    return dest


def _print_file_spec(spec: str, max_items: int) -> dict:
    """Parse and sanity-check the `spec` form field; anything wrong is a 422."""
    try:
        data = json.loads(spec)
        if data.get("format", "pdf") not in ("pdf", "jpeg"):
            raise ValueError("format must be 'pdf' or 'jpeg'")
        sections = data["sections"]
        if not isinstance(sections, list) or not sections:
            raise ValueError("add at least one section")
        if sum(len(s.get("items", [])) for s in sections) > max_items:
            raise ValueError(f"at most {max_items} items per print file")
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise HTTPException(422, f"invalid spec: {e}")
    return data


def _print_file_item(it: dict, image, print_file):
    """One spec item -> print_file.Item (a bad number, unit or qty is a 422 that names the item)."""
    name = str(it.get("name", "")).strip()
    try:
        w, h, qty = float(it["width"]), float(it["height"]), int(it.get("qty") or 1)
    except (KeyError, ValueError, TypeError):
        raise HTTPException(422, f"item \"{name}\" needs a numeric width and height")
    unit = it.get("unit", "in")
    if not (w > 0 and h > 0) or unit not in ("in", "ft", "cm", "mm") or qty < 1:
        raise HTTPException(422, f"item \"{name}\" needs width/height > 0, a unit in/ft/cm/mm and qty >= 1")
    return print_file.Item(name=name, width=w, height=h, unit=unit, board_type=str(it.get("type", "")).strip(),
                           no=it.get("no") or "", qty=qty, image=image)


def _print_file_section(s: dict, preview, print_file):
    """One spec section -> print_file.Section; `preview(file_index)` resolves an item's upload to an image path."""
    items = [_print_file_item(it, preview(it.get("file")), print_file) for it in s.get("items", [])]
    try:
        qo = int(s["qty"]) if s.get("qty") not in (None, "") else None
        so = float(s["sqft"]) if s.get("sqft") not in (None, "") else None
    except (ValueError, TypeError):
        raise HTTPException(422, f"section \"{s.get('name', '')}\": QTY and Sq.feet must be numbers")
    return print_file.Section(name=str(s.get("name", "")).strip(), items=items, qty_override=qo, sqft_override=so)


@app.post("/api/print-file/generate", responses={413: {"description": "Too large"}, 422: {"description": "Validation error"}, 503: {"description": "Service unavailable"}})
def print_file_generate(spec: str = Form(...), files: list[UploadFile] = File(default=[])):
    """"Create Print File" page: `spec` is JSON {title, project_no, date, lines[], format, sections[{name, qty, sqft,
    items[{file, name, width, height, unit, type, no, qty}]}]} and `files` the uploads (an item's `file` is the index into
    `files`, or null for a card without a picture). Rendered by print_file.py; nothing is stored."""
    try:
        from . import print_file, print_sheet
    except ImportError as e:
        raise HTTPException(503, f"The print file needs Pillow, which is not installed for the Python running this server: {e}")
    data = _print_file_spec(spec, print_file.MAX_ITEMS)
    tmp = Path(tempfile.mkdtemp(prefix="print_file_"))
    previews: dict[int, Path | None] = {}

    def preview(idx):
        if idx is None:
            return None
        if not isinstance(idx, int) or not 0 <= idx < len(files):
            raise HTTPException(422, f"item refers to file #{idx}, which was not uploaded")
        if idx not in previews:
            previews[idx] = _print_file_preview(files[idx], tmp, idx)
        return previews[idx]

    try:
        sections = [_print_file_section(s, preview, print_file) for s in data["sections"]]
        meta = print_file.FileMeta(title=str(data.get("title", "")).strip(), project_no=str(data.get("project_no", "")).strip(),
                                   date=print_sheet.format_date(str(data.get("date", ""))),
                                   lines=[str(x) for x in data.get("lines", [])])
        try:
            path, media = print_file.write(meta, sections, data.get("format", "pdf"), tmp / "sheet")
        except ValueError as e:
            raise HTTPException(422, str(e))
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    name = "_".join(p for p in ("Print_File", _safe_file_part(meta.project_no), _safe_file_part(meta.date)) if p)
    return FileResponse(path, media_type=media, filename=f"{name}{path.suffix}",
                        background=BackgroundTask(lambda: shutil.rmtree(tmp, ignore_errors=True)))


class AssetZipRequest(BaseModel):
    shop_ids: list[str]
    numbers: dict[str, int | str] | None = None  # the S.no each shop has in the queue, for the file names (default: its own)


@dataclass
class _BuiltZip:
    dir: Path              # temp dir holding the archive
    at: float              # built at (for the TTL sweep)
    busy: int = 0          # WeTransfer uploads reading it right now
    discard: bool = False  # DELETE arrived while busy: remove once the upload ends


_asset_zips: dict[str, _BuiltZip] = {}  # token -> a built Signage_Assets_Export.zip
_asset_zips_lock = threading.Lock()
ASSET_ZIP_TTL_S = 3600


def _drop_zip_dir(d: Path) -> None:
    shutil.rmtree(d, ignore_errors=True)


def _sweep_asset_zips() -> None:
    """Remove archives older than the TTL that no upload is using (a closed tab, a modal left open)."""
    now = time.time()
    with _asset_zips_lock:
        old = [t for t, z in _asset_zips.items() if now - z.at > ASSET_ZIP_TTL_S and not z.busy]
        dirs = [_asset_zips.pop(t).dir for t in old]
    for d in dirs:
        _drop_zip_dir(d)


def _zip_path(token: str) -> Path:
    from . import asset_zip

    with _asset_zips_lock:
        z = _asset_zips.get(token)
    if z is None or z.discard or not (z.dir / asset_zip.ZIP_NAME).is_file():
        raise HTTPException(404, "this ZIP has expired - press Generate ZIP again")
    return z.dir / asset_zip.ZIP_NAME


@app.post("/api/export-zip", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 422: {"description": "Validation error"}})
def export_zip(body: AssetZipRequest):
    """"Generate ZIP": builds Signage_Assets_Export.zip - `<NN>_<SHOP>.jpg` at the root, `CDR&PDF/cdr/` and `CDR&PDF/pdf/`
    (see asset_zip.py for which file stands for each shop) - and answers {token, download, summary}. The archive stays on
    the server so the modal can both download it (`download`, a GET the browser streams to disk - CDRs are 9-300 MB, too
    big to hold in page memory) and upload it to WeTransfer (POST /api/export-wetransfer with the token). It is removed
    by DELETE /api/export-zip/{token} (the modal closing) or after an hour. `summary` lists what could not be included
    and shops whose editor edits were never exported."""
    from . import asset_zip

    _sweep_asset_zips()
    ids = list(dict.fromkeys(body.shop_ids))
    if not ids:
        raise HTTPException(422, "select at least one shop")
    if len(ids) > 500:
        raise HTTPException(422, "at most 500 shops per ZIP")
    numbers = body.numbers or {}
    shops = []
    for sid in ids:
        row = db.get_shop(sid)
        if not row:
            raise HTTPException(404, f"shop {sid} not found")
        if row["status"] != "done":
            raise HTTPException(409, f"shop \"{row['name']}\" has not been converted yet")
        out_dir = JOBS_V2 / row["job_id"] / "out" / sid
        exports = [asset_zip.export_record(db.get_export(e["id"])) for e in db.list_exports(sid, limit=20)]
        src = asset_zip.pick_sources(out_dir, json.loads(row["files_json"]) if row.get("files_json") else {},
                                     exports, db.get_editor_ops(sid))
        no = numbers.get(sid) or file_naming.row_sno(row)
        shops.append(asset_zip.ShopAssets(no=no, name=row["name"], stem=file_naming.shop_basename(row, no),
                                           cdr=src["cdr"], pdf=src["pdf"], image=src["image"], notes=src["notes"]))
    tmp = Path(tempfile.mkdtemp(prefix="asset_zip_"))
    try:
        summary = asset_zip.write_zip(shops, tmp / asset_zip.ZIP_NAME)
    except Exception:
        _drop_zip_dir(tmp)
        raise
    token = uuid.uuid4().hex
    with _asset_zips_lock:
        _asset_zips[token] = _BuiltZip(tmp, time.time())
    summary["bytes"] = (tmp / asset_zip.ZIP_NAME).stat().st_size
    return {"token": token, "download": f"/api/export-zip/{token}", "summary": summary}


CDR_ZIP_NAME = "All_CDR_Files.zip"


ALL_ZIP_NAMES = {"cdr": CDR_ZIP_NAME, "pdf": "All_PDF_Files.zip", "jpg": "All_JPG_Files.zip", "png": "All_PNG_Files.zip"}


@app.get("/api/v2/download-all", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 422: {"description": "Validation error"}})
def v2_download_all(ids: str, format: str = "cdr", nos: str | None = None):
    """"Download All": one ZIP of every listed converted shop's file in ONE format (`format` = cdr | pdf | jpg | png), each at
    the archive root under its standard name ("01 - 125 X 48 Inch - Nonlit - SHOP.pdf"). `ids` = comma-separated shop ids;
    `nos` = their S.no in the queue, same order (default: each shop's own). Each file is chosen exactly like the row's
    Download popup (asset_zip.pick_single: the newest editor export of the shop's current edits, else the conversion's
    file; a JPG may be the PNG re-encoded). Files that do not exist are listed in MISSING_FILES.txt inside the ZIP. A plain
    GET so the browser streams it to disk; the temp archive is deleted once sent. 409 when no shop has that format."""
    from . import asset_zip

    fmt = (format or "").strip().lower()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in asset_zip.SINGLE_FORMATS:
        raise HTTPException(422, "format must be one of cdr, pdf, jpg, png")
    shop_ids = [s for s in dict.fromkeys(x.strip() for x in ids.split(",")) if s]
    if not shop_ids:
        raise HTTPException(422, "no shops given")
    if len(shop_ids) > 500:
        raise HTTPException(422, "at most 500 shops per download")
    numbers = [x.strip() for x in nos.split(",")] if nos else []   # S.Nos, numeric or not ("76", "SL-01")
    items = []
    for k, sid in enumerate(shop_ids):
        row = db.get_shop(sid)
        if not row:
            raise HTTPException(404, f"shop {sid} not found")
        if row["status"] != "done":
            continue
        no = numbers[k] if k < len(numbers) and numbers[k] not in ("", "0") else file_naming.row_sno(row)
        items.append((file_naming.shop_basename(row, no), _single_sources(row, fmt)))
    if not any(src["path"] is not None for _, src in items):
        raise HTTPException(409, f"none of these shops has a {fmt.upper()} file yet")
    tmp = tempfile.NamedTemporaryFile(prefix=f"all_{fmt}_", suffix=".zip", delete=False)
    tmp.close()
    try:
        asset_zip.write_format_zip(items, fmt, Path(tmp.name))
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise
    return FileResponse(tmp.name, media_type=_MEDIA_ZIP, filename=ALL_ZIP_NAMES[fmt],
                        background=BackgroundTask(lambda: Path(tmp.name).unlink(missing_ok=True)))


@app.get("/api/v2/download-cdrs", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 422: {"description": "Validation error"}})
def v2_download_cdrs(ids: str, nos: str | None = None):
    """"Download All CDRs": one ZIP of the CDR (vector source) of every listed converted shop, each under its standard
    name ("01 - 125 X 48 Inch - Nonlit - SHOP.cdr"). `ids` = comma-separated shop ids; `nos` = their S.no in the queue,
    same order (default: each shop's seq_no). The CDR is the newest editor export made from the shop's current edits,
    else the conversion's own file - the same rule as Generate ZIP. A plain GET so the browser streams it to disk (CDRs
    are 9-300 MB); the temp archive is deleted once sent. 409 when none of the shops has a CDR."""
    from . import asset_zip

    shop_ids = [s for s in dict.fromkeys(x.strip() for x in ids.split(",")) if s]
    if not shop_ids:
        raise HTTPException(422, "no shops given")
    if len(shop_ids) > 500:
        raise HTTPException(422, "at most 500 shops per download")
    numbers = [x.strip() for x in nos.split(",")] if nos else []   # S.Nos, numeric or not ("76", "SL-01")
    shops = []
    for k, sid in enumerate(shop_ids):
        row = db.get_shop(sid)
        if not row:
            raise HTTPException(404, f"shop {sid} not found")
        if row["status"] != "done":
            continue
        out_dir = JOBS_V2 / row["job_id"] / "out" / sid
        exports = [asset_zip.export_record(db.get_export(e["id"])) for e in db.list_exports(sid, limit=20)]
        src = asset_zip.pick_sources(out_dir, json.loads(row["files_json"]) if row.get("files_json") else {},
                                     exports, db.get_editor_ops(sid))
        no = numbers[k] if k < len(numbers) and numbers[k] not in ("", "0") else file_naming.row_sno(row)
        shops.append(asset_zip.ShopAssets(no=no, name=row["name"], stem=file_naming.shop_basename(row, no), cdr=src["cdr"]))
    if not any(s.cdr for s in shops):
        raise HTTPException(409, "none of these shops has a converted CDR yet")
    tmp = tempfile.NamedTemporaryFile(prefix="cdrs_", suffix=".zip", delete=False)
    tmp.close()
    try:
        asset_zip.write_cdr_zip(shops, Path(tmp.name))
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise
    return FileResponse(tmp.name, media_type=_MEDIA_ZIP, filename=CDR_ZIP_NAME,
                        background=BackgroundTask(lambda: Path(tmp.name).unlink(missing_ok=True)))


@app.get("/api/export-zip/{token}", responses={404: {"description": "Not found"}})
def export_zip_download(token: str):
    """The archive built by POST /api/export-zip (any number of downloads until it is deleted or expires)."""
    from . import asset_zip

    return FileResponse(_zip_path(token), media_type=_MEDIA_ZIP, filename=asset_zip.ZIP_NAME)


@app.delete("/api/export-zip/{token}")
def export_zip_delete(token: str):
    """The Generate ZIP modal closed: remove the archive now - or, if a WeTransfer upload is still reading it, as soon as
    that upload ends."""
    with _asset_zips_lock:
        z = _asset_zips.get(token)
        if z is None:
            return {"deleted": False}
        if z.busy:
            z.discard = True
            return {"deleted": False, "pending_upload": True}
        _asset_zips.pop(token)
    _drop_zip_dir(z.dir)
    return {"deleted": True}


# --------------------------------------------------------------- WeTransfer link
# "Generate WeTransfer Link" in the Generate ZIP modal: the built archive is uploaded through wetransfer.com's own web page,
# driven by a headless browser (wetransfer_uploader.py - WeTransfer's Public API was retired in 2020 and issues no keys).
# Uploads run one at a time on their own thread, never on _pool, so they do not hold up CorelDRAW conversions; the page
# polls GET /api/export-wetransfer/{job_id}.
# When WeTransfer cannot be used, the job still succeeds with a link served by this server (`fallback_used`).

class WeTransferRequest(BaseModel):
    token: str | None = None               # a ZIP already built by POST /api/export-zip (the modal's)
    shop_ids: list[str] | None = None      # or build one now from these shops
    numbers: dict[str, int | str] | None = None
    sender_email: str | None = None        # WeTransfer requires the sender's address for a link transfer
    project_id: str | None = None          # accepted for the caller's convenience; not sent to WeTransfer


class VerifyOtpRequest(BaseModel):
    session_id: str                        # the upload's job_id
    otp_code: str


_wt_codes: dict[str, "queue.Queue[str]"] = {}  # job_id -> the code the person types, handed to the waiting upload thread


def _otp_wait_s() -> float:
    """How long an upload waits, browser open, for the person to type WeTransfer's e-mailed code."""
    try:
        return float(os.environ.get("SIGNAGE_WETRANSFER_OTP_WAIT_S", 300))
    except ValueError:
        return 300.0


_wt_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wetransfer")
_wt_jobs: dict[str, dict] = {}
_wt_lock = threading.Lock()

# The fallback when WeTransfer cannot be used: a copy of the archive kept for SHARE_DAYS (WeTransfer's own default is 3
# days) under an unguessable id and served by this server. The modal's archive cannot be used for this - it is deleted
# when the modal closes.
SHARED_ZIPS = DATA / "shared_zips"
SHARE_ID_RE = re.compile(r"[A-Za-z0-9_-]{20,64}")


def _share_days() -> float:
    try:
        return float(os.environ.get("SIGNAGE_SHARE_DAYS", 3))
    except ValueError:
        return 3.0


def _sweep_shared_zips() -> None:
    now = time.time()
    if not SHARED_ZIPS.is_dir():
        return
    for d in SHARED_ZIPS.iterdir():
        try:
            meta = json.loads((d / _META_JSON).read_text(encoding="utf-8"))
            if now > meta["expires_at"]:
                shutil.rmtree(d, ignore_errors=True)
        except (OSError, ValueError, KeyError):
            if now - d.stat().st_mtime > 86400:              # half-written or unreadable: gone after a day
                shutil.rmtree(d, ignore_errors=True)


def _share_zip(path: Path) -> dict:
    """Copy `path` into the share store; returns {id, expires_at}."""
    import secrets

    from . import asset_zip

    _sweep_shared_zips()
    share_id = secrets.token_urlsafe(18)
    d = SHARED_ZIPS / share_id
    d.mkdir(parents=True)
    shutil.copyfile(path, d / asset_zip.ZIP_NAME)
    expires_at = time.time() + _share_days() * 86400
    (d / _META_JSON).write_text(json.dumps({"expires_at": expires_at, "created_at": time.time()}), encoding="utf-8")
    return {"id": share_id, "expires_at": expires_at}


def _lan_ip() -> str | None:
    """This machine's address on its local network: the source address the OS would use for an outside route. A UDP
    `connect` only picks a route - no packet is sent."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))                       # TEST-NET-1: never contacted
            return s.getsockname()[0]
    except OSError:
        return None


def _share_base(server: tuple | None) -> dict:
    """Where a shared link must point, and whether anyone but this computer can open it. `server` is the (host, port)
    this server listens on (request.scope["server"]). SIGNAGE_PUBLIC_BASE_URL overrides everything (e.g. a DNS name)."""
    env = os.environ.get("SIGNAGE_PUBLIC_BASE_URL", "").strip()
    if env:
        return {"base": env.rstrip("/"), "local_only": False}
    host, port = (server or ("127.0.0.1", 8000))[:2]
    if host in ("127.0.0.1", "localhost", "::1"):
        # started without --host: the server cannot be reached from another computer at all
        return {"base": f"http://127.0.0.1:{port}", "local_only": True}  # NOSONAR - loopback share link on a local server
    if host in ("0.0.0.0", "::", ""):
        ip = _lan_ip()
        return {"base": f"http://{ip or '127.0.0.1'}:{port}", "local_only": ip is None}  # NOSONAR - LAN share link on a local server
    return {"base": f"http://{host}:{port}", "local_only": False}  # NOSONAR - LAN share link on a local server


def _wt_worker(job_id: str, token: str, share: dict, skip_reason: str | None = None,
               sender_email: str | None = None) -> None:
    """Upload to WeTransfer; on ANY failure (or `skip_reason` - no Playwright, too big) make a server link instead, so
    the page always gets a link. The WeTransfer error is kept in `wetransfer_error` and logged."""
    from . import asset_zip

    def update(**kw) -> None:
        with _wt_lock:
            _wt_jobs[job_id].update(kw)

    def step(name: str, progress: float | None = None) -> None:
        update(step=name, **({"progress": progress} if progress is not None else {}))

    codes: queue.Queue[str] = queue.Queue()
    with _wt_lock:
        _wt_codes[job_id] = codes

    def ask_code(error: str | None) -> str | None:
        """WeTransfer asks for the code it e-mailed: tell the page (status requires_otp) and wait - browser open - for
        the person to POST it to /api/export-wetransfer/verify-otp. None when nobody answers in time."""
        while not codes.empty():                       # a stale code from an earlier prompt is not this one's answer
            codes.get_nowait()
        update(status="requires_otp", step="requires_otp", session_id=job_id, otp_error=error,
               otp_deadline=time.time() + _otp_wait_s())
        try:
            code = codes.get(timeout=_otp_wait_s())
        except queue.Empty:
            update(status="running", otp_error=None)
            return None
        update(status="running", step="verifying", otp_error=None)
        return code

    reason = skip_reason
    try:
        update(status="running")
        if reason is None:
            try:
                from . import wetransfer_uploader as wt
                url = wt.upload_zip_to_wetransfer(str(_zip_path(token)), sender_email=sender_email, on_step=step,
                                                  debug_dir=DATA / "wetransfer_debug", ask_code=ask_code)
                update(status="success", wetransfer_url=url, fallback_used=False, progress=100, step="done")
                return
            except Exception as e:  # NOSONAR - whatever broke, the fallback below still gives a link
                reason = e.detail if isinstance(e, HTTPException) else str(e)
                logger.warning("WeTransfer upload failed, falling back to a server link: %s", reason)
        step("server_link", 95)
        try:
            shared = _share_zip(_zip_path(token))
        except Exception as e:  # noqa: BLE001
            update(status="failed", error=f"WeTransfer failed ({reason}) and the server link could not be made either: "
                                          f"{e.detail if isinstance(e, HTTPException) else e}")
            return
        where = ("It opens only on this computer: the backend listens on 127.0.0.1. To share it on the office network, "
                 "start the backend with --host 0.0.0.0 (and allow the port through the firewall)." if share["local_only"]
                 else "It works for anyone who can reach this server (the office network or VPN), not the open internet.")
        days = _share_days()
        update(status="success", fallback_used=True, progress=100, step="done", wetransfer_error=reason,
               wetransfer_url=f"{share['base']}/api/shared/{shared['id']}/{asset_zip.ZIP_NAME}",
               local_only=share["local_only"], expires_at=shared["expires_at"],
               message=f"WeTransfer automated link creation failed, so this is a download link served by this server "
                       f"instead (kept {days:g} day{'' if days == 1 else 's'}). {where}")
    finally:
        with _wt_lock:
            _wt_codes.pop(job_id, None)
        with _asset_zips_lock:
            z = _asset_zips.get(token)
            drop = False
            if z is not None:
                z.busy -= 1
                drop = z.discard and not z.busy
                if drop:
                    _asset_zips.pop(token)
        if drop:
            _drop_zip_dir(z.dir)


@app.post("/api/export-wetransfer", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 422: {"description": "Validation error"}})
def export_wetransfer(body: WeTransferRequest, request: Request):
    """Get a share link for a ZIP; answers {job_id}. Poll GET /api/export-wetransfer/{job_id} for {status:
    queued|running|success|failed, step, progress, wetransfer_url, fallback_used, message, local_only, expires_at, error}.
    WeTransfer is tried first; when it cannot be used - Playwright missing, the ZIP over the size limit, or any failure
    on the site - `wetransfer_url` is a server link and `fallback_used` is true (see _wt_worker)."""
    token = body.token
    if not token:
        if not body.shop_ids:
            raise HTTPException(422, "send the token of a generated ZIP, or shop_ids")
        token = export_zip(AssetZipRequest(shop_ids=body.shop_ids, numbers=body.numbers))["token"]
    email = (body.sender_email or "").strip() or None
    try:
        from . import wetransfer_uploader as wt_check
        if email is not None and not wt_check.valid_email(email):
            raise HTTPException(422, "that sender e-mail address does not look valid")
    except ImportError:
        pass
    path = _zip_path(token)
    size = path.stat().st_size
    skip = None if email else "no sender e-mail was given - WeTransfer requires one for a link transfer"
    try:
        from . import wetransfer_uploader as wt
        wt.check_available()
        if skip is None and size > wt.max_bytes():
            skip = (f"the ZIP is {size / 1e9:.2f} GB, over the {wt.max_bytes() / 1e9:g} GB WeTransfer free-transfer limit "
                    "set on this server (SIGNAGE_WETRANSFER_MAX_GB)")
    except ImportError as e:
        skip = skip or f"Playwright is not installed for the server's Python ({e}); `\"{sys.executable}\" -m pip install playwright`"
    with _asset_zips_lock:
        _asset_zips[token].busy += 1
        _asset_zips[token].at = time.time()          # not swept while the link is being made
    job_id = uuid.uuid4().hex[:12]
    with _wt_lock:
        _wt_jobs[job_id] = {"job_id": job_id, "status": "queued", "step": "queued", "progress": 0,
                            "wetransfer_url": None, "fallback_used": False, "error": None, "bytes": size}
    _wt_pool.submit(_wt_worker, job_id, token, _share_base(request.scope.get("server")), skip, email)
    return {"job_id": job_id, "token": token}


@app.post("/api/export-wetransfer/verify-otp", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 422: {"description": "Validation error"}})
def export_wetransfer_verify_otp(body: VerifyOtpRequest):
    """The code WeTransfer e-mailed to the sender, typed by the person: handed to the upload waiting for it (status
    requires_otp). The result arrives through the usual status polling - success, another requires_otp with `otp_error`
    (rejected, tries left), or the server-link fallback."""
    code = re.sub(r"[\s-]+", "", body.otp_code or "").upper()     # WeTransfer's codes mix letters and digits, e.g. 953GYV
    if not re.fullmatch(r"[A-Z0-9]{4,10}", code):
        raise HTTPException(422, "enter the code from the WeTransfer e-mail (letters and numbers, e.g. 953GYV)")
    with _wt_lock:
        job = _wt_jobs.get(body.session_id)
        codes = _wt_codes.get(body.session_id)
        if job is None:
            raise HTTPException(404, "unknown upload")
        if job["status"] != "requires_otp" or codes is None:
            raise HTTPException(409, "this upload is not waiting for a code (it may have finished or timed out)")
        job.update(status="running", step="verifying", otp_error=None)
    codes.put(code)
    return {"status": "verifying", "session_id": body.session_id}


@app.get("/api/export-wetransfer/{job_id}", responses={404: {"description": "Not found"}})
def export_wetransfer_status(job_id: str):
    with _wt_lock:
        job = _wt_jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown upload")
        return dict(job)


@app.get("/api/shared/{share_id}/{filename}", responses={404: {"description": "Not found"}, 410: {"description": "No longer available"}})
def shared_zip(share_id: str, filename: str):
    """A fallback share link (see _share_zip). Unknown, malformed or expired ids are 404 / 410."""
    from . import asset_zip

    if not SHARE_ID_RE.fullmatch(share_id) or filename != asset_zip.ZIP_NAME:
        raise HTTPException(404, "not found")
    d = SHARED_ZIPS / share_id
    try:
        meta = json.loads((d / _META_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "this link does not exist")
    if time.time() > meta.get("expires_at", 0):
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(410, "this link has expired")
    if not (d / filename).is_file():
        raise HTTPException(404, "this link does not exist")
    return FileResponse(d / filename, media_type=_MEDIA_ZIP, filename=filename)


# ------------------------------------------------------ editor (Phase C)
#
# GET /api/editor/{job}/{shop}/scene returns the converted board as an editor
# scene (scene_ops.py describes the model). The first request builds it: the
# .cdr is opened read-only in the worker subprocess and every leaf shape is
# rendered by CorelDRAW itself (scene_export.py) - that takes seconds to tens
# of seconds, so until it finishes the endpoint answers 202 with progress and
# the editor polls. The build is queued on the same single-worker pool as
# conversions (one CorelDRAW job at a time) and goes through
# corel_supervisor.run_batch, so it inherits the free-RAM refusal floor, the
# hang watchdog and orphan cleanup. The finished scene is cached on disk.

_scene_builds: dict[str, dict] = {}  # shop_id -> {"status": "building"|"error", ...}
_scene_lock = threading.Lock()


def _editor_shop(job_id: str, shop_id: str) -> dict:
    row = db.get_shop(shop_id)
    if not row or row["job_id"] != job_id:
        raise HTTPException(404, _SHOP_NOT_FOUND)
    if row["status"] != "done":
        raise HTTPException(409, f"shop is not converted yet (status: {row['status']})")
    return row


def _scene_dir(job_id: str, shop_id: str) -> Path:
    return JOBS_V2 / job_id / "out" / shop_id / "scene"


def _scene_build_worker(job_id: str, shop_id: str) -> None:
    row = db.get_shop(shop_id)
    files = json.loads(row["files_json"]) if row["files_json"] else {}
    out_dir = JOBS_V2 / job_id / "out" / shop_id
    scene_dir = _scene_dir(job_id, shop_id)
    engine = get_engine(os.environ.get("SIGNAGE_ENGINE", "auto"))
    results_path = CONVERT_RUNS / f"scene_{shop_id}.json"
    try:
        scene_dir.mkdir(parents=True, exist_ok=True)
        (scene_dir / _SCENE_JSON).unlink(missing_ok=True)
        if engine.name == "corel":
            cdr = out_dir / files["cdr"]
            if not cdr.is_file():
                raise FileNotFoundError(f"converted file is missing: {files['cdr']}")
            results = corel_supervisor.run_batch(
                [{"scene_export": str(cdr), "scene_out": str(scene_dir)}], results_path,
            )
            if results[0].get("status") != "done":
                raise RuntimeError(results[0].get("error", "scene export failed"))
        else:
            scene_export.mock_scene(
                scene_dir, to_mm(row["width"], row["width_unit"]), to_mm(row["height"], row["height_unit"]),
            )
        with _scene_lock:
            _scene_builds.pop(shop_id, None)
    except corel_supervisor.RefusedToStart as e:
        with _scene_lock:
            _scene_builds[shop_id] = {"status": "error", "error": str(e), "low_memory": True}
    except Exception as e:
        with _scene_lock:
            _scene_builds[shop_id] = {"status": "error", "error": str(e), "low_memory": False}
    finally:
        for p in (results_path, results_path.with_suffix(_HEARTBEAT_SUFFIX), results_path.with_suffix(_DONE_SUFFIX),
                  results_path.with_suffix(".jobs.json"), results_path.with_suffix(".json.tmp"),
                  results_path.with_suffix(".heartbeat.tmp")):
            p.unlink(missing_ok=True)


def _scene_progress(shop_id: str) -> dict:
    step, pct = "starting", 3
    hb = CONVERT_RUNS / f"scene_{shop_id}.heartbeat"
    if hb.exists():
        try:
            step = json.loads(hb.read_text(encoding="utf-8")).get("step", step)
        except Exception:
            pass
    if step.startswith("images "):
        try:
            done, total = (int(v) for v in step.split()[1].split("/"))
            pct = 10 + int(85 * done / max(total, 1))
        except ValueError:
            pass
    elif step == "walk":
        pct = 8
    elif step == "page_image":
        pct = 96
    return {"status": "building", "step": step, "progress_pct": pct}


def _needs_powerclip_images(scene: dict) -> bool:
    """A scene cached before version 3 has PowerClips whose children carry no images (v1) or empty
    SVGs (v2), so the editor could only outline them or drew them as nothing; before version 4 the frame's own fill was
    missing (frame_image) - pink panels drawn white. Rebuild it once (boards without a PowerClip are untouched)."""
    if scene.get("version", 1) >= scene_export.SCENE_VERSION:
        return False
    return any(n.get("kind") == "powerclip" for n in scene_ops.iter_nodes(scene))


def _scene_cut_short(scene: dict) -> bool:
    """A scene cached while CorelDRAW died part-way (before scene_export stopped doing that): every object after the crash
    has no image, so it is listed in Layers but draws nothing on the canvas. Rebuild it once; a crash during the rebuild
    now fails the build instead of caching it again."""
    return any(scene_export.server_gone(f) for f in (scene.get("stats") or {}).get("image_failures") or [])


@app.get("/api/editor/{job_id}/{shop_id}/scene", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 500: {"description": "Server error"}, 503: {"description": "Service unavailable"}})
def editor_scene(job_id: str, shop_id: str, rebuild: bool = False, retry: bool = False):
    _editor_shop(job_id, shop_id)
    scene_path = _scene_dir(job_id, shop_id) / _SCENE_JSON
    with _scene_lock:
        state = _scene_builds.get(shop_id)
        if state is not None and state["status"] == "building":
            return JSONResponse(_scene_progress(shop_id), status_code=202)
        if state is not None and state["status"] == "error" and not (retry or rebuild):
            raise HTTPException(503 if state.get("low_memory") else 500, state["error"])
        if scene_path.is_file() and not rebuild:
            scene = json.loads(scene_path.read_text(encoding="utf-8"))
            if _needs_powerclip_images(scene) or _scene_cut_short(scene):
                _scene_builds[shop_id] = {"status": "building"}
                _pool.submit(_scene_build_worker, job_id, shop_id)
                return JSONResponse({"status": "building", "step": "queued", "progress_pct": 1}, status_code=202)
            scene["ops"] = db.get_editor_ops(shop_id)
            # Versioned by the scene build (scene.json is written last), so the images can be cached as immutable: re-opening a
            # board no longer re-validates its 100-350 object images one request at a time; a rebuilt scene gets new URLs.
            scene["asset_base"] = f"/api/editor/{job_id}/{shop_id}/asset/v/{int(scene_path.stat().st_mtime)}/"
            return scene
        _scene_builds[shop_id] = {"status": "building"}
    _pool.submit(_scene_build_worker, job_id, shop_id)
    return JSONResponse({"status": "building", "step": "queued", "progress_pct": 1}, status_code=202)


_ASSET_TYPES = {".svg": "image/svg+xml", ".png": "image/png"}


@app.get("/api/editor/{job_id}/{shop_id}/asset/v/{version}/{filename}", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def editor_asset_versioned(job_id: str, shop_id: str, version: str, filename: str):
    """Same file as below under a build-versioned URL (see the scene endpoint's asset_base) - safe to cache forever."""
    r = editor_asset(job_id, shop_id, filename)
    r.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return r


@app.get("/api/editor/{job_id}/{shop_id}/asset/{filename}", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def editor_asset(job_id: str, shop_id: str, filename: str):
    _editor_shop(job_id, shop_id)
    scene_dir = _scene_dir(job_id, shop_id).resolve()
    p = (scene_dir / "page.png") if filename == "page.png" else (scene_dir / "img" / filename)
    p = p.resolve()
    if scene_dir not in p.parents or not p.is_file() or p.suffix not in _ASSET_TYPES:
        raise HTTPException(404)
    return FileResponse(p, media_type=_ASSET_TYPES[p.suffix], headers={"Cache-Control": "private, max-age=300"})


# ------------------------------------------------------ product-slot replacement assets
#
# A designer's own upload for a `product_image` slot (see product_engine.py). Stored per-shop
# (never in signage_dataset/, never under the master's own directory) so `export_replay.py`'s
# Replayer can resolve the SAME file back into CorelDRAW at "Save and Generate" time - the op's
# `asset.path` this endpoint hands back is exactly the filename Replayer._resolve_asset_path expects,
# resolved against `_product_assets_dir` on both ends.

_PRODUCT_ASSET_TYPES = {".png": "image/png", ".jpg": _MEDIA_JPEG, _JPEG_SUFFIX: _MEDIA_JPEG,
                        _WEBP_SUFFIX: "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}


def _product_assets_dir(job_id: str, shop_id: str) -> Path:
    return JOBS_V2 / job_id / "out" / shop_id / "product_assets"


@app.post("/api/editor/{job_id}/{shop_id}/product-assets", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 422: {"description": "Validation error"}})
async def upload_product_asset(job_id: str, shop_id: str, file: UploadFile = File(...)):
    """Saves an uploaded replacement image and reports its natural pixel size - the caller builds a
    `swap_image`/`update_product_slot` op from the result via product_engine.js's swapImageOp/
    updateSlotOp (`{"name": ..., "w": ..., "h": ..., "path": ...}`), the same as any other asset."""
    _editor_shop(job_id, shop_id)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _PRODUCT_ASSET_TYPES:
        raise HTTPException(422, f"unsupported image type {ext!r} - use one of {sorted(_PRODUCT_ASSET_TYPES)}")
    assets_dir = _product_assets_dir(job_id, shop_id)
    assets_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest = assets_dir / stored_name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        from PIL import Image
        with Image.open(dest) as im:
            w, h = im.size
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, f"could not read {file.filename!r} as an image: {e}") from None
    return {"name": file.filename or stored_name, "w": w, "h": h, "path": stored_name}


@app.get("/api/editor/{job_id}/{shop_id}/product-asset/{filename}", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def editor_product_asset(job_id: str, shop_id: str, filename: str):
    _editor_shop(job_id, shop_id)
    assets_dir = _product_assets_dir(job_id, shop_id).resolve()
    p = (assets_dir / filename).resolve()
    if assets_dir not in p.parents or not p.is_file() or p.suffix.lower() not in _PRODUCT_ASSET_TYPES:
        raise HTTPException(404)
    return FileResponse(p, media_type=_PRODUCT_ASSET_TYPES[p.suffix.lower()], headers={"Cache-Control": "private, max-age=300"})


class EditorOps(BaseModel):
    ops: list[dict]


MAX_EDITOR_OPS = 5000


def _load_scene(job_id: str, shop_id: str) -> dict | None:
    p = _scene_dir(job_id, shop_id) / _SCENE_JSON
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


@app.get("/api/editor/{job_id}/{shop_id}/ops", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def editor_get_ops(job_id: str, shop_id: str):
    _editor_shop(job_id, shop_id)
    return {"ops": db.get_editor_ops(shop_id)}


@app.put("/api/editor/{job_id}/{shop_id}/ops", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 413: {"description": "Too large"}, 422: {"description": "Validation error"}})
def editor_put_ops(job_id: str, shop_id: str, body: EditorOps):
    _editor_shop(job_id, shop_id)
    if len(body.ops) > MAX_EDITOR_OPS:
        raise HTTPException(413, f"too many operations (max {MAX_EDITOR_OPS})")
    scene = _load_scene(job_id, shop_id)
    if scene is None:
        raise HTTPException(409, "scene has not been built yet")
    try:  # never persist a list that cannot be replayed
        edited = scene_ops.apply_ops(scene, body.ops)
    except scene_ops.OpError as e:
        raise HTTPException(422, str(e))
    db.set_editor_ops(shop_id, body.ops)
    _capture_correction(job_id, shop_id, scene, edited)
    return {"saved": len(body.ops)}


def recapture_corrections(statuses: tuple[str, ...] = ("pending",)) -> dict:
    """Re-derive stored editor corrections of the given statuses from the saved edit lists and the cached scenes, keeping each one's status.
    Used after the learner gained something new (nested objects, text style): records made earlier only hold what it could see then.
    Dataset seeds are left alone. Returns {"updated": n, "skipped": n}."""
    done = {"updated": 0, "skipped": 0}
    for row in db.list_corrections():
        if row["status"] not in statuses or row["record"].get("source"):
            continue
        shop = db.get_shop(row["shop_id"])
        scene = _load_scene(shop["job_id"], shop["id"]) if shop else None
        ops = db.get_editor_ops(row["shop_id"]) if shop else []
        if scene is None or not ops:
            done["skipped"] += 1
            continue
        try:
            edited = scene_ops.apply_ops(scene, ops)
        except scene_ops.OpError:
            done["skipped"] += 1
            continue
        report = json.loads(shop["report_json"]) if shop.get("report_json") else {}
        made_from = db.get_job((report.get("master_used") or {}).get("job_id") or shop["job_id"]) or db.get_job(shop["job_id"])
        record = corrections.build_record(shop, made_from, scene, edited, report.get("layout"))
        if not record:
            done["skipped"] += 1
            continue
        record["status"] = row["status"]
        db.save_correction(record)
        done["updated"] += 1
    return done


def _capture_correction(job_id: str, shop_id: str, base: dict, edited: dict) -> None:
    """Record what the designer changed (correction memory). Learning is never allowed to break saving: any failure is only logged."""
    try:
        if db.get_setting(INTELLIGENCE_KEY, "1") != "1":
            return                                              # Corel Intelligence is switched off: collect nothing
        shop = db.get_shop(shop_id)
        try:
            report = json.loads(shop["report_json"]) if shop.get("report_json") else {}
        except ValueError:
            report = {}
        # the master the board was really made from (a shop can use another upload's master), not just its own job's
        made_from = db.get_job((report.get("master_used") or {}).get("job_id") or job_id) or db.get_job(job_id)
        record = corrections.build_record(shop, made_from, base, edited, report.get("layout"))
        if record:
            db.save_correction(record)
        else:
            db.delete_correction(shop_id)
    except Exception:
        logging.getLogger("signage.corrections").exception("could not record the correction for shop %s", shop_id)


@app.get("/api/editor/{job_id}/{shop_id}/replayed", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 422: {"description": "Validation error"}})
def editor_replayed(job_id: str, shop_id: str):
    """The saved operation list applied to the pristine scene, server side (what Phase D replays)."""
    _editor_shop(job_id, shop_id)
    scene = _load_scene(job_id, shop_id)
    if scene is None:
        raise HTTPException(409, "scene has not been built yet")
    try:
        return scene_ops.apply_ops(scene, db.get_editor_ops(shop_id))
    except scene_ops.OpError as e:
        raise HTTPException(422, str(e))


class ConvertOrientationRequest(BaseModel):
    scene: dict
    target_w: float
    target_h: float
    same_orientation_fit: bool = False   # scene is a master of the target's orientation: uniform fit + padding


@app.post("/api/scene/convert-orientation", responses={422: {"description": "Validation error"}})
def convert_orientation(body: ConvertOrientationRequest, response: Response):
    """Re-lays a scene out for a different target page size (mm) - see orientation_adapter.py.

    Stateless and not tied to a job/shop id: the caller sends the scene JSON it already has (the
    editor's current, possibly-unsaved, in-memory scene - not necessarily what's on disk), gets back
    the transformed scene plus the op list that produced it, and merges those ops into its own
    undo/op timeline exactly like a locally-generated `resize`/`page` op - nothing here writes to
    disk or the `editor_ops` table itself, and nothing it reads from is cached either: `body.scene`
    is read fresh from the request on every call and passed straight to
    `orientation_adapter.convert_orientation`, so two calls with different `target_w`/`target_h` (or
    a changed `scene`) always recompute from scratch - there was never a cache here to invalidate.
    The explicit `Cache-Control: no-store` below is defensive (rules out a browser/proxy layer ever
    reusing a stale response for what is a POST anyway, browsers don't cache those by default), not a
    fix for a caching bug that was found - see CLAUDE.md "Orientation adaptation" for how this was
    checked."""
    response.headers["Cache-Control"] = "no-store"
    try:
        ops = orientation_adapter.convert_orientation(body.scene, body.target_w, body.target_h,
                                                        same_orientation_fit=body.same_orientation_fit)
        scene = scene_ops.apply_ops(body.scene, ops)
    except scene_ops.OpError as e:
        raise HTTPException(422, str(e))
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(422, f"malformed scene: {e}")
    return {"scene": scene, "ops": ops}


@app.get("/api/corel/health")
def corel_health():
    """Can this server run conversions? For the launch screen. Deliberately does NOT start CorelDRAW: there is no long-running
    CorelDRAW to "connect" to - every job launches its own hidden instance - and a test launch would cost seconds and RAM and
    race a running conversion. It reports what a job would use: the engine, the installed CorelDRAW versions (registry +
    executable version, in the order a job tries them) and free RAM against the floor a batch needs."""
    kind = os.environ.get("SIGNAGE_ENGINE", "auto")
    engine = ("corel" if sys.platform == "win32" else "mock") if kind == "auto" else kind
    installs = corel_util.corel_installs() if sys.platform == "win32" else []
    try:
        free = round(corel_util.check_memory(), 2)
    except Exception:
        free = None
    floor = corel_supervisor.MIN_FREE_RAM_GB
    low = free is not None and free < floor
    if engine == "mock":
        ok, message = True, "Mock engine: previews are simulated, CorelDRAW is not needed."
    elif installs:
        ok = True
        message = f"CorelDRAW {installs[0]['version'] or ''} ready".strip() + (
            f" - low memory ({free} GB free, conversions need {floor} GB)" if low else "")
    else:
        ok = False
        message = ("No CorelDRAW installation found on the server. Install CorelDRAW (2019 or newer), or check "
                   "SIGNAGE_COREL_PROGID if it is set." if sys.platform == "win32" else
                   "CorelDRAW needs a Windows server; set SIGNAGE_ENGINE=mock to try the app without it.")
    return {"ok": ok, "engine": engine, "corel_available": bool(installs), "installs": installs,
            "selected": installs[0] if installs else None, "pinned": os.environ.get("SIGNAGE_COREL_PROGID") or None,
            "free_ram_gb": free, "min_free_ram_gb": floor, "low_memory": low, "message": message}


class FontSubstituteRequest(BaseModel):
    shop_id: str
    original_font: str
    substitute_font: str
    is_permanent: bool = True
    project_id: str | None = None          # accepted; substitutions are kept per shop


def _installed_font_name(name: str) -> str | None:
    """The installed family matching `name` (case-insensitive), None if it is not installed; `name` itself when the
    installed-font list is unavailable (not Windows) - the check is skipped there, as in the editor."""
    info = fonts.installed_fonts()
    if not info.get("available"):
        return name
    by_lower = {f.lower(): f for f in info.get("fonts", [])}
    return by_lower.get(name.strip().lower())


@app.get("/api/fonts/substitutions", responses={404: {"description": "Not found"}})
def font_substitutions(shop_id: str):
    """The permanent substitutions saved for a shop: {original font: substitute font}."""
    if not db.get_shop(shop_id):
        raise HTTPException(404, _SHOP_NOT_FOUND)
    return {"shop_id": shop_id, "substitutions": db.get_font_substitutions(shop_id)}


@app.post("/api/fonts/substitute", responses={404: {"description": "Not found"}, 422: {"description": "Validation error"}})
def font_substitute(body: FontSubstituteRequest):
    """Save (is_permanent) a replacement for a font missing on this server: every export of this shop's board sets text
    in `original_font` to `substitute_font` in CorelDRAW (export_replay.apply_font_substitutions). A temporary choice
    lives only in the editor tab and is not stored - this answers {saved: false} for it. The substitute must be installed
    on the server, because CorelDRAW writes the files and silently ignores a font it does not have."""
    if not db.get_shop(body.shop_id):
        raise HTTPException(404, _SHOP_NOT_FOUND)
    original, wanted = body.original_font.strip(), body.substitute_font.strip()
    if not original or not wanted:
        raise HTTPException(422, "name both the missing font and its replacement")
    if original.lower() == wanted.lower():
        raise HTTPException(422, "the replacement must be a different font")
    installed = _installed_font_name(wanted)
    if installed is None:
        raise HTTPException(422, f"{wanted!r} is not installed on this server - CorelDRAW would ignore it; pick an installed font")
    if body.is_permanent:
        db.set_font_substitution(body.shop_id, original, installed)
    return {"saved": body.is_permanent, "original_font": original, "substitute_font": installed,
            "substitutions": db.get_font_substitutions(body.shop_id)}


@app.delete("/api/fonts/substitute", responses={404: {"description": "Not found"}})
def font_substitute_delete(shop_id: str, original_font: str):
    """Forget a permanent substitution: exports go back to CorelDRAW's own handling of the missing font."""
    if not db.get_shop(shop_id):
        raise HTTPException(404, _SHOP_NOT_FOUND)
    return {"deleted": db.delete_font_substitution(shop_id, original_font.strip()),
            "substitutions": db.get_font_substitutions(shop_id)}


@app.get("/api/v2/fonts")
def api_v2_fonts(refresh: bool = False):
    """Installed fonts for the Shops Queue's English / Tamil font pickers: {available, all_fonts, english_fonts, tamil_fonts,
    tamil_detection}. Read from Windows (the same list CorelDRAW sees), NOT by launching CorelDRAW through COM: a Dispatch
    starts a hidden CorelDRAW (seconds and hundreds of MB) and would compete with a running conversion for the one CorelDRAW
    this app allows at a time. Tamil support is read from each font file's character map (fonts.tamil_fonts)."""
    return fonts.script_fonts(refresh)


@app.get("/api/fonts")
def api_fonts(refresh: bool = False):
    """Installed font families (the editor refuses to name a font CorelDRAW would silently ignore)."""
    return fonts.installed_fonts(refresh)


_FONT_MEDIA = {".ttf": "font/ttf", ".otf": "font/otf", ".ttc": "font/collection"}


@app.get("/api/fonts/file", responses={404: {"description": "Not found"}})
def api_font_file(family: str):
    """The installed font file for `family`, so the editor's live text renders in the board's real font on any browser."""
    path = fonts.font_file(family)
    if path is None:
        raise HTTPException(404, f"font {family!r} is not installed on the server")
    return FileResponse(path, media_type=_FONT_MEDIA.get(path.suffix.lower(), "application/octet-stream"),
                        headers={"Cache-Control": "public, max-age=86400"})


# ------------------------------------------------------ export (Phase D)
#
# "Save and Generate": POST .../export replays the SAVED operation list on the
# converted .cdr through CorelDRAW (export_replay.py) and exports the chosen
# formats. The job is queued on the same single-worker pool as everything else
# that needs CorelDRAW and runs via corel_supervisor (RAM floor, hang
# watchdog, orphan cleanup). Progress comes from the worker's heartbeat file.

class ExportRequest(BaseModel):
    formats: list[str]
    options: dict | None = None


def _export_dir(job_id: str, shop_id: str, export_id: str) -> Path:
    return JOBS_V2 / job_id / "out" / shop_id / "exports" / export_id


def _mock_export(expected: dict, formats: list[str], opts: dict, out_dir: Path, base: str, heartbeat: Path) -> dict:
    """MockEngine stand-in (no CorelDRAW): simple placeholder files so the whole flow is testable anywhere.
    Clearly flagged in the report - real output only ever comes from CorelDRAW."""
    import zipfile

    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    timings: dict[str, float] = {}
    sizes = export_replay.raster_sizes(expected["page"]["width"], expected["page"]["height"], opts, ["png", "jpeg"])

    def beat(step: str) -> None:
        heartbeat.write_text(json.dumps({"step": step}), encoding="utf-8")
        time.sleep(0.25)

    for step in ["launch", "open"]:
        beat(step)
    for fmt in export_replay.export_order(formats, opts):
        beat(fmt)
        size = sizes["png" if fmt in ("cdr", "pdf") else fmt]
        scale = min(1.0, 1200 / max(size["w_px"], size["h_px"]))
        w, h = max(1, round(size["w_px"] * scale)), max(1, round(size["h_px"] * scale))
        p = out_dir / f"{base}.{export_replay.EXTENSION[fmt]}"
        if fmt == "cdr":
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("mimetype", "application/x-cdr")
        else:
            transparent = fmt == "png" and opts["png"]["png_background"] == "transparent"
            im = Image.new("RGBA" if transparent else "RGB", (w, h), (255, 255, 255, 0) if transparent else (255, 255, 255))
            d = ImageDraw.Draw(im)
            for layer in expected["layers"]:
                for n in layer["children"]:
                    x0, y0 = n["x"] / expected["page"]["width"] * w, h - (n["y"] + n["h"]) / expected["page"]["height"] * h
                    d.rectangle([x0, y0, x0 + n["w"] / expected["page"]["width"] * w, y0 + n["h"] / expected["page"]["height"] * h],
                                outline=(224, 24, 47))
            d.text((10, 10), f"MOCK EXPORT - not from CorelDRAW ({base})", fill=(0, 0, 0))
            if fmt == "jpeg" and opts["jpeg"]["color"] == "cmyk":
                im = im.convert("CMYK")
            im.save(p, "PDF" if fmt == "pdf" else "PNG" if fmt == "png" else "JPEG")
        files[fmt] = p.name
        timings[fmt] = 0.25
    return {
        "mock": True, "formats": formats, "options": opts, "files": files, "timings_s": timings,
        "file_bytes": {k: (out_dir / v).stat().st_size for k, v in files.items()},
        "verification": {"ok": True, "compared": 0, "mismatches": []}, "warnings": ["Mock engine: placeholder files, not a CorelDRAW export."],
    }


def _export_worker(job_id: str, shop_id: str, export_id: str) -> None:
    row = db.get_export(export_id)
    shop_row = db.get_shop(shop_id)
    files = json.loads(shop_row["files_json"]) if shop_row["files_json"] else {}
    out_dir = _export_dir(job_id, shop_id, export_id)
    formats, options, ops = json.loads(row["formats_json"]), json.loads(row["options_json"]), json.loads(row["ops_json"])
    results_path = CONVERT_RUNS / f"export_{export_id}.json"
    db.set_export_status(export_id, "running")
    try:
        engine = get_engine(os.environ.get("SIGNAGE_ENGINE", "auto"))
        if engine.name == "corel":
            spec = {
                "cdr": str(JOBS_V2 / job_id / "out" / shop_id / files["cdr"]),
                "scene": str(_scene_dir(job_id, shop_id) / _SCENE_JSON),
                "ops": ops, "formats": formats, "options": options,
                "out_dir": str(out_dir), "base_name": file_naming.shop_basename(shop_row),
                "assets_dir": str(_product_assets_dir(job_id, shop_id)),
                "font_subs": db.get_font_substitutions(shop_id),     # permanent "Missing Font" choices
            }
            entry = corel_supervisor.run_batch([{"export_replay": spec}], results_path)[0]
            if entry.get("status") != "done":
                raise RuntimeError(entry.get("error", "export failed"))
            report = entry["report"]
        else:
            scene = _load_scene(job_id, shop_id)
            report = _mock_export(scene_ops.apply_ops(scene, ops), formats, options, out_dir,
                                  export_replay.safe_name(shop_row["name"]), results_path.with_suffix(_HEARTBEAT_SUFFIX))
            subs = db.get_font_substitutions(shop_id)
            if subs:   # recorded only - the mock engine renders no text
                report["font_substitutions"] = {k: {"to": v, "changed": None, "mock": True} for k, v in subs.items()}
        db.set_export_result(export_id, report["files"], report)
    except corel_supervisor.RefusedToStart as e:
        db.set_export_status(export_id, "failed", str(e))
    except Exception as e:
        db.set_export_status(export_id, "failed", str(e))
    finally:
        for p in (results_path, results_path.with_suffix(_HEARTBEAT_SUFFIX), results_path.with_suffix(_DONE_SUFFIX),
                  results_path.with_suffix(".jobs.json"), results_path.with_suffix(".json.tmp"),
                  results_path.with_suffix(".heartbeat.tmp")):
            p.unlink(missing_ok=True)


@app.get("/api/editor/export-estimates")
def editor_export_estimates():
    return db.get_export_step_estimates()


@app.post("/api/editor/{job_id}/{shop_id}/export", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 422: {"description": "Validation error"}, 503: {"description": "Service unavailable"}})
def editor_export(job_id: str, shop_id: str, body: ExportRequest):
    _editor_shop(job_id, shop_id)
    scene = _load_scene(job_id, shop_id)
    if scene is None:
        raise HTTPException(409, "scene has not been built yet - open the editor first")
    ops = db.get_editor_ops(shop_id)
    try:
        expected = scene_ops.apply_ops(scene, ops) if ops else scene
    except scene_ops.OpError as e:
        raise HTTPException(422, f"the saved edits cannot be replayed: {e}")
    unknown = [f for f in body.formats if f not in export_replay.ALL_FORMATS]
    if unknown:
        raise HTTPException(422, f"unknown format(s): {', '.join(unknown)}")
    formats = [f for f in export_replay.ALL_FORMATS if f in body.formats]
    try:
        opts = export_replay.normalize_options(formats, body.options)
        sizes = export_replay.raster_sizes(expected["page"]["width"], expected["page"]["height"], opts, formats)
        size = max(sizes.values(), key=lambda z: z["megapixels"]) if sizes else None
    except (export_replay.ExportOptionError, ValueError, TypeError) as e:
        raise HTTPException(422, str(e))
    if os.environ.get("SIGNAGE_ENGINE", "auto") != "mock" and get_engine(os.environ.get("SIGNAGE_ENGINE", "auto")).name == "corel":
        free = corel_util.check_memory()
        need = corel_supervisor.MIN_FREE_RAM_GB + (size["megapixels"] * 0.012 if size else 0)
        if free < need:
            raise HTTPException(503, f"not enough free RAM for this export: {free:.2f} GB free, about {need:.2f} GB needed"
                                     + (f" ({size['w_px']} x {size['h_px']} px images)" if size else "") + ". Close other programs or lower the image size.")
    plan = export_replay.plan_steps(formats, len(ops), db.get_export_step_estimates(), opts)
    export_id = uuid.uuid4().hex[:12]
    db.create_export(export_id, shop_id, formats, opts, ops, plan)
    _pool.submit(_export_worker, job_id, shop_id, export_id)
    return {"export_id": export_id, "plan": plan, "raster": size, "sizes": sizes, "ops": len(ops)}


@app.post("/api/editor/{job_id}/{shop_id}/publish", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}, 422: {"description": "Validation error"}, 503: {"description": "Service unavailable"}})
def editor_publish(job_id: str, shop_id: str):
    """"Save Changes" makes the saved edits the board's NEW files: queues one export (cdr, pdf, png, jpeg with the default options) of
    the shop's current edit list, so the ZIP, the print sheet and the row's downloads pick up the edited version without a manual export.
    Nothing is queued when there are no edits, or when an export of exactly these edits is already queued / running / done."""
    _editor_shop(job_id, shop_id)
    ops = db.get_editor_ops(shop_id)
    if not ops:
        return {"status": "unchanged", "export_id": None}
    for listed in db.list_exports(shop_id, limit=20):
        e = db.get_export(listed["id"])
        same = json.loads(e["ops_json"]) == ops and set(export_replay.ALL_FORMATS) <= set(json.loads(e["formats_json"]))
        if same and e["status"] in ("queued", "running", "done"):
            return {"status": "ready" if e["status"] == "done" else "building", "export_id": e["id"]}
    queued = editor_export(job_id, shop_id, ExportRequest(formats=list(export_replay.ALL_FORMATS), options=None))
    return {"status": "building", "export_id": queued["export_id"]}


def _export_row(job_id: str, shop_id: str, export_id: str) -> dict:
    _editor_shop(job_id, shop_id)
    row = db.get_export(export_id)
    if not row or row["shop_id"] != shop_id:
        raise HTTPException(404, "export not found")
    return row


@app.get("/api/editor/{job_id}/{shop_id}/export/{export_id}", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def editor_export_status(job_id: str, shop_id: str, export_id: str):
    row = _export_row(job_id, shop_id, export_id)
    plan = json.loads(row["plan_json"])
    step, sub = None, None
    if row["status"] == "running":
        step = "launch"
        hb = CONVERT_RUNS / f"export_{export_id}.heartbeat"
        if hb.exists():
            try:
                raw = json.loads(hb.read_text(encoding="utf-8")).get("step", step)
                parts = raw.split()
                step = parts[0]
                if len(parts) > 1 and "/" in parts[1]:
                    done, total = (int(v) for v in parts[1].split("/"))
                    sub = {"done": done, "total": total}
            except Exception:
                pass
    pct = 0
    if row["status"] == "done":
        pct = 100
    elif step:
        i = next((k for k, s in enumerate(plan) if s["key"] == step), None)
        if i is not None:
            start = plan[i - 1]["endPct"] if i else 0
            frac = (sub["done"] - 1) / sub["total"] if sub and sub["total"] else 0
            pct = int(start + (plan[i]["endPct"] - 1 - start) * frac)
    report = json.loads(row["report_json"]) if row["report_json"] else None
    return {
        "export_id": export_id, "status": row["status"], "error": row["error"], "step": step, "sub": sub,
        "progress_pct": pct, "plan": plan, "formats": json.loads(row["formats_json"]),
        "files": json.loads(row["files_json"]) if row["files_json"] else None,
        "report": report,
    }


@app.get("/api/editor/{job_id}/{shop_id}/exports", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def editor_export_list(job_id: str, shop_id: str):
    _editor_shop(job_id, shop_id)
    return [{"export_id": r["id"], "status": r["status"], "formats": json.loads(r["formats_json"]),
             "files": json.loads(r["files_json"]) if r["files_json"] else None,
             "error": r["error"], "created_at": r["created_at"], "completed_at": r["completed_at"]}
            for r in db.list_exports(shop_id)]


def _std_ext(p: Path) -> str:
    """The extension a downloaded file gets: `.jpeg` is spelt `.jpg` like the designers' files."""
    return ".jpg" if p.suffix.lower() in (_JPEG_SUFFIX, ".jpg") else p.suffix.lower()


def _zip_name(shop: dict) -> str:
    """A shop's export ZIP is named like its files: `76 - 125 X 48 Inch - Nonlit - SHOP NAME.zip` (file_naming.py)."""
    return f"{file_naming.shop_basename(shop)}.zip"


@app.get("/api/editor/{job_id}/{shop_id}/exports/{export_id}/zip", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def editor_export_zip(job_id: str, shop_id: str, export_id: str):
    """Every generated file of one export (cdr/pdf/png/jpeg) in a single `<S.no> - <size> - <type> - <SHOP>.zip`. Built on
    the server rather than in the browser: a CDR is often 100-300 MB and would have to sit in browser memory to be
    zipped client-side. Members are STORED, not deflated - a CDR/PNG/JPEG/PDF is already compressed, so deflating
    only costs time. The temp archive is deleted once it has been sent."""
    shop = _editor_shop(job_id, shop_id)
    row = _export_row(job_id, shop_id, export_id)
    files = json.loads(row["files_json"]) if row["files_json"] else {}
    base = _export_dir(job_id, shop_id, export_id).resolve()
    members = [(base / name).resolve() for name in files.values()]
    members = [m for m in members if base in m.parents and m.is_file()]
    if row["status"] != "done" or not members:
        raise HTTPException(409, "this export has no finished files to download")
    tmp = tempfile.NamedTemporaryFile(prefix="export_", suffix=".zip", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED) as z:
        stem = file_naming.shop_basename(shop)
        for m in members:
            z.write(m, arcname=f"{stem}{_std_ext(m)}")
    return FileResponse(tmp.name, media_type=_MEDIA_ZIP, filename=_zip_name(shop),
                        background=BackgroundTask(lambda: Path(tmp.name).unlink(missing_ok=True)))


@app.get("/api/editor/{job_id}/{shop_id}/exports/{export_id}/files/{filename}", responses={404: {"description": "Not found"}, 409: {"description": "Conflict with the current state"}})
def editor_export_file(job_id: str, shop_id: str, export_id: str, filename: str):
    shop = _editor_shop(job_id, shop_id)
    _export_row(job_id, shop_id, export_id)
    base = _export_dir(job_id, shop_id, export_id).resolve()
    p = (base / filename).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=f"{file_naming.shop_basename(shop)}{_std_ext(p)}")
