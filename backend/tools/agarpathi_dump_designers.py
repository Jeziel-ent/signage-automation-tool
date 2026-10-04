"""Read-only COM dump of every designer Agarpathi .cdr (+ masters) -> dataset_analysis/real_dumps_cache/agarpathi/<safe>.json"""
import sys, json
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import corel_supervisor
from tools.validate_all import DATASET, REAL_DUMPS_CACHE_ROOT, _safe

cache = REAL_DUMPS_CACHE_ROOT / "agarpathi"; cache.mkdir(parents=True, exist_ok=True)
files = sorted((DATASET / "Agarpathi").glob("*.cdr"))
jobs = []
for f in files:
    cp = cache / f"{_safe(f.stem)}.json"
    if not cp.exists():
        jobs.append({"dump_only": str(f), "dump_cache": str(cp)})
print(len(jobs), "to dump", flush=True)
def prog(i, e): print(i + 1, len(jobs), Path(e.get("dump_only", "?")).name[:50], e.get("status"), e.get("seconds"), e.get("error"), flush=True)
for k in range(0, len(jobs), 6):
    try:
        corel_supervisor.run_batch(jobs[k:k+6], ROOT / "dataset_analysis" / "agarpathi_fidelity" / "dump_results.json",
                                   overall_timeout_s=300, on_progress=prog)
    except corel_supervisor.RefusedToStart as e:
        print("REFUSED", e, flush=True)
