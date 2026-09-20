"""Agarpathi phase 1, step 1: dump one real Agarpathi .cdr via COM, one file
per process invocation (never batches - see CLAUDE.md "Agarpathi is
untested... do not run a full validate_all.py agarpathi batch without
confirming timing/memory behavior on one file first").

Read-only against signage_dataset/ (dump_objects.py never writes to the
source file); all output goes under backend/dataset_analysis/. Refuses to
even start if free RAM is below `--min-ram-gb` (default 3.0, per this
session's RAM protocol) - checked HERE, before `corel_supervisor.run_batch`
gets a chance to (its own floor is a looser 1.5GB, meant for full batches).

Each invocation is its own single-job `corel_supervisor.run_batch` call - a
fresh worker subprocess, a fresh CorelDRAW instance, quit at the end
(dump_objects.dump() never pools) - so "one file at a time, instance
recycled after each file" is just "run this script once per file", not a
new pooling mode to build.

While the dump runs, a background thread in THIS process polls free RAM
every 0.5s and keeps the lowest value seen - the closest available proxy
for "peak RAM used" without instrumenting the worker subprocess itself.

Usage:
    python tools/agarpathi_dump_probe.py "<path to .cdr>" [--min-ram-gb 3.0]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Tamil text content must never crash the report
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import corel_supervisor, corel_util  # noqa: E402
from tools.dataset_inventory import _safe_name  # noqa: E402

OUT_ROOT = ROOT / "dataset_analysis" / "inventory" / "agarpathi_phase1"
SHAPE_TYPES_OF_INTEREST = ("group", "curve", "text", "bitmap")


def zip_largest_entries(cdr_path: Path, top_n: int = 5) -> list[dict]:
    """Read-only, no COM: the .cdr's own zip directory, largest entries first -
    finds the embedded bitmap's stored size without opening CorelDRAW at all
    (a .cdr from X4+ is a zip archive - see CLAUDE.md).
    """
    try:
        with zipfile.ZipFile(cdr_path) as z:
            infos = sorted(z.infolist(), key=lambda i: i.file_size, reverse=True)[:top_n]
            return [{"name": i.filename, "stored_mb": round(i.file_size / 1_000_000, 2),
                     "compressed_mb": round(i.compress_size / 1_000_000, 2)} for i in infos]
    except zipfile.BadZipFile:
        return []


def summarize_dump(dump: dict) -> dict:
    shapes = dump["shapes"]
    counts: dict[str, int] = {}
    for s in shapes:
        counts[s["type"]] = counts.get(s["type"], 0) + 1
    texts = [{"content": (s.get("text") or "")[:60], "font": s.get("font"), "font_size": s.get("font_size")}
             for s in shapes if s["type"] == "text"]
    bitmaps = [{"w_mm": round(s["w"], 1), "h_mm": round(s["h"], 1)} for s in shapes if s["type"] == "bitmap"]
    return {
        "page_mm": dump["page_mm"], "shape_count": dump["shape_count"],
        "counts_by_type": counts, "text_objects": texts, "bitmap_shapes": bitmaps,
    }


def probe(cdr_path: Path, min_ram_gb: float) -> dict:
    free = corel_util.check_memory()
    if free < min_ram_gb:
        return {"file": cdr_path.name, "status": "refused",
                "reason": f"free RAM {free:.2f} GB is below the {min_ram_gb:.1f} GB floor for this phase"}

    safe = _safe_name(cdr_path.stem)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    dump_cache = OUT_ROOT / f"{safe}.json"
    results_path = OUT_ROOT / f"{safe}.worker_results.json"

    min_free = {"value": free}
    stop = threading.Event()

    def _watch():
        while not stop.is_set():
            v = corel_util.check_memory()
            if v < min_free["value"]:
                min_free["value"] = v
            stop.wait(0.5)

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    t0 = time.time()
    try:
        results = corel_supervisor.run_batch(
            [{"dump_only": str(cdr_path), "dump_cache": str(dump_cache)}], results_path,
        )
    finally:
        stop.set()
        watcher.join(timeout=2)
        for p in (results_path, results_path.with_suffix(".heartbeat"), results_path.with_suffix(".done"),
                  results_path.with_suffix(".jobs.json")):
            p.unlink(missing_ok=True)
    elapsed = time.time() - t0

    entry = results[0]
    report = {
        "file": cdr_path.name, "status": entry.get("status"),
        "seconds": round(elapsed, 1), "worker_reported_seconds": entry.get("seconds"),
        "free_ram_before_gb": round(free, 2), "min_free_ram_seen_gb": round(min_free["value"], 2),
        "approx_peak_used_gb": round(free - min_free["value"], 2),
        "zip_largest_entries": zip_largest_entries(cdr_path),
    }
    if entry.get("status") == "done":
        report["dump"] = summarize_dump(entry["dump"])
    else:
        report["error"] = entry.get("error")
    return report


def _print_report(r: dict) -> None:
    print(f"\n=== {r['file']} ===")
    if r["status"] == "refused":
        print(f"REFUSED: {r['reason']}")
        return
    print(f"status={r['status']} time={r.get('seconds')}s "
          f"free_RAM_before={r.get('free_ram_before_gb')}GB min_free_seen={r.get('min_free_ram_seen_gb')}GB "
          f"approx_peak_used={r.get('approx_peak_used_gb')}GB")
    if r["status"] != "done":
        print("ERROR:", r.get("error"))
        return
    d = r["dump"]
    print(f"page: {d['page_mm']['w']:.1f} x {d['page_mm']['h']:.1f} mm, {d['shape_count']} shapes total")
    print("counts by type:", d["counts_by_type"])
    print(f"text objects ({len(d['text_objects'])}):")
    for t in d["text_objects"]:
        print(f"   {t['font']!r} {t['font_size']}pt: {t['content']!r}")
    print(f"bitmap shapes ({len(d['bitmap_shapes'])}):", d["bitmap_shapes"])
    print("largest zip entries (no-COM, from the .cdr's own zip structure):")
    for e in r["zip_largest_entries"]:
        print(f"   {e['name']}: {e['stored_mb']}MB stored ({e['compressed_mb']}MB compressed)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cdr_path")
    ap.add_argument("--min-ram-gb", type=float, default=3.0)
    args = ap.parse_args()

    report = probe(Path(args.cdr_path), args.min_ram_gb)
    _print_report(report)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = OUT_ROOT / f"{_safe_name(Path(args.cdr_path).stem)}.summary.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"-> {out_path}")
    sys.exit(0 if report["status"] in ("done", "refused") else 1)


if __name__ == "__main__":
    main()
