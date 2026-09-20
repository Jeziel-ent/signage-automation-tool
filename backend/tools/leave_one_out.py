"""Phase 2 leave-one-out validation: for each plain (non-tiled) real dalmia
board, build examples.json from every *other* board, predict this board's
layout from those examples, and compare the prediction against this
board's own real layout (ground truth) - "the honest accuracy number" the
project plan asks for, next to the existing rule-based baseline's already
-known diff (from validate_all.py's validation_report.json).

Wide/tiled boards are NOT run leave-one-out here: there are only 4 of them
and the wide-panel rule (dalmia_wide_panel_entities) was hand-derived from
all 4 - there isn't enough data to hold one out and still have a
meaningful rule from the rest. That comparison (rule's prediction vs. each
of the 4 boards it was built from) is reported separately and honestly
labelled as not-held-out.

Offline - reads cached dumps only, no CorelDRAW.

Usage:
    python leave_one_out.py dalmia
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.example_engine import dalmia_wide_panel_entities, master_entities, predict_layout  # noqa: E402
from app.layout import Obj  # noqa: E402
from tools.build_examples import UNIT_MM, build as build_examples_excluding  # noqa: E402
from tools.validate_all import (  # noqa: E402
    BRAND_MASTER, DATASET, REAL_DUMPS_CACHE_ROOT, VALIDATION_ROOT, _flatten_leaves, _safe,
)
from app.batch_import import parse_shop_lines  # noqa: E402

TILE_ASPECT_THRESHOLD = 1.4


def _leaf_objs(dump: dict) -> list[Obj]:
    return [Obj(str(i), s["name"], s["type"], s["x"], s["y"], s["w"], s["h"], s.get("text"))
            for i, s in enumerate(_flatten_leaves(dump))]


def _match_copies(pred_copies: list[dict], gt_copies: list[dict]) -> list[tuple[dict, dict]]:
    pred_sorted = sorted(pred_copies, key=lambda c: c["cx_frac"])
    gt_sorted = sorted(gt_copies, key=lambda c: c["cx_frac"])
    return list(zip(pred_sorted, gt_sorted))  # only meaningful when counts match; caller checks that


def _diff_pct(a: dict, b: dict) -> float:
    return max(abs(a[k] - b[k]) * 100 for k in ("cx_frac", "cy_frac", "w_frac", "h_frac"))


def score_prediction(predicted: dict[str, dict], ground_truth: dict[str, dict]) -> dict:
    diffs = []
    notes = []
    for key, gt in ground_truth.items():
        pred = predicted.get(key, {"present": False})
        if not gt.get("present") and not pred.get("present"):
            continue
        if gt.get("present") != pred.get("present"):
            notes.append(f"{key}: present mismatch (predicted={pred.get('present')}, real={gt.get('present')})")
            diffs.append(100.0)  # a wrong presence call is a full-scale miss
            continue
        if gt["repeat_count"] != pred["repeat_count"]:
            notes.append(f"{key}: repeat count mismatch (predicted={pred['repeat_count']}, real={gt['repeat_count']})")
            diffs.append(100.0)
            continue
        for p, g in _match_copies(pred["copies"], gt["copies"]):
            diffs.append(_diff_pct(p, g))
    return {
        "max_diff_pct": max(diffs) if diffs else None,
        "mean_diff_pct": sum(diffs) / len(diffs) if diffs else None,
        "notes": notes,
    }


def run(brand: str) -> list[dict]:
    cfg = BRAND_MASTER[brand]
    shopname_hints = [cfg["shop_name"], cfg["shop_name_local"]]
    brand_dir = DATASET / ("Agarpathi" if brand == "agarpathi" else brand)
    master_path = brand_dir / cfg["file"]
    master_dump = json.loads((REAL_DUMPS_CACHE_ROOT / brand / f"{_safe(master_path.stem)}.json").read_text(encoding="utf-8"))
    master_w, master_h = master_dump["page_mm"]["w"], master_dump["page_mm"]["h"]
    master_ents = master_entities(_leaf_objs(master_dump), master_w, master_h, shopname_hints)

    baseline_report = json.loads((VALIDATION_ROOT / brand / "validation_report.json").read_text(encoding="utf-8"))
    baseline_by_file = {b["file"]: b for b in baseline_report["boards"]}

    results = []
    for f in sorted(brand_dir.glob("*.cdr")):
        if "copy" in f.stem.lower() or f.name == master_path.name:
            continue
        parsed = parse_shop_lines(f.stem)
        if not parsed.shops:
            continue
        shop_spec = parsed.shops[0]
        cache_path = REAL_DUMPS_CACHE_ROOT / brand / f"{_safe(f.stem)}.json"
        if not cache_path.exists():
            continue
        board_w = shop_spec.width * UNIT_MM[shop_spec.unit]
        board_h = shop_spec.height * UNIT_MM[shop_spec.unit]
        rx, ry = board_w / master_w, board_h / master_h
        is_tiled = max(rx, ry) > TILE_ASPECT_THRESHOLD

        real_dump = json.loads(cache_path.read_text(encoding="utf-8"))
        gt_ents_list = master_entities(_leaf_objs(real_dump), board_w, board_h, shopname_hints)
        from app.example_engine import build_examples as _be
        ground_truth = _be(master_ents, master_w, master_h, [(board_w, board_h, gt_ents_list)])["boards"][0]["entities"]

        if is_tiled:
            # not held out - the wide-panel rule was built from all 4 wide
            # samples, this board included; reported as such, not blended
            # into the leave-one-out headline number
            predicted = dalmia_wide_panel_entities(master_ents, board_w, board_h)
            # non-logo entities: just reuse ground truth's own bg/text/shopname
            # (this run isn't testing those - the wide rule only overrides logo_cluster)
            for key, val in ground_truth.items():
                if key not in predicted:
                    predicted[key] = val
            mode = "wide-rule (not held out)"
        else:
            examples = build_examples_excluding(brand, exclude=f.stem)
            predicted = predict_layout(brand, master_ents, master_w, master_h, examples, board_w, board_h)
            mode = "leave-one-out"

        score = score_prediction(predicted, ground_truth) if predicted else {"max_diff_pct": None, "mean_diff_pct": None, "notes": ["no prediction"]}
        baseline = baseline_by_file.get(f.name, {})
        results.append({
            "file": f.name, "mode": mode,
            "baseline_max_diff_pct": baseline.get("diff_pct", {}).get("max"),
            "example_max_diff_pct": score["max_diff_pct"],
            "example_mean_diff_pct": score["mean_diff_pct"],
            "notes": score["notes"],
        })
    return results


def render_table(results: list[dict]) -> str:
    lines = [
        "| File | Mode | Baseline (rule-based) max diff % | Example-based max diff % | Notes |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        b = "-" if r["baseline_max_diff_pct"] is None else f"{r['baseline_max_diff_pct']:.1f}"
        e = "-" if r["example_max_diff_pct"] is None else f"{r['example_max_diff_pct']:.1f}"
        notes = "; ".join(r["notes"]) or "-"
        lines.append(f"| {r['file']} | {r['mode']} | {b} | {e} | {notes} |")
    return "\n".join(lines)


def main():
    brand = sys.argv[1] if len(sys.argv) > 1 else "dalmia"
    results = run(brand)
    table = render_table(results)
    print(table)
    out = ROOT / "dataset_analysis" / "leave_one_out" / brand
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(table + "\n", encoding="utf-8")
    (out / "report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n-> {out / 'report.md'}")


if __name__ == "__main__":
    main()
