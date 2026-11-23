"""Phase 4: the lifecycle of a master's spatial model - request, claim, store, validate, reuse.

The model is analysed ONCE per master version and reused for every shop size:
- identity of a version = the master file's SHA-256 (`jobs.master_hash`, recorded at upload) + `master_analysis.EXTRACTOR_VERSION` +
  `master_model.SCHEMA_VERSION`;
- state lives in the `master_models` table (db.py), the model itself in `<master folder>/spatial_model.json` (+ `master_page.png`, the
  render the background analysis used);
- a completed row whose hash or versions differ from the master's current ones is `needs_reanalysis` and is NEVER used by a conversion.

Nothing here calls CorelDRAW: running the analysis is main.py's job (local supervisor or the owner's Windows agent); this module stores
what they produce (`store_result`) and hands conversions a validated model (`usable_model`) or None - None always means "use the
legacy layout path".
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from . import background_analysis, db, master_analysis, master_model

log = logging.getLogger("signage.master_store")

EXTRACTOR_VERSION = master_analysis.EXTRACTOR_VERSION
SCHEMA_VERSION = master_model.SCHEMA_VERSION
MODEL_FILE = "spatial_model.json"
RENDER_FILE = "master_page.png"
INTERRUPTED = "Interrupted: the server restarted while the master was being analysed - it is analysed again on demand."

_cache: dict[str, tuple[float, master_model.MasterModel]] = {}      # job_id -> (file mtime, model)
_cache_lock = threading.Lock()


def enabled(engine_kind: str | None = None) -> bool:
    """Is the analysis run at all? SIGNAGE_MASTER_ANALYSIS=1/0 decides. Unset: on only when the spatial planner is on (nothing else
    consumes the model) AND the engine is a real one (corel / remote) - the mock engine never analyses on its own. Default off keeps every
    existing deployment exactly as it was: no extra CorelDRAW / agent job on an upload."""
    v = os.environ.get("SIGNAGE_MASTER_ANALYSIS", "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    kind = (engine_kind or os.environ.get("SIGNAGE_ENGINE", "auto")).strip().lower()
    return planner_enabled() and (kind in ("corel", "remote") or (kind == "auto" and os.name == "nt"))


def planner_enabled() -> bool:
    """The target planner is behind SIGNAGE_SPATIAL_PLANNER=1 (default off)."""
    return os.environ.get("SIGNAGE_SPATIAL_PLANNER", "").strip().lower() in ("1", "true", "yes", "on")


def model_dir(job_row: dict) -> Path:
    return Path(job_row["master_path"]).parent


def model_path(job_row: dict) -> Path:
    return model_dir(job_row) / MODEL_FILE


def render_path(job_row: dict) -> Path:
    return model_dir(job_row) / RENDER_FILE


def status_for(job_row: dict | None, admin: bool = False) -> dict:
    """Public analysis state of a master: {status, ...}. status is one of pending | analysing | completed | failed | needs_reanalysis |
    disabled. Ordinary designers get no technical error text (it can name COM members and file paths); `admin=True` adds it."""
    if not job_row:
        return {"status": "disabled"}
    row = db.get_master_model(job_row["id"])
    if row is None:
        return {"status": "pending" if enabled() else "disabled"}
    status = row["status"]
    current = (row["master_hash"] == job_row.get("master_hash") and row["extractor_version"] == EXTRACTOR_VERSION
               and row["schema_version"] == SCHEMA_VERSION)
    if status == "completed" and not current:
        status = "needs_reanalysis"
    out = {"status": status, "analysed_at": row["updated_at"] if status == "completed" else None}
    if admin:
        out.update(error=row.get("error"), extractor_version=row["extractor_version"], schema_version=row["schema_version"],
                   corel_version=row.get("corel_version"), master_hash=row["master_hash"], model_path=row.get("model_path"))
    return out


def should_analyse(job_row: dict, force: bool = False) -> bool:
    """True when the master has no usable, current analysis and none is running."""
    st = status_for(job_row)["status"]
    if force:
        return st != "analysing"
    return st in ("pending", "failed", "needs_reanalysis")


AUTO_RETRY_COOLDOWN_S = 600.0


def may_auto_retry(job_row: dict) -> bool:
    """A conversion asks for a missing analysis only when the last attempt did not fail within the last 10 minutes - a master that cannot be
    analysed (corrupt file, low RAM) must not queue a new CorelDRAW / agent job for every board converted from it. An admin can force one."""
    row = db.get_master_model(job_row["id"])
    return not (row and row["status"] == "failed" and time.time() - (row["updated_at"] or 0) < AUTO_RETRY_COOLDOWN_S)


def begin(job_row: dict, force: bool = False) -> bool:
    """Claim the analysis of this master version (atomic). False = someone else is on it, or a current result exists."""
    return db.claim_master_analysis(job_row["id"], job_row.get("master_hash"), EXTRACTOR_VERSION, SCHEMA_VERSION, force=force)


def store_result(job_row: dict, extraction: dict, render_png: Path | None = None) -> master_model.MasterModel:
    """Build the model (+ render-based background analysis when a page render is given), write it atomically, mark the row completed.
    Raises on an unusable extraction - the caller records the failure with `fail`."""
    model = master_model.build_model(extraction, master_hash=job_row.get("master_hash"))
    if render_png is not None and Path(render_png).is_file():
        try:
            bg = background_analysis.analyze_background(Path(render_png), model.to_dict())
            master_model.attach_background(model, bg)
        except Exception as e:  # noqa: BLE001 - the geometry model is still valuable without the render analysis
            model.warnings.append(f"background analysis failed: {type(e).__name__}")
            log.warning("background analysis of master %s failed: %s", job_row["id"], e)
    elif render_png is not None:
        model.warnings.append("the page render is missing: no background analysis")
    path = model_path(job_row)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(model.to_dict(), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    db.finish_master_analysis(job_row["id"], True, str(path), extraction.get("corel_version"))
    with _cache_lock:
        _cache.pop(job_row["id"], None)
    return model


def fail(job_id: str, error: str) -> None:
    db.finish_master_analysis(job_id, False, error=error)
    with _cache_lock:
        _cache.pop(job_id, None)


def load_model(job_row: dict) -> master_model.MasterModel | None:
    """The stored model of this master, validated; None when missing / unreadable / invalid (callers fall back to the legacy layout)."""
    path = model_path(job_row)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _cache_lock:
        hit = _cache.get(job_row["id"])
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        model = master_model.MasterModel.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001
        log.warning("spatial model of master %s is unusable: %s", job_row["id"], e)
        return None
    with _cache_lock:
        _cache[job_row["id"]] = (mtime, model)
    return model


def usable_model(job_row: dict | None) -> master_model.MasterModel | None:
    """A model a conversion may use: analysis completed AND current for this exact master file and extractor / schema version."""
    if not job_row:
        return None
    if status_for(job_row)["status"] != "completed":
        return None
    model = load_model(job_row)
    if model is None or model.master_hash != job_row.get("master_hash"):
        return None
    return model


def recover_interrupted() -> int:
    n = db.fail_interrupted_master_analyses(INTERRUPTED)
    if n:
        log.info("startup: %d interrupted master analysis row(s) marked failed", n)
    return n


# --------------------------------------------------------------------------------------------- mock engine (no CorelDRAW)


def mock_extraction(page_w: float = 3000.0, page_h: float = 1000.0) -> dict:
    """A small deterministic extraction (the MockEngine's demo scene) so the whole lifecycle can run without CorelDRAW. Clearly NOT a real
    analysis of the uploaded file: its `corel_version` says so."""
    def o(i, name, type_, x, y, w, h, **kw):
        d = {"id": str(i), "scene_id": f"s{i}", "parent": None, "children": [], "z": i, "layer": None, "name": name, "type": type_,
             "kind": "group" if type_ == "group" else "shape", "x": x, "y": y, "w": w, "h": h, "rotation": 0.0, "visible": True, "locked": False}
        d.update(kw)
        return d
    sx, sy = page_w / 3000.0, page_h / 1000.0
    objs = [o(0, "bg_wall", "rectangle", 0, 0, page_w, page_h, fill={"type": "uniform", "type_code": 1, "color": {"cmyk": [95, 80, 4, 0]}}),
            o(1, "logo_main", "group", 150 * sx, 300 * sy, 700 * sx, 400 * sy),
            o(2, "Shop name", "text", 1000 * sx, 400 * sy, 1500 * sx, 200 * sy,
              text={"kind": "artistic", "content": "SHOP NAME", "font": "Arial", "size_pt": 120.0}),
            o(3, "fixed_phone", "text", 2400 * sx, 80 * sy, 500 * sx, 60 * sy,
              text={"kind": "artistic", "content": "Phone No. 000", "font": "Arial", "size_pt": 40.0})]
    return {"extractor_version": EXTRACTOR_VERSION, "corel_version": "mock", "page": {"width_mm": page_w, "height_mm": page_h, "unit": "mm",
            "origin": "bottom-left"}, "layers": [], "shape_sets": {"equal": True}, "objects": objs, "truncated": False, "errors": {}, "seconds": 0.0}
