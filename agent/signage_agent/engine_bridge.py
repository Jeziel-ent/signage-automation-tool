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


def heartbeat_path(results_path: Path) -> Path:
    return results_path.with_suffix(".heartbeat")
