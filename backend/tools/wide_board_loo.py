"""Step 4: leave-one-out check on the wide-board `panel_sequence` size_table
(`app/brand_rules/dalmia.json`) - "if we hadn't measured this specific
board's real render, how well would the CURRENT per-aspect interpolation
(`layout._interp_size_table`) have predicted it from the other samples, and
does any simple alternative do better?"

Only 4 real wide dalmia boards exist (02 180x48, 06 180x60, 11-216, 11-240) -
each one's `size_table` row IS a direct real-render measurement (see
CLAUDE.md "Wide-board panel sequence"), so there is no separate "real" data
to re-derive here; leaving a board's row out and re-interpolating from the
other 3 is the whole experiment. Pure Python, no CorelDRAW, no new dumps.

Usage:
    python tools/wide_board_loo.py
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.layout import _interp_size_table  # noqa: E402

BOARDS = {  # aspect -> (label, new_w_mm, new_h_mm)
    3.75: ("02 (180x48)", 4572.0, 1219.2),
    3.0: ("06 (180x60)", 4572.0, 1524.0),
    4.0: ("11-240 (240x60)", 6096.0, 1524.0),
    4.5: ("11-216 (216x48)", 5486.4, 1219.2),
}
ASPECT_SPLIT = 4.25  # from brand_rules/dalmia.json - only 11-216 (4.5) is a sequence_4 sample


def load_groups(brand: str = "dalmia") -> dict:
    import json

    d = json.loads((ROOT / "app" / "brand_rules" / f"{brand}.json").read_text(encoding="utf-8"))
    return {g["group_id"]: g for g in d["panel_sequence"]["groups"] if "size_table" in g}


def loo_rows(group: dict) -> list[dict]:
    """One row per held-out aspect: current-interpolation error and a flat
    -average-of-cy alternative's error, both in mm on that board's own page.
    """
    table = group["size_table"]
    out = []
    for row in table:
        a = row["aspect"]
        label, new_w, new_h = BOARDS[a]
        others = [r for r in table if r["aspect"] != a]
        pred_h, pred_w, pred_cy = _interp_size_table(others, a)
        flat_cy = statistics.mean(r["target_cy_frac"] for r in others)
        out.append({
            "aspect": a, "label": label, "sequence": "seq4" if a > ASPECT_SPLIT else "seq3",
            "h_err_mm": (pred_h - row["target_h_frac"]) * new_h,
            "w_err_mm": (pred_w - row["target_w_frac"]) * new_w,
            "cy_err_interp_mm": (pred_cy - row["target_cy_frac"]) * new_h,
            "cy_err_flat_mm": (flat_cy - row["target_cy_frac"]) * new_h,
        })
    return out


def main():
    groups = load_groups()
    for gid in ("tamil_card", "roof_graphic"):
        print(f"\n=== {gid} - leave-one-out (each board predicted from the OTHER 3) ===")
        print(f"{'board':18s} {'seq':5s} {'h_err_mm':>9s} {'w_err_mm':>9s} {'cy_err(interp)':>15s} {'cy_err(flat_avg)':>17s}")
        for r in loo_rows(groups[gid]):
            print(f"{r['label']:18s} {r['sequence']:5s} {r['h_err_mm']:+9.1f} {r['w_err_mm']:+9.1f} "
                  f"{r['cy_err_interp_mm']:+15.1f} {r['cy_err_flat_mm']:+17.1f}")
    print("""
Conclusion (see CLAUDE.md "Step 4"): size (h/w) leave-one-out error is
moderate for the 3 sequence_3 boards (tens to ~150mm) but severe for 11-216
(up to ~320mm on width) - the lone sequence_4 sample, which the current
scheme can only ever CLAMP to the nearest sequence_3 point for, never
genuinely interpolate. A flat-average alternative for target_cy_frac does
not consistently beat the current per-aspect interpolation (wins on some
boards, loses on others, all within single-digit mm) - not applied.
No rule change proposed; 11-216 stays a REVIEW case for insufficient data,
not a fittable pattern.""")


if __name__ == "__main__":
    main()
