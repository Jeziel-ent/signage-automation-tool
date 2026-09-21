"""Agarpathi phase 2, step 2: batch structure probe over Agarpathi's
landscape boards, classifying each into a structural family based on real
COM dumps - never guessed from filenames or file size.

Phase 1 (see CLAUDE.md-adjacent session notes / PHASE_COMPLETION.md history)
hand-dumped the master + 4 in-range boards and found two distinct real
structures sharing the same 3 logo groups:
  - Family A ("single-bitmap master compatible"): 1 embedded product-photo
    bitmap, a `rectangle` full-page background, shape_count ~41.
  - Family B ("multi-bitmap composition"): 3 embedded bitmaps, a `curve`
    full-page background, shape_count ~43.
This script extends that classification to the rest of the landscape set,
one file at a time (never a multi-job batch - see CLAUDE.md "Content
check..." section: batching multiple real jobs in one worker-subprocess
invocation on this machine's memory-constrained state has an observed
near-100% failure rate for jobs 2+; one-file-per-subprocess reliably
succeeds instead).

Read-only against signage_dataset/ - dump_objects.dump() never writes to
the source file; every output goes under backend/dataset_analysis/. Skips
"Copy" duplicate filenames, same convention as validate_all.py.

RAM protocol: checks free RAM against --min-ram-gb (default 1.5, the same
floor corel_supervisor.run_batch enforces for a real batch) before EVERY
file; stops cleanly (does not raise) and reports exactly where it stopped
if free RAM ever drops below the floor, so a re-run with --resume can pick
up where it left off.

Usage:
    python tools/agarpathi_structure_probe.py [--min-ram-gb 1.5] [--resume] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import corel_supervisor, corel_util  # noqa: E402
from tools.dataset_inventory import _safe_name  # noqa: E402

DATASET = ROOT.parent / "signage_dataset" / "Agarpathi"
OUT_ROOT = ROOT / "dataset_analysis" / "inventory" / "agarpathi_phase2"
INVENTORY_JSON = ROOT / "dataset_analysis" / "inventory" / "agarpathi_inventory.json"
REPORT_PATH = OUT_ROOT / "structure_probe.json"

# Already hand-verified in phase 1 (dumped and reasoned about by eye) -
# never re-dumped here.
ALREADY_PROBED = {
    "16 - 12 X 4 Feet - Nonlit - AL MADEENA POOJA STORE.cdr": "A",  # master
    "48 - 96 X 36 Inch - Nonlit - OM ARULS NATTU MARUNDHU KADAI.cdr": "A",
    "72 - 12 X 4 Feet - Nonlit - SAI TREDSES STORES.cdr": "A",
    "20 - 9 X 3 Feet - Nonlit - VMUK NATTU MARUNTHU KADAI.cdr": "B",
    "65 - 6 X 2 Feet - Nonlit - BASKAR NATTU MARUNDHU KADAI.cdr": "B",
}


def classify(dump: dict) -> dict:
    shapes = dump["shapes"]
    counts: dict[str, int] = {}
    for s in shapes:
        counts[s["type"]] = counts.get(s["type"], 0) + 1
    bitmap_n = counts.get("bitmap", 0)
    page_w, page_h = dump["page_mm"]["w"], dump["page_mm"]["h"]
    page_area = page_w * page_h
    bg_type = None
    for s in shapes:
        if not s["group_path"] and s["w"] * s["h"] >= 0.9 * page_area:
            bg_type = s["type"]
            break

    # bg_type (rectangle vs. curve) is cosmetic - a full-page background drawn
    # as a freeform curve is functionally identical to one drawn as a
    # rectangle primitive for our purposes (layout.detect_role's >=90%-page
    # -area heuristic doesn't care about literal shape type either). Found by
    # inspecting the phase-2 "unclassified" boards: 5 of 9 were exactly the
    # single-bitmap family with a curve bg (21, 35, 40, 43, 47), and 3 more
    # were the multi-bitmap family with a rectangle bg (25, 28, 42) - neither
    # is a real third structure, both were an over-strict classifier. Family
    # membership is decided by bitmap count alone; bg_type is kept in the
    # report as informational metadata only, not a classification input.
    if bitmap_n == 1:
        family = "A"
    elif bitmap_n >= 2:
        family = "B"
    else:
        family = "unclassified"  # bitmap_n == 0: a genuinely different composition, not a bg-type artifact

    return {
        "shape_count": dump["shape_count"], "counts_by_type": counts,
        "bitmap_count": bitmap_n, "bg_type": bg_type, "family": family,
    }


def probe_one(cdr_path: Path, min_ram_gb: float) -> dict:
    free = corel_util.check_memory()
    if free < min_ram_gb:
        return {"file": cdr_path.name, "status": "refused",
                "reason": f"free RAM {free:.2f} GB is below the {min_ram_gb:.1f} GB floor"}

    safe = _safe_name(cdr_path.stem)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    dump_cache = OUT_ROOT / f"{safe}.json"
    results_path = OUT_ROOT / f"{safe}.worker_results.json"

    t0 = time.time()
    try:
        results = corel_supervisor.run_batch(
            [{"dump_only": str(cdr_path), "dump_cache": str(dump_cache)}], results_path,
        )
    finally:
        for p in (results_path, results_path.with_suffix(".heartbeat"), results_path.with_suffix(".done"),
                  results_path.with_suffix(".jobs.json")):
            p.unlink(missing_ok=True)
    elapsed = time.time() - t0

    entry = results[0]
    if entry.get("status") != "done":
        return {"file": cdr_path.name, "status": "error", "seconds": round(elapsed, 1),
                "error": entry.get("error"), "free_ram_before_gb": round(free, 2)}

    info = classify(entry["dump"])
    return {"file": cdr_path.name, "status": "done", "seconds": round(elapsed, 1),
            "free_ram_before_gb": round(free, 2), **info}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-ram-gb", type=float, default=1.5)
    ap.add_argument("--resume", action="store_true", help="skip files already recorded in structure_probe.json")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    inv = json.loads(INVENTORY_JSON.read_text(encoding="utf-8"))
    files = [r["file"] for r in inv["rows"]
             if r["orientation"] == "landscape" and "copy" not in r["file"].lower()
             and r["file"] not in ALREADY_PROBED]

    results: list[dict] = []
    if REPORT_PATH.exists():
        results = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        if args.resume:
            done = {r["file"] for r in results if r.get("status") == "done"}
            files = [f for f in files if f not in done]
            print(f"resume: {len(done)} already done, {len(files)} remaining")
        else:
            results = []

    if args.limit:
        files = files[: args.limit]

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    for i, fname in enumerate(files):
        cdr_path = DATASET / fname
        r = probe_one(cdr_path, args.min_ram_gb)
        if r["status"] == "refused":
            print(f"[{i + 1}/{len(files)}] {fname}: REFUSED - {r['reason']}; stopping.")
            break
        if r["status"] == "error":
            print(f"[{i + 1}/{len(files)}] {fname}: ERROR - {r.get('error')}")
        else:
            print(f"[{i + 1}/{len(files)}] {fname}: family={r['family']} "
                  f"bitmaps={r['bitmap_count']} bg={r['bg_type']} shapes={r['shape_count']} "
                  f"({r['seconds']}s, free_ram_before={r['free_ram_before_gb']}GB)")
        # replace-or-append so a --resume run updates in place
        results = [x for x in results if x["file"] != fname]
        results.append(r)
        REPORT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    n_a = sum(1 for r in results if r.get("family") == "A")
    n_b = sum(1 for r in results if r.get("family") == "B")
    n_u = sum(1 for r in results if r.get("family") == "unclassified")
    n_err = sum(1 for r in results if r.get("status") == "error")
    print(f"\nTotals so far: Family A={n_a}, Family B={n_b}, unclassified={n_u}, errors={n_err}, "
          f"total recorded={len(results)}")
    print(f"-> {REPORT_PATH}")


if __name__ == "__main__":
    main()
