"""Group and count why boards failed validate_all.py, from its JSON report.

Usage:
    python failure_analysis.py <validation_report.json>

Categorizes each failing board using the signals validate_all.py already
records (tile axis/count, object counts, per-role max diff%) - it can't see
anything validate_all.py didn't capture, so a board can only land in one
category even if several things are slightly off; it's picked by whichever
signal is largest, to keep the count actionable rather than double-counting.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def _role_max_diff(board: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for o in board.get("objects", []):
        d = max(o["dx_pct"], o["dy_pct"], o["dw_pct"], o["dh_pct"])
        out[o["role"]] = max(out.get(o["role"], 0.0), d)
    return out


def classify(board: dict, tolerance_pct: float) -> str:
    if "error" in board:
        return "engine/dump error"

    counts = board["counts"]
    ours_n, real_n = counts["ours"], counts["real"]
    count_ratio = real_n / ours_n if ours_n else float("inf")
    role_max = _role_max_diff(board)
    axis, n = board["our_tile"]["axis"], board["our_tile"]["n"]

    # object counts wildly apart -> the comparison itself is on shaky ground,
    # and it usually means our tile count guess (or lack of tiling) is wrong
    if count_ratio > 1.3 and (axis is None or n == 1):
        return "designer tiled but we didn't (scaled instead of tiled)"
    if count_ratio < 0.77 and axis and n > 1:
        return "we tiled but designer didn't (over-tiled)"
    if axis and n > 1 and not (0.8 <= count_ratio <= 1.25):
        return "tile count mismatch (tiled both, different N)"

    if role_max.get("shopname", 0) >= tolerance_pct and role_max.get("shopname", 0) == max(role_max.values(), default=0):
        return "shop name position/size off"

    if axis and n > 1 and role_max.get("logo", 0) >= tolerance_pct:
        return "tiled panel gap/scale drift"

    if role_max.get("logo", 0) >= tolerance_pct or role_max.get("text", 0) >= tolerance_pct:
        return "logo/text position or size drift (possibly ungrouped-fragment shattering)"

    return "other/unclassified drift"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    tol = report["tolerance_pct"]

    reasons = Counter()
    by_reason: dict[str, list[str]] = {}
    for b in report["boards"]:
        if b.get("pass"):
            continue
        reason = classify(b, tol)
        reasons[reason] += 1
        by_reason.setdefault(reason, []).append(b["file"])

    total = len(report["boards"])
    n_fail = sum(reasons.values())
    print(f"{n_fail}/{total} boards fail at {tol}% tolerance\n")
    for reason, count in reasons.most_common():
        print(f"{count:>3}  {reason}")
        for f in by_reason[reason]:
            print(f"       - {f}")


if __name__ == "__main__":
    main()
