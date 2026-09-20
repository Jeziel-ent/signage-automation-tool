"""Read-only COM dump of already-generated "ours" .cdr files (from a prior
validate_all.py run), cached to dataset_analysis/ours_dumps_cache/<brand>/.

validate_all.py's worker only keeps ours_dump/real_dump in the ephemeral
worker_results.json for the jobs of its *last* run (resume runs overwrite
it), so after several --resume cycles the shape-level dumps for boards
validated in earlier cycles are gone even though the generated .cdr is
still on disk. This re-dumps those files (once, read-only, via the same
worker-subprocess/supervisor hang protection as everything else that talks
to CorelDRAW) so metrics.py's cluster-level comparison has shape data for
every board without re-running full generation.

Safe to re-run - skips files already cached; use --force to redo.

Usage:
    python cache_ours_dumps.py dalmia
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import corel_supervisor  # noqa: E402
from tools.validate_all import VALIDATION_ROOT  # noqa: E402

OURS_DUMPS_CACHE_ROOT = ROOT / "dataset_analysis" / "ours_dumps_cache"


def cache_ours_dumps(brand: str, force: bool = False, overall_timeout_s: float | None = None) -> dict[str, Path]:
    out_root = VALIDATION_ROOT / brand
    report = json.loads((out_root / "validation_report.json").read_text(encoding="utf-8"))
    cache_dir = OURS_DUMPS_CACHE_ROOT / brand
    cache_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    safes = []
    for b in report["boards"]:
        if "error" in b or "ours_png" not in b:
            continue
        safe = Path(b["ours_png"]).parent.name
        cache_path = cache_dir / f"{safe}.json"
        if cache_path.exists() and not force:
            continue
        cdr_candidates = sorted((out_root / "generated" / safe).glob("*.cdr"))
        cdr_candidates = [p for p in cdr_candidates if not p.stem.lower().startswith("backup_of")]
        if not cdr_candidates:
            print(f"skip {safe}: no generated .cdr found")
            continue
        jobs.append({"dump_only": str(cdr_candidates[0]), "dump_cache": str(cache_path)})
        safes.append(safe)

    if not jobs:
        print("nothing to do - all boards already cached (use --force to redo)")
        return {}

    def _on_progress(idx, entry):
        status = entry.get("status")
        print(f"[{idx + 1}/{len(jobs)}] {safes[idx]}: {status}" + (f" - {entry.get('error')}" if status == "error" else ""))

    try:
        corel_supervisor.run_batch(jobs, out_root / "ours_dump_worker_results.json",
                                    overall_timeout_s=overall_timeout_s, on_progress=_on_progress)
    except corel_supervisor.RefusedToStart as e:
        print(str(e))

    return {s: cache_dir / f"{s}.json" for s in safes}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brand")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout", type=float, default=None)
    args = ap.parse_args()
    cache_ours_dumps(args.brand, args.force, args.timeout)


if __name__ == "__main__":
    main()
