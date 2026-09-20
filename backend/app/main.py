from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .batch_import import parse_shop_lines
from .engines import get_engine

DATA = Path(os.environ.get("SIGNAGE_DATA", Path(__file__).resolve().parents[1] / "data"))
JOBS = DATA / "jobs"
BRANDS_FILE = DATA / "brands.json"
JOBS.mkdir(parents=True, exist_ok=True)

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
