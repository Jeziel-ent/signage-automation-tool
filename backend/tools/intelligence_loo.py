"""Leave-one-out accuracy of Corel Intelligence on the stored corrections (the yardstick for any change to how it learns).

    SIGNAGE_DATA=<data dir> python tools/intelligence_loo.py [--brand NAME] [--rows]

For every stored record R that moved / resized objects: hide R, rebuild the engine's placement from R's own "before" boxes, let
`corrections.apply_to_placed` correct it using ONLY the other records that describe the same board (same brand, master file, size, board type -
`usable_records`, nearby sizes included) and measure how far each object ends from where R's designer put it ("after"), against how far the
engine alone was ("before"). Distances are the centre distance in mm plus nothing else, so a lower number is better and 0 is perfect.

Prints one line per record that had peers and a total: objects covered, mean error WITHOUT learning, mean error WITH learning, and how many of
R's changes learning could not place (no peer named them). Read-only: it never writes the database.
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import corrections, db  # noqa: E402


class _Obj:
    """The bits of a layout.Placed that apply_to_placed reads and writes."""

    def __init__(self, oid: str, x: float, y: float, w: float, h: float):
        self.id, self.role, self.x, self.y, self.w, self.h, self.warnings = oid, "logo", x, y, w, h, []


def _centre_error(box_a: dict, box_b: dict, pw: float, ph: float) -> float:
    return math.hypot((box_a["cx"] - box_b["cx"]) * pw, (box_a["cy"] - box_b["cy"]) * ph)


def _moved_changes(row: dict) -> list[dict]:
    return [c for c in row["record"].get("changes", [])
            if c.get("action") in corrections.APPLIED_ACTIONS and (c.get("signature") or {}).get("kind") != "text" and c.get("before") and c.get("after")]


def evaluate(rows: list[dict], brand: str | None = None, cross: bool = False, min_confidence: float | None = None) -> list[dict]:
    """One result per record that has at least one moved / resized change and at least one peer record for its board. With `cross` the peers are
    ONLY records made on other sizes of the same master (what Step 2 adds), so the result says how well one size teaches another."""
    out = []
    for row in rows:
        if brand and row.get("brand") != brand:
            continue
        changes = _moved_changes(row)
        if not changes:
            continue
        pw, ph = float(row["page_w_mm"]), float(row["page_h_mm"])
        others = [r for r in rows if r["shop_id"] != row["shop_id"]]
        peers = corrections.usable_records(others, row.get("brand"), row.get("master_file"), pw, ph, row.get("board_type"), family=cross)
        if cross:
            peers = [r for r in peers if r.get("family")]
        if not peers:
            continue
        placed = []
        for i, c in enumerate(changes):
            b = c["before"]
            w, h = b["w"] * pw, b["h"] * ph
            placed.append(_Obj(str(c.get("master_id") or f"o{i}"), b["cx"] * pw - w / 2, b["cy"] * ph - h / 2, w, h))
        floor = corrections.APPLY_MIN_CONFIDENCE if min_confidence is None else min_confidence
        summary = corrections.apply_to_placed(placed, pw, ph, peers, min_confidence=floor)
        base_err, learned_err = [], []
        for c, p in zip(changes, placed):
            base_err.append(_centre_error(c["before"], c["after"], pw, ph))
            learned_err.append(_centre_error({"cx": (p.x + p.w / 2) / pw, "cy": (p.y + p.h / 2) / ph}, c["after"], pw, ph))
        out.append({"shop_id": row["shop_id"], "brand": row.get("brand"), "size": f"{pw:.0f}x{ph:.0f}", "peers": len(peers),
                    "objects": len(changes), "applied": summary["applied"], "skipped": summary["skipped"], "conflicting": summary["conflicting"],
                    "held_back": summary.get("held_back", 0), "gated": summary.get("gated", 0),
                    "engine_mm": statistics.mean(base_err), "learned_mm": statistics.mean(learned_err),
                    "engine_all": base_err, "learned_all": learned_err})
    return out


def totals(results: list[dict]) -> dict:
    base = [e for r in results for e in r["engine_all"]]
    learned = [e for r in results for e in r["learned_all"]]
    if not base:
        return {"records": 0}
    return {"records": len(results), "objects": len(base), "applied": sum(r["applied"] for r in results),
            "held_back": sum(r["held_back"] for r in results), "gated": sum(r["gated"] for r in results),
            "engine_mean_mm": statistics.mean(base), "learned_mean_mm": statistics.mean(learned),
            "engine_median_mm": statistics.median(base), "learned_median_mm": statistics.median(learned),
            "better": sum(1 for a, b in zip(base, learned) if b < a - 0.5), "worse": sum(1 for a, b in zip(base, learned) if b > a + 0.5)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--brand")
    ap.add_argument("--rows", action="store_true", help="also print one line per record")
    ap.add_argument("--cross-size", action="store_true", help="predict each record only from records of OTHER sizes of the same master")
    ap.add_argument("--sweep", action="store_true", help="try several confidence floors and print the error of each (how APPLY_MIN_CONFIDENCE is tuned)")
    ap.add_argument("--min-confidence", type=float, help="confidence floor to use instead of corrections.APPLY_MIN_CONFIDENCE")
    args = ap.parse_args()
    rows = db.list_corrections()
    if args.sweep:
        print("floor   records objects placed held gated   mean mm (engine -> learned)   closer / further")
        for floor in (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
            t = totals(evaluate(rows, args.brand, cross=args.cross_size, min_confidence=floor))
            if t.get("records"):
                print(f"{floor:<7} {t['records']:<7} {t['objects']:<7} {t['applied']:<6} {t['held_back']:<4} {t['gated']:<5} {t['engine_mean_mm']:8.1f} -> {t['learned_mean_mm']:7.1f}"
                      f"            {t['better']} / {t['worse']}")
        return 0
    res = evaluate(rows, args.brand, cross=args.cross_size, min_confidence=args.min_confidence)
    if args.rows:
        for r in res:
            print(f"{r['brand'] or '-':<10} {r['size']:<10} peers={r['peers']:<2} objects={r['objects']:<2} applied={r['applied']:<2} "
                  f"engine={r['engine_mm']:7.1f} mm  learned={r['learned_mm']:7.1f} mm   {r['shop_id'][:40]}")
    t = totals(res)
    if not t.get("records"):
        print(f"{len(rows)} stored record(s), none with a peer for the same board: nothing to evaluate yet.")
        return 0
    print(f"{t['records']} record(s) with peers, {t['objects']} moved object(s): mean error {t['engine_mean_mm']:.1f} mm without learning -> "
          f"{t['learned_mean_mm']:.1f} mm with it (median {t['engine_median_mm']:.1f} -> {t['learned_median_mm']:.1f}); "
          f"{t['better']} object(s) closer, {t['worse']} further, {t['applied']} placed, {t['held_back']} held back, {t['gated']} undone by the quality gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
