"""Step 5 support: derive `app/confidence_bounds.json` from the validated
dalmia sample set - the historical (aspect range, typical/worst mm error)
per regime (tiled vs untiled) that `app/confidence.py` looks up at convert
time, WITHOUT a designer file for the new request (there isn't one yet).
This script is the only place that touches real validation data; the
consuming function only ever reads the small JSON it writes.

Known outliers (`validate_all.KNOWN_OUTLIERS`, e.g. board 12) are excluded
from the bounds - same reasoning as their exclusion from the pass rate
elsewhere: a one-off manual recomposition shouldn't set the bar for what
"typical" error looks like.

Usage:
    python tools/derive_confidence_bounds.py dalmia
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.build_metrics_report import build_cards  # noqa: E402
from tools.validate_all import BRAND_MASTER, VALIDATION_ROOT  # noqa: E402

BOUNDS_PATH = ROOT / "app" / "confidence_bounds.json"


def derive(brand: str) -> dict:
    report = json.loads((VALIDATION_ROOT / brand / "validation_report.json").read_text(encoding="utf-8"))
    outlier_files = {b["file"] for b in report["boards"] if b.get("excluded_outlier")}
    cards, _ = build_cards(brand, want_images=False)

    buckets: dict[str, list[dict]] = {"tiled": [], "untiled": []}
    for c in cards:
        if c["file"] in outlier_files or "geometric" not in c:
            continue
        w, h = (float(x) for x in c["target"].replace("mm", "").split("x"))
        aspect = max(w, h) / min(w, h)
        tiled = c["tile"] != "None,1"
        g = c["geometric"]
        buckets["tiled" if tiled else "untiled"].append({
            "aspect": aspect,
            "position_error_mm": g["position_error_mm"]["max"],
            "size_error_mm": g["size_error_mm"]["max"],
        })

    regimes = {}
    for name, rows in buckets.items():
        if not rows:
            continue
        aspects = [r["aspect"] for r in rows]
        pos = [r["position_error_mm"] for r in rows]
        size = [r["size_error_mm"] for r in rows]
        regimes[name] = {
            "n_samples": len(rows),
            "aspect_min": min(aspects), "aspect_max": max(aspects),
            "median_position_error_mm": statistics.median(pos),
            "median_size_error_mm": statistics.median(size),
            "max_position_error_mm": max(pos),
            "max_size_error_mm": max(size),
        }

    cfg = BRAND_MASTER[brand]
    from app.batch_import import parse_shop_lines
    from app.layout import to_mm

    dump_path = ROOT / "dataset_analysis" / "dumps"
    master_page = None
    for f in dump_path.glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("file", "").lower() == cfg["file"].lower() or "master" in f.stem.lower() and brand in f.stem.lower():
            master_page = d.get("page_mm")
            break
    if master_page is None:
        # fall back to one already-generated board's own recorded original_page_mm
        safe_dirs = list((VALIDATION_ROOT / brand / "generated").glob("*"))
        for d in safe_dirs:
            reports = list(d.glob("*_report.json"))
            if reports:
                master_page = json.loads(reports[0].read_text(encoding="utf-8"))["original_page_mm"]
                break
    assert master_page, f"could not determine {brand}'s master page size"

    return {"master_mm": master_page, "regimes": regimes, "excluded_outliers": sorted(outlier_files)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brand")
    args = ap.parse_args()

    all_bounds = json.loads(BOUNDS_PATH.read_text(encoding="utf-8")) if BOUNDS_PATH.exists() else {}
    all_bounds[args.brand] = derive(args.brand)
    BOUNDS_PATH.write_text(json.dumps(all_bounds, indent=2), encoding="utf-8")
    print(json.dumps(all_bounds[args.brand], indent=2))
    print(f"-> {BOUNDS_PATH}")


if __name__ == "__main__":
    main()
