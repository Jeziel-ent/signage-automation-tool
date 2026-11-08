"""The website's CorelDRAW engine code (backend/app: layout rules, CorelEngine, corel_supervisor, corel_worker) as the agent uses it.

From source it is imported from the repository's backend/ folder; inside SignageAgent.exe it is bundled (agent/build.py). Imports are lazy,
so the agent starts and heartbeats even on a PC where the engine cannot load; a job then fails with the reason instead.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def prepare(data_dir: Path) -> None:
    """Point the engine at the agent's own data folder (CorelDRAW process tracking lives there) and make `app` importable."""
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SIGNAGE_DATA", str(data_dir))
    if not getattr(sys, "frozen", False):
        backend = Path(__file__).resolve().parents[2] / "backend"
        if backend.is_dir() and str(backend) not in sys.path:
            sys.path.insert(0, str(backend))


def mock_mode() -> bool:
    """SIGNAGE_ENGINE=mock on the agent (tests and demos): jobs are 'converted' by the mock engine, CorelDRAW is not needed."""
    return os.environ.get("SIGNAGE_ENGINE", "").strip().lower() == "mock"


def run_batch(jobs: list[dict], results_path: Path, on_progress=None) -> list[dict]:
    """Run a batch of convert jobs through the real worker (one hidden CorelDRAW instance, hang watchdog, RAM floor). Same limits as the
    website's own local conversions."""
    from app import corel_supervisor

    limit = corel_supervisor.shop_timeout_s()
    limits = {"job_timeout_s": limit, "overall_timeout_s": max(120.0, 2 * limit)} if limit > 0 else {}
    return corel_supervisor.run_batch(jobs, results_path, on_progress=on_progress, **limits)


def run_mock(job: dict) -> dict:
    """The mock engine for one job; returns the same {files, report} a real conversion result carries."""
    from app.engines import MockEngine

    return MockEngine().process(Path(job["master_path"]), job["shop"], Path(job["out_dir"]))


def run_spec(job_spec: dict, results_path: Path, on_progress=None) -> dict:
    """One scene-export / export-replay job through the real worker. Unlike conversions these have no per-shop time limit (the worker's own
    no-progress watchdog applies) - the same as when the website ran them itself. Returns the worker's result entry."""
    from app import corel_supervisor

    return corel_supervisor.run_batch([job_spec], results_path, on_progress=on_progress)[0]


def mock_scene(scene_dir: Path, width_mm: float, height_mm: float) -> None:
    from app import scene_export

    scene_export.mock_scene(scene_dir, width_mm, height_mm)


def mock_export(scene_path: Path, spec: dict, out_dir: Path, heartbeat: Path) -> dict:
    """The mock engine's export of a scene with the edits applied (placeholder files, flagged in the report)."""
    import json

    from app import export_replay, scene_ops

    scene = json.loads(Path(scene_path).read_text(encoding="utf-8"))
    expected = scene_ops.apply_ops(scene, spec.get("ops") or [])
    return export_replay.mock_export(expected, spec["formats"], spec["options"], out_dir, spec["base_name"], heartbeat)


def selftest() -> dict:
    """What the bundled engine can see: imports, brand rules, example libraries, confidence bounds. `SignageAgent.exe --selftest` writes
    this to selftest.json in the agent's folder (the exe has no console), so a PC where jobs behave oddly can be checked in a second."""
    out: dict = {"frozen": bool(getattr(sys, "frozen", False)), "imports": {}, "brands": {}}
    for mod in ("app.layout", "app.engines", "app.corel_supervisor", "app.corel_worker", "app.example_layout", "app.confidence"):
        try:
            __import__(mod)
            out["imports"][mod] = "ok"
        except Exception as e:  # noqa: BLE001
            out["imports"][mod] = f"{type(e).__name__}: {e}"
    try:
        from app import confidence, example_layout, layout

        out["confidence_bounds"] = confidence.BOUNDS_PATH.exists()
        for brand in ("agarpathi", "dalmia", "hangyo", "agni"):
            rule = layout.load_brand_rule(brand)
            lib = example_layout.library(brand)
            out["brands"][brand] = {"rule": rule is not None, "library": bool(lib), "boards": len((lib or {}).get("boards", []))}
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def heartbeat_path(results_path: Path) -> Path:
    return results_path.with_suffix(".heartbeat")
