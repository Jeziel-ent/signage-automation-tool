from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import corel_supervisor, db
from .batch_import import parse_shop_lines
from .engines import get_engine
from .layout import to_mm

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

app = FastAPI(title="signage-automation-tool")
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


@app.post("/api/brands")
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
            out = engine.process(jdir / "master.cdr", shop, jdir / "out" / f"{i+1:02d}")
            res.update(status="done", files=out["files"], report=out["report"], note=out.get("note"))
        except Exception as e:  # keep going with the other shops
            res.update(status="error", error=str(e))
    job["status"] = "done" if all(r["status"] == "done" for r in job["results"]) else "error"


@app.post("/api/jobs")
async def create_job(
    master: UploadFile = File(...),
    brand: str = Form(...),
    shops: str = Form(...),
):
    if not (master.filename or "").lower().endswith(".cdr"):
        raise HTTPException(400, "Master file must be a .cdr")
    try:
        shop_list = json.loads(shops)
        assert isinstance(shop_list, list) and shop_list
        for s in shop_list:
            assert s["name"].strip()
            assert float(s["width"]) > 0 and float(s["height"]) > 0
            assert s["unit"] in ("mm", "cm", "in", "ft", "m")
            # optional per-shop content fields (see CLAUDE.md "Per-shop content
            # replacement"): shop_name_local (Tamil display name), phone, gst,
            # address_lines (list[str]) - all freeform, engine treats missing/empty
            # as "don't touch that line"
    except Exception:
        raise HTTPException(400, "Invalid shops: each needs name, width>0, height>0, unit")

    job_id = uuid.uuid4().hex[:12]
    jdir = JOBS / job_id
    jdir.mkdir(parents=True)
    with open(jdir / "master.cdr", "wb") as f:
        shutil.copyfileobj(master.file, f)

    _jobs[job_id] = {
        "id": job_id, "brand": brand, "master": master.filename, "status": "queued",
        "shops": shop_list,
        "results": [{"shop": s["name"], "status": "queued"} for s in shop_list],
    }
    _pool.submit(_run, job_id)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "job not found")
    return _jobs[job_id]


@app.get("/api/jobs/{job_id}/files/{idx}/{filename}")
def get_file(job_id: str, idx: int, filename: str):
    p = (JOBS / job_id / "out" / f"{idx:02d}" / filename).resolve()
    if JOBS.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/jobs/{job_id}/download.zip")
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


@app.post("/api/v2/brands")
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


@app.post("/api/v2/upload")
async def v2_upload(master: UploadFile = File(...), brand: str = Form(...)):
    if not (master.filename or "").lower().endswith(".cdr"):
        raise HTTPException(400, "Master file must be a .cdr")
    if not brand.strip():
        raise HTTPException(400, "brand required")

    job_id = uuid.uuid4().hex[:12]
    jdir = JOBS_V2 / job_id
    jdir.mkdir(parents=True)
    master_path = jdir / "master.cdr"
    # UploadFile spools large files to disk itself (SpooledTemporaryFile) -
    # this copy is a plain streamed write, fine up to the 300MB target the
    # UI's upload progress bar is sized for.
    with open(master_path, "wb") as f:
        shutil.copyfileobj(master.file, f)

    db.create_job(job_id, brand.strip(), master.filename, str(master_path))
    preview_path, preview_error = _extract_cdr_preview(master_path)
    db.set_job_preview(job_id, str(preview_path) if preview_path else None, preview_error)

    return {
        "id": job_id,
        "preview_url": f"/api/v2/jobs/{job_id}/preview" if preview_path else None,
        "preview_error": preview_error,
    }


@app.get("/api/v2/jobs/{job_id}")
def v2_get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    job["preview_url"] = f"/api/v2/jobs/{job_id}/preview" if job.get("preview_path") else None
    job["shops"] = db.list_shops(job_id)
    return job


@app.get("/api/v2/jobs/{job_id}/preview")
def v2_job_preview(job_id: str):
    job = db.get_job(job_id)
    if not job or not job.get("preview_path"):
        raise HTTPException(404, "no preview available")
    return FileResponse(job["preview_path"])


@app.post("/api/v2/jobs/{job_id}/shops")
def v2_add_shop(job_id: str, payload: dict):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    try:
        name = payload["name"].strip()
        width = float(payload["width"])
        height = float(payload["height"])
        width_unit = payload.get("width_unit", "in")
        height_unit = payload.get("height_unit", "in")
        reference = (payload.get("reference") or "").strip() or None
        assert name and width > 0 and height > 0
        assert width_unit in ("mm", "cm", "in", "ft") and height_unit in ("mm", "cm", "in", "ft")
    except Exception:
        raise HTTPException(400, "invalid shop: need name, width>0, height>0, unit in mm/cm/in/ft")

    shop_id = uuid.uuid4().hex[:12]
    seq_no = len(db.list_shops(job_id)) + 1
    db.create_shop(shop_id, job_id, seq_no, name, width, width_unit, height, height_unit, reference)
    return db.get_shop(shop_id)


@app.get("/api/v2/jobs/{job_id}/shops")
def v2_list_shops(job_id: str):
    if not db.get_job(job_id):
        raise HTTPException(404, "job not found")
    return db.list_shops(job_id)


# Discrete CorelEngine steps (see app/engines.py's `step()` closure) mapped to
# a rough completion percentage - the best "real" progress signal available,
# since COM gives step transitions, not byte-level progress within a step.
_STEP_PERCENT = {"launch": 10, "open": 25, "tile_resize": 55, "saveas": 75, "pdf": 88, "png": 97}


def _v2_convert_worker(shop_id: str, job_id: str) -> None:
    db.set_shop_status(shop_id, "converting", step="starting")
    shop_row = db.get_shop(shop_id)
    job_row = db.get_job(job_id)
    out_dir = JOBS_V2 / job_id / "out" / shop_id
    shop_dict = {
        "name": shop_row["name"],
        "width": to_mm(shop_row["width"], shop_row["width_unit"]),
        "height": to_mm(shop_row["height"], shop_row["height_unit"]),
        "unit": "mm",
        "brand": job_row["brand"],
    }
    master_path = Path(job_row["master_path"])
    engine = get_engine(os.environ.get("SIGNAGE_ENGINE", "auto"))

    try:
        if engine.name == "corel":
            results_path = CONVERT_RUNS / f"{shop_id}.json"
            jobs = [{"master_path": str(master_path), "shop": shop_dict, "out_dir": str(out_dir)}]
            results = corel_supervisor.run_batch(jobs, results_path)
            entry = results[0]
            if entry.get("status") == "done":
                out = entry["result"]
                db.set_shop_result(shop_id, out["files"], out["report"])
            else:
                db.set_shop_status(shop_id, "failed", error=entry.get("error", "unknown error"))
            for p in (results_path, results_path.with_suffix(".heartbeat"), results_path.with_suffix(".done")):
                p.unlink(missing_ok=True)
        else:
            out = engine.process(master_path, shop_dict, out_dir)
            db.set_shop_result(shop_id, out["files"], out["report"])
    except Exception as e:
        db.set_shop_status(shop_id, "failed", error=str(e))


@app.post("/api/v2/shops/{shop_id}/convert")
def v2_convert_shop(shop_id: str):
    row = db.get_shop(shop_id)
    if not row:
        raise HTTPException(404, "shop not found")
    if row["status"] in ("queued", "converting"):
        raise HTTPException(409, "already converting")
    db.set_shop_status(shop_id, "queued")
    _pool.submit(_v2_convert_worker, shop_id, row["job_id"])
    return {"status": "queued"}


@app.get("/api/v2/shops/{shop_id}/status")
def v2_shop_status(shop_id: str):
    row = db.get_shop(shop_id)
    if not row:
        raise HTTPException(404, "shop not found")

    if row["status"] == "done":
        row["progress_pct"] = 100
        row["files"] = json.loads(row["files_json"]) if row["files_json"] else None
        row["report"] = json.loads(row["report_json"]) if row["report_json"] else None
    elif row["status"] == "failed":
        row["progress_pct"] = 0
    elif row["status"] == "converting":
        step = row["step"]
        pct = _STEP_PERCENT.get(step, 5)
        heartbeat_path = CONVERT_RUNS / f"{shop_id}.heartbeat"
        if heartbeat_path.exists():
            try:
                hb = json.loads(heartbeat_path.read_text(encoding="utf-8"))
                step = hb.get("step", step)
                pct = _STEP_PERCENT.get(step, pct)
            except Exception:
                pass
        row["step"], row["progress_pct"] = step, pct
    else:  # "new" or "queued"
        row["progress_pct"] = 0
    return row


@app.get("/api/v2/shops/{shop_id}/files/{filename}")
def v2_shop_file(shop_id: str, filename: str):
    row = db.get_shop(shop_id)
    if not row:
        raise HTTPException(404, "shop not found")
    out_dir = (JOBS_V2 / row["job_id"] / "out" / shop_id).resolve()
    p = (out_dir / filename).resolve()
    if out_dir not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)
