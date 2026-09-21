"""Agarpathi phase 2, step 3: run the existing (unmodified) rule engine
against every Family A landscape board that the real tiling decision
(app.layout._tile_plan) says would NOT tile - i.e. every board the plain
per-object scale+centre path is meant to handle, staying inside the scope
already agreed for this phase (no tiling/portrait/square work).

Read-only against signage_dataset/ (validate_all.py never writes there -
all generated output goes under backend/dataset_analysis/). One real
CorelDRAW job per `validate_all.validate_brand(..., only=...)` call - never
a multi-job batch in one worker subprocess (see CLAUDE.md "Content check..."
section on why that fails under this machine's memory pressure) - checking
free RAM against `--min-ram-gb` (default 1.5, matching corel_supervisor's
own batch floor) before every single job, and passing a strict per-job
`--timeout` through to validate_all's own overall_timeout_s (corel_supervisor
kills the whole worker process, CorelDRAW included, if a job makes no
heartbeat progress for that long).

Usage:
    python tools/agarpathi_family_a_batch.py [--min-ram-gb 1.5] [--timeout 180] [--resume]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import corel_util  # noqa: E402
from app.batch_import import parse_shop_lines  # noqa: E402
from app.layout import _tile_plan, to_mm  # noqa: E402
from tools.validate_all import BRAND_MASTER, DATASET, VALIDATION_ROOT, validate_brand  # noqa: E402
import json  # noqa: E402

MASTER_MM = (3657.6, 1219.2)  # agarpathi master (16 - 12 X 4 Feet), see BRAND_MASTER


def family_a_no_tile_files() -> list[str]:
    probe_path = ROOT / "dataset_analysis" / "inventory" / "agarpathi_phase2" / "structure_probe.json"
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    files = {BRAND_MASTER["agarpathi"]["file"],
             "48 - 96 X 36 Inch - Nonlit - OM ARULS NATTU MARUNDHU KADAI.cdr",
             "72 - 12 X 4 Feet - Nonlit - SAI TREDSES STORES.cdr"}
    files |= {e["file"] for e in probe if e.get("family") == "A"}

    no_tile = []
    for f in sorted(files):
        stem = f.rsplit(".cdr", 1)[0]
        parsed = parse_shop_lines(stem)
        if not parsed.shops:
            continue
        s = parsed.shops[0]
        w_mm, h_mm = to_mm(s.width, s.unit), to_mm(s.height, s.unit)
        axis, _n = _tile_plan(MASTER_MM[0], MASTER_MM[1], w_mm, h_mm)
        if axis is None:
            no_tile.append(f)
    return no_tile


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-ram-gb", type=float, default=1.5)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    files = family_a_no_tile_files()
    print(f"{len(files)} Family A, non-tiling boards in scope")

    report_path = VALIDATION_ROOT / "agarpathi" / "validation_report.json"
    already_done = set()
    if args.resume and report_path.exists():
        rpt = json.loads(report_path.read_text(encoding="utf-8"))
        already_done = {b["file"] for b in rpt["boards"] if "error" not in b}

    for i, fname in enumerate(files):
        if fname in already_done:
            continue
        free = corel_util.check_memory()
        if free < args.min_ram_gb:
            print(f"[{i + 1}/{len(files)}] {fname}: REFUSED - free RAM {free:.2f} GB "
                  f"below the {args.min_ram_gb:.1f} GB floor; stopping.")
            return
        # substring must uniquely match this file's numeric prefix - verified
        # unique across the whole agarpathi dataset before this tool was written
        substr = fname.split(" - ", 1)[0] + " - "
        print(f"[{i + 1}/{len(files)}] {fname}: free RAM {free:.2f} GB, running...")
        validate_brand("agarpathi", only=substr, resume=True, overall_timeout_s=args.timeout)

    print("done")


if __name__ == "__main__":
    main()
