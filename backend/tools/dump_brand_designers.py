"""Read-only COM dump (with PowerClip contents) of every .cdr under given folders -> dataset_analysis/real_dumps_cache/<brand>/<safe>.json
    python tools/dump_brand_designers.py <brand> <folder> [<folder> ...]"""
import os, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["SIGNAGE_DUMP_CLIPS"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import corel_supervisor
from tools.validate_all import REAL_DUMPS_CACHE_ROOT, _safe

brand = sys.argv[1]
cache = REAL_DUMPS_CACHE_ROOT / brand
cache.mkdir(parents=True, exist_ok=True)
jobs = []
for folder in sys.argv[2:]:
    for f in sorted(Path(folder).glob("*.cdr")):
        if f.stem.lower().startswith("backup_of"):
            continue
        cp = cache / f"{_safe(f.stem)}.json"
        if not cp.exists():
            jobs.append({"dump_only": str(f), "dump_cache": str(cp)})
print(len(jobs), "to dump", flush=True)
def prog(i, e): print(Path(e.get("dump_only", "?")).name[:60], e.get("status"), e.get("seconds"), e.get("error"), flush=True)
for k in range(0, len(jobs), 5):
    try:
        corel_supervisor.run_batch(jobs[k:k + 5], cache / "_dump_results.json", overall_timeout_s=400, on_progress=prog)
    except corel_supervisor.RefusedToStart as e:
        print("REFUSED", e, flush=True)
