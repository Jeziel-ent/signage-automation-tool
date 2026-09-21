"""Agarpathi phase 3: run the existing (unmodified) rule engine against the
9 portrait/square Agarpathi boards, one CorelDRAW job at a time.

Unlike the phase-2 Family A batch, this doesn't need a separate structure
-probe pass first: `validate_all.py`'s own job already COM-dumps the real
designer file (cached under dataset_analysis/real_dumps_cache/agarpathi/)
as part of computing the geometric comparison, and that same dump gives us
everything needed for structural family classification (bitmap count, bg
shape type) and eyeballing how elements are rearranged for a portrait/square
target vs. the landscape master - see agarpathi_portrait_report.py, which
reads these cached dumps after this batch runs.

Read-only against signage_dataset/ (validate_all.py never writes there).
One real CorelDRAW job per validate_brand(only=...) call - never a
multi-job batch (see CLAUDE.md "Content check..." section on why that
fails under this machine's memory pressure) - checking free RAM against
the 1.5 GB floor before every single job, using a strict per-job timeout,
and relying on corel_util's existing recycle-instance-between-jobs pool
(SIGNAGE_COREL_RECYCLE_N=1 by default outside the worker subprocess - a
fresh instance every job unless overridden).

Usage:
    python tools/agarpathi_portrait_batch.py [--min-ram-gb 1.5] [--timeout 180] [--resume]
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
from tools.validate_all import VALIDATION_ROOT, validate_brand  # noqa: E402
import json  # noqa: E402

PORTRAIT_SQUARE_FILES = [
    "26 - 5 X 5 Feet - Nonlit - N N NATTU MARUNDHU KADAI.cdr",
    "27 - 3 X 4 Feet - Nonlit - AATHI SELVAM STORE.cdr",
    "37 - 2 X 6 Feet - Nonlit - THANGAM STORE.cdr",
    "41 - 4 X 8 Inch - Nonlit - NEW SELVI STORE.cdr",
    "44 - 34 X 45 Inch - Nonlit - VADIVAMBIGAI STORE.cdr",
    "49 - 36 X 41 Inch - Nonlit - NEW G. VASAN STORE.cdr",
    "67 - 6 X 6 Feet - Nonlit - KALKEE POOJA STORES.cdr",
    "73 - 60 X 75 Inch - Nonlit - SRI AMBIRAMI PROVISON STORES.cdr",
    "76 - 36 X 48 Inch - Nonlit - SRI KANNIYAMMAN NATTU MARUNTHU KADAI.cdr",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-ram-gb", type=float, default=1.5)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    print(f"{len(PORTRAIT_SQUARE_FILES)} portrait/square boards in scope")

    report_path = VALIDATION_ROOT / "agarpathi" / "validation_report.json"
    already_done = set()
    if args.resume and report_path.exists():
        rpt = json.loads(report_path.read_text(encoding="utf-8"))
        already_done = {b["file"] for b in rpt["boards"] if "error" not in b}

    for i, fname in enumerate(PORTRAIT_SQUARE_FILES):
        if fname in already_done:
            print(f"[{i + 1}/{len(PORTRAIT_SQUARE_FILES)}] {fname}: already done, skipping")
            continue
        corel_util.cleanup_orphaned_instances()
        free = corel_util.check_memory()
        if free < args.min_ram_gb:
            print(f"[{i + 1}/{len(PORTRAIT_SQUARE_FILES)}] {fname}: REFUSED - free RAM {free:.2f} GB "
                  f"below the {args.min_ram_gb:.1f} GB floor; stopping.")
            return
        # The full stem (not just the numeric prefix) - '76 - 36 X 48 Inch...'
        # shares its number with the already-processed '76 - 125 X 48 Inch...'
        # from the phase-2 Family A batch, so a bare "76 - " substring would
        # ambiguously match both files in validate_all.py's directory scan.
        substr = fname.rsplit(".cdr", 1)[0]
        print(f"[{i + 1}/{len(PORTRAIT_SQUARE_FILES)}] {fname}: free RAM {free:.2f} GB, running...")
        validate_brand("agarpathi", only=substr, resume=True, overall_timeout_s=args.timeout)

    print("done")


if __name__ == "__main__":
    main()
