"""Render every real designer file referenced in a validate_all.py report to
PNG (read-only, via the subprocess-isolated worker/supervisor - same hang
protection as everything else that talks to CorelDRAW), so metrics.py can
compare "ours" vs "real" images without a second, separate COM tool per
board. Skips renders that already exist on disk; use --force to redo.

Usage:
    python cache_real_renders.py dalmia
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import corel_supervisor  # noqa: E402
from tools.validate_all import DATASET, VALIDATION_ROOT  # noqa: E402
import json  # noqa: E402


def cache_real_renders(brand: str, force: bool = False, overall_timeout_s: float | None = None) -> None:
    out_root = VALIDATION_ROOT / brand
    report = json.loads((out_root / "validation_report.json").read_text(encoding="utf-8"))
    brand_dir = DATASET / ("Agarpathi" if brand == "agarpathi" else brand)

    jobs, labels = [], []
    for b in report["boards"]:
        if "error" in b or "real_png" not in b:
            continue
        real_png = ROOT / b["real_png"]
        if real_png.exists() and not force:
            continue
        real_file = brand_dir / b["file"]
        real_png.parent.mkdir(parents=True, exist_ok=True)
        jobs.append({"render_only": str(real_file), "render_out": str(real_png)})
        labels.append(b["file"])

    if not jobs:
        print("nothing to do - all real renders already exist (use --force to redo)")
        return

    def _on_progress(idx, entry):
        status = entry.get("status")
        print(f"[{idx + 1}/{len(jobs)}] {labels[idx]}: {status}" + (f" - {entry.get('error')}" if status == "error" else ""))

    try:
        corel_supervisor.run_batch(jobs, out_root / "real_render_worker_results.json",
                                    overall_timeout_s=overall_timeout_s, on_progress=_on_progress)
    except corel_supervisor.RefusedToStart as e:
        print(str(e))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brand")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout", type=float, default=None)
    args = ap.parse_args()
    cache_real_renders(args.brand, args.force, args.timeout)


if __name__ == "__main__":
    main()
