"""Refresh the "ours" shape-dump cache for the 4 real wide/tiled dalmia
boards after backend/app/layout.py's panel_sequence cx_frac fix (their
previously-cached dumps reflect the OLD, pre-fix placement), one CorelDRAW
job at a time - never the multi-job batch cache_ours_dumps.py itself would
build (see CLAUDE.md "Content check..." on why batching several real jobs
in one worker-subprocess invocation is unreliable on this machine's
memory-constrained state; this script deliberately calls
corel_supervisor.run_batch with exactly one job per invocation instead).

Real designer files' renders (dataset_analysis/compare/*/real.png) are
untouched - ground truth doesn't change when our engine does, so there is
nothing to re-cache there for these boards specifically; the script still
runs cache_real_renders.py's own no-op-if-already-cached check as a cheap
sanity pass (it never launches CorelDRAW when nothing is missing).

Read-only against signage_dataset/. RAM-gated at 1.5 GB before every job.

Usage:
    python tools/dalmia_wide_recache.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import corel_supervisor, corel_util  # noqa: E402
from tools.validate_all import VALIDATION_ROOT  # noqa: E402
from tools.cache_real_renders import cache_real_renders  # noqa: E402

WIDE_BOARDS = [
    "02 - 180 X 48 Inch - GSB - SRI KAVI STEELS.cdr",
    "06 - 180 X 60 Inch - 2 Nos Double Side GSB - SEETHARAMAN TRADERS.cdr",
    "11 - 216 X 48 Inch - GSB - AHMED TRADERS.cdr",
    "11 - 240 X 60 Inch - 2 Nos Double Side GSB - AHMED TRADERS.cdr",
]

OURS_DUMPS_CACHE_ROOT = ROOT / "dataset_analysis" / "ours_dumps_cache" / "dalmia"


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "x"


def main(min_ram_gb: float = 1.5, timeout: float = 180.0) -> None:
    out_root = VALIDATION_ROOT / "dalmia"
    report = json.loads((out_root / "validation_report.json").read_text(encoding="utf-8"))
    by_file = {b["file"]: b for b in report["boards"]}

    OURS_DUMPS_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    for i, fname in enumerate(WIDE_BOARDS):
        b = by_file.get(fname)
        if b is None or "error" in b:
            print(f"[{i + 1}/{len(WIDE_BOARDS)}] {fname}: SKIP - not found or errored in validation_report.json")
            continue
        safe = _safe(fname.rsplit(".cdr", 1)[0])
        cdr_candidates = sorted((out_root / "generated" / safe).glob("*.cdr"))
        cdr_candidates = [p for p in cdr_candidates if not p.stem.lower().startswith("backup_of")]
        if not cdr_candidates:
            print(f"[{i + 1}/{len(WIDE_BOARDS)}] {fname}: SKIP - no generated .cdr on disk")
            continue

        corel_util.cleanup_orphaned_instances()
        free = corel_util.check_memory()
        if free < min_ram_gb:
            print(f"[{i + 1}/{len(WIDE_BOARDS)}] {fname}: REFUSED - free RAM {free:.2f} GB "
                  f"below the {min_ram_gb:.1f} GB floor; stopping.")
            return

        cache_path = OURS_DUMPS_CACHE_ROOT / f"{safe}.json"
        print(f"[{i + 1}/{len(WIDE_BOARDS)}] {fname}: free RAM {free:.2f} GB, dumping...")
        results_path = out_root / f"{safe}.wide_recache_results.json"
        try:
            results = corel_supervisor.run_batch(
                [{"dump_only": str(cdr_candidates[0]), "dump_cache": str(cache_path)}],
                results_path, overall_timeout_s=timeout,
            )
            entry = results[0]
            if entry.get("status") == "done":
                print(f"    -> cached {cache_path.name}")
            else:
                print(f"    ERROR: {entry.get('error')}")
        finally:
            for p in (results_path, results_path.with_suffix(".heartbeat"), results_path.with_suffix(".done")):
                p.unlink(missing_ok=True)

    print("\nre-checking real designer renders (no-op if all already cached)...")
    cache_real_renders("dalmia")


if __name__ == "__main__":
    main()
