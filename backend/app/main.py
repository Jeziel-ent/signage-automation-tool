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
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import corel_supervisor, corel_util, db, export_replay, fonts, orientation_adapter, scene_export, scene_ops
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
        # No upload UI for this yet (see CLAUDE.md "New UI") - accepted now so the
        # data model doesn't need another migration once one exists.
        reference_file_path = (payload.get("reference_file_path") or "").strip() or None
        # Optional per-shop contact fields (see CLAUDE.md "Per-shop content
        # replacement", already supported by the old /api/jobs flow - this wires
        # the same engine capability into the new v2 UI/API). `.strip() or None`
        # matters here: compute_layout treats None as "leave the master's own
        # text alone" but "" as "replace with blank" - the frontend must never
        # send an empty string for a field the user left untouched, so this
        # normalizes that at the API boundary regardless of what the client sends.
        phone = (payload.get("phone") or "").strip() or None
        gst = (payload.get("gst") or "").strip() or None
        address = (payload.get("address") or "").strip() or None
        assert name and width > 0 and height > 0
        assert width_unit in ("mm", "cm", "in", "ft") and height_unit in ("mm", "cm", "in", "ft")
    except Exception:
        raise HTTPException(400, "invalid shop: need name, width>0, height>0, unit in mm/cm/in/ft")

    shop_id = uuid.uuid4().hex[:12]
    seq_no = len(db.list_shops(job_id)) + 1
    db.create_shop(shop_id, job_id, seq_no, name, width, width_unit, height, height_unit,
                    reference, reference_file_path, phone, gst, address)
    return db.get_shop(shop_id)


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
        raise HTTPException(404, "shop not found")
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
        (scene_dir / "scene.json").unlink(missing_ok=True)
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
        for p in (results_path, results_path.with_suffix(".heartbeat"), results_path.with_suffix(".done"),
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
    SVGs (v2), so the editor could only outline them or drew them as nothing; rebuild it once
    (boards without a PowerClip are untouched)."""
    if scene.get("version", 1) >= scene_export.SCENE_VERSION:
        return False
    return any(n.get("kind") == "powerclip" for n in scene_ops.iter_nodes(scene))


@app.get("/api/editor/{job_id}/{shop_id}/scene")
def editor_scene(job_id: str, shop_id: str, rebuild: bool = False, retry: bool = False):
    _editor_shop(job_id, shop_id)
    scene_path = _scene_dir(job_id, shop_id) / "scene.json"
    with _scene_lock:
        state = _scene_builds.get(shop_id)
        if state is not None and state["status"] == "building":
            return JSONResponse(_scene_progress(shop_id), status_code=202)
        if state is not None and state["status"] == "error" and not (retry or rebuild):
            raise HTTPException(503 if state.get("low_memory") else 500, state["error"])
        if scene_path.is_file() and not rebuild:
            scene = json.loads(scene_path.read_text(encoding="utf-8"))
            if _needs_powerclip_images(scene):
                _scene_builds[shop_id] = {"status": "building"}
                _pool.submit(_scene_build_worker, job_id, shop_id)
                return JSONResponse({"status": "building", "step": "queued", "progress_pct": 1}, status_code=202)
            scene["ops"] = db.get_editor_ops(shop_id)
            scene["asset_base"] = f"/api/editor/{job_id}/{shop_id}/asset/"
            return scene
        _scene_builds[shop_id] = {"status": "building"}
    _pool.submit(_scene_build_worker, job_id, shop_id)
    return JSONResponse({"status": "building", "step": "queued", "progress_pct": 1}, status_code=202)


_ASSET_TYPES = {".svg": "image/svg+xml", ".png": "image/png"}


@app.get("/api/editor/{job_id}/{shop_id}/asset/{filename}")
def editor_asset(job_id: str, shop_id: str, filename: str):
    _editor_shop(job_id, shop_id)
    scene_dir = _scene_dir(job_id, shop_id).resolve()
    p = (scene_dir / "page.png") if filename == "page.png" else (scene_dir / "img" / filename)
    p = p.resolve()
    if scene_dir not in p.parents or not p.is_file() or p.suffix not in _ASSET_TYPES:
        raise HTTPException(404)
    return FileResponse(p, media_type=_ASSET_TYPES[p.suffix], headers={"Cache-Control": "private, max-age=300"})


class EditorOps(BaseModel):
    ops: list[dict]


MAX_EDITOR_OPS = 5000


def _load_scene(job_id: str, shop_id: str) -> dict | None:
    p = _scene_dir(job_id, shop_id) / "scene.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


@app.get("/api/editor/{job_id}/{shop_id}/ops")
def editor_get_ops(job_id: str, shop_id: str):
    _editor_shop(job_id, shop_id)
    return {"ops": db.get_editor_ops(shop_id)}


@app.put("/api/editor/{job_id}/{shop_id}/ops")
def editor_put_ops(job_id: str, shop_id: str, body: EditorOps):
    _editor_shop(job_id, shop_id)
    if len(body.ops) > MAX_EDITOR_OPS:
        raise HTTPException(413, f"too many operations (max {MAX_EDITOR_OPS})")
    scene = _load_scene(job_id, shop_id)
    if scene is None:
        raise HTTPException(409, "scene has not been built yet")
    try:  # never persist a list that cannot be replayed
        scene_ops.apply_ops(scene, body.ops)
    except scene_ops.OpError as e:
        raise HTTPException(422, str(e))
    db.set_editor_ops(shop_id, body.ops)
    return {"saved": len(body.ops)}


@app.get("/api/editor/{job_id}/{shop_id}/replayed")
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


@app.post("/api/scene/convert-orientation")
def convert_orientation(body: ConvertOrientationRequest):
    """Re-lays a scene out for a different target page size (mm) - see orientation_adapter.py.

    Stateless and not tied to a job/shop id: the caller sends the scene JSON it already has (the
    editor's current, possibly-unsaved, in-memory scene - not necessarily what's on disk), gets back
    the transformed scene plus the op list that produced it, and merges those ops into its own
    undo/op timeline exactly like a locally-generated `resize`/`page` op - nothing here writes to
    disk or the `editor_ops` table itself."""
    try:
        ops = orientation_adapter.convert_orientation(body.scene, body.target_w, body.target_h)
        scene = scene_ops.apply_ops(body.scene, ops)
    except scene_ops.OpError as e:
        raise HTTPException(422, str(e))
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(422, f"malformed scene: {e}")
    return {"scene": scene, "ops": ops}


@app.get("/api/fonts")
def api_fonts(refresh: bool = False):
    """Installed font families (the editor refuses to name a font CorelDRAW would silently ignore)."""
    return fonts.installed_fonts(refresh)


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
    size = export_replay.resolve_raster(expected["page"]["width"], expected["page"]["height"], opts["raster"])
    scale = min(1.0, 1200 / max(size["w_px"], size["h_px"]))
    w, h = max(1, round(size["w_px"] * scale)), max(1, round(size["h_px"] * scale))

    def beat(step: str) -> None:
        heartbeat.write_text(json.dumps({"step": step}), encoding="utf-8")
        time.sleep(0.25)

    for step in ["launch", "open"]:
        beat(step)
    for fmt in export_replay.ALL_FORMATS:
        if fmt not in formats:
            continue
        beat(fmt)
        p = out_dir / f"{base}.{export_replay.EXTENSION[fmt]}"
        if fmt == "cdr":
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("mimetype", "application/x-cdr")
        else:
            im = Image.new("RGB" if fmt != "png" or opts["raster"]["png_background"] == "white" else "RGBA", (w, h),
                           (255, 255, 255) if fmt != "png" or opts["raster"]["png_background"] == "white" else (255, 255, 255, 0))
            d = ImageDraw.Draw(im)
            for layer in expected["layers"]:
                for n in layer["children"]:
                    x0, y0 = n["x"] / expected["page"]["width"] * w, h - (n["y"] + n["h"]) / expected["page"]["height"] * h
                    d.rectangle([x0, y0, x0 + n["w"] / expected["page"]["width"] * w, y0 + n["h"] / expected["page"]["height"] * h],
                                outline=(224, 24, 47))
            d.text((10, 10), f"MOCK EXPORT - not from CorelDRAW ({base})", fill=(0, 0, 0))
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
                "scene": str(_scene_dir(job_id, shop_id) / "scene.json"),
                "ops": ops, "formats": formats, "options": options,
                "out_dir": str(out_dir), "base_name": shop_row["name"],
            }
            entry = corel_supervisor.run_batch([{"export_replay": spec}], results_path)[0]
            if entry.get("status") != "done":
                raise RuntimeError(entry.get("error", "export failed"))
            report = entry["report"]
        else:
            scene = _load_scene(job_id, shop_id)
            report = _mock_export(scene_ops.apply_ops(scene, ops), formats, options, out_dir,
                                  export_replay.safe_name(shop_row["name"]), results_path.with_suffix(".heartbeat"))
        db.set_export_result(export_id, report["files"], report)
    except corel_supervisor.RefusedToStart as e:
        db.set_export_status(export_id, "failed", str(e))
    except Exception as e:
        db.set_export_status(export_id, "failed", str(e))
    finally:
        for p in (results_path, results_path.with_suffix(".heartbeat"), results_path.with_suffix(".done"),
                  results_path.with_suffix(".jobs.json"), results_path.with_suffix(".json.tmp"),
                  results_path.with_suffix(".heartbeat.tmp")):
            p.unlink(missing_ok=True)


@app.get("/api/editor/export-estimates")
def editor_export_estimates():
    return db.get_export_step_estimates()


@app.post("/api/editor/{job_id}/{shop_id}/export")
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
        size = (export_replay.resolve_raster(expected["page"]["width"], expected["page"]["height"], opts["raster"])
                if ("png" in formats or "jpeg" in formats) else None)
    except (export_replay.ExportOptionError, ValueError, TypeError) as e:
        raise HTTPException(422, str(e))
    if os.environ.get("SIGNAGE_ENGINE", "auto") != "mock" and get_engine(os.environ.get("SIGNAGE_ENGINE", "auto")).name == "corel":
        free = corel_util.check_memory()
        need = corel_supervisor.MIN_FREE_RAM_GB + (size["megapixels"] * 0.012 if size else 0)
        if free < need:
            raise HTTPException(503, f"not enough free RAM for this export: {free:.2f} GB free, about {need:.2f} GB needed"
                                     + (f" ({size['w_px']} x {size['h_px']} px images)" if size else "") + ". Close other programs or lower the image size.")
    plan = export_replay.plan_steps(formats, len(ops), db.get_export_step_estimates())
    export_id = uuid.uuid4().hex[:12]
    db.create_export(export_id, shop_id, formats, opts, ops, plan)
    _pool.submit(_export_worker, job_id, shop_id, export_id)
    return {"export_id": export_id, "plan": plan, "raster": size, "ops": len(ops)}


def _export_row(job_id: str, shop_id: str, export_id: str) -> dict:
    _editor_shop(job_id, shop_id)
    row = db.get_export(export_id)
    if not row or row["shop_id"] != shop_id:
        raise HTTPException(404, "export not found")
    return row


@app.get("/api/editor/{job_id}/{shop_id}/export/{export_id}")
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


@app.get("/api/editor/{job_id}/{shop_id}/exports")
def editor_export_list(job_id: str, shop_id: str):
    _editor_shop(job_id, shop_id)
    return [{"export_id": r["id"], "status": r["status"], "formats": json.loads(r["formats_json"]),
             "files": json.loads(r["files_json"]) if r["files_json"] else None,
             "error": r["error"], "created_at": r["created_at"], "completed_at": r["completed_at"]}
            for r in db.list_exports(shop_id)]


@app.get("/api/editor/{job_id}/{shop_id}/exports/{export_id}/files/{filename}")
def editor_export_file(job_id: str, shop_id: str, export_id: str, filename: str):
    _export_row(job_id, shop_id, export_id)
    base = _export_dir(job_id, shop_id, export_id).resolve()
    p = (base / filename).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)
