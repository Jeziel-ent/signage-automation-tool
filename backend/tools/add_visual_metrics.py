"""Second pass over a validate_all.py report: render each board's real file
to PNG and score SSIM against the already-generated "ours" PNG.

Separate from validate_all.py's main loop on purpose - adding this as a 4th
COM session per board (on top of generate + 2 dumps) pushed this machine
into sustained <2GB-free territory and made CorelDRAW launches fail outright
for the rest of a batch (see CLAUDE.md). Running it as its own pass, after
the geometry comparison is safely on disk, means a memory-pressure failure
here only costs the visual-metrics pass, not the whole validation.

Updates validation_report.json's `ssim` field in place (and rewrites the
.md) after every board, same incremental-progress reasoning as
validate_all.py. Safe to re-run - only fills boards where `ssim` is still
None; use --force to redo all of them.

Usage:
    python add_visual_metrics.py dalmia [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.image_compare import ssim_between_files  # noqa: E402
from tools.render_real_preview import render as render_real_png  # noqa: E402
from tools.validate_all import DATASET, VALIDATION_ROOT, _render_md  # noqa: E402

RETRY_SLEEP_S = 5
RETRIES = 3


def add_visual_metrics(brand: str, force: bool = False) -> dict:
    out_root = VALIDATION_ROOT / brand
    report_path = out_root / "validation_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    brand_dir = DATASET / ("Agarpathi" if brand == "agarpathi" else brand)

    for b in report["boards"]:
        if "error" in b or "real_png" not in b:
            continue
        if b.get("ssim") is not None and not force:
            continue

        real_file = brand_dir / b["file"]
        real_png = ROOT / b["real_png"]
        ours_png = ROOT / b["ours_png"]
        print(f"--- {b['file']} ---")

        last_err = None
        for attempt in range(RETRIES):
            try:
                time.sleep(3)
                render_real_png(real_file, real_png)
                b["ssim"] = ssim_between_files(ours_png, real_png)
                print(f"  ssim={b['ssim']:.3f}")
                last_err = None
                break
            except Exception as e:
                last_err = e
                print(f"  attempt {attempt + 1} failed: {e}; retrying")
                time.sleep(RETRY_SLEEP_S)
        if last_err is not None:
            print(f"  giving up on this board's SSIM: {last_err}")
            b["ssim_error"] = str(last_err)

        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (out_root / "validation_report.md").write_text(_render_md(report), encoding="utf-8")
        time.sleep(2)

    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brand")
    ap.add_argument("--force", action="store_true", help="recompute SSIM even where already set")
    args = ap.parse_args()
    add_visual_metrics(args.brand, args.force)


if __name__ == "__main__":
    main()
