"""Step 3: given the user's own OK/NOT_OK label per board (see
build_label_page.py), find which threshold on which metric best reproduces
those labels, and show the numbers - a PROPOSAL only. This script never
writes to metrics_config.json; whether/how to apply a proposed threshold is
the user's call (project rule of engagement: never tune a threshold just to
raise a pass rate).

Workflow:
    python tools/build_metrics_report.py dalmia     # builds the images/metrics
    python tools/build_label_page.py dalmia         # open the page it prints,
                                                      # click OK/NOT OK per board,
                                                      # click "Download labels.json",
                                                      # save as dataset_analysis/labels/dalmia_labels.json
    python tools/calibrate_threshold.py dalmia

Writes dataset_analysis/calibration/<brand>/proposal.json and .md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.build_metrics_report import build_cards  # noqa: E402

LABELS_ROOT = ROOT / "dataset_analysis" / "labels"
CALIBRATION_ROOT = ROOT / "dataset_analysis" / "calibration"


def load_labels(brand: str) -> dict[str, bool]:
    """{"safe_board_name": True|False} - True means the user marked it OK."""
    path = LABELS_ROOT / f"{brand}_labels.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no labels file at {path} - run build_label_page.py {brand}, label the boards in your "
            f"browser, and save the downloaded labels.json there first."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: (v == "OK") for k, v in raw.items() if v in ("OK", "NOT_OK")}


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def best_threshold(values: list[float], labels: list[bool], direction: str) -> dict:
    """Find the threshold on `values` that best predicts `labels` (True = OK).

    `direction` is "higher_is_better" (predicted OK iff value >= threshold,
    e.g. a visual similarity score) or "lower_is_better" (predicted OK iff
    value <= threshold, e.g. a position error in mm). Tries every midpoint
    between consecutive sorted unique values, plus one threshold below the
    minimum and one above the maximum (so "everything passes" / "nothing
    passes" are considered too - the best boundary might genuinely be one
    of those). Ties on accuracy are broken by preferring the threshold
    closer to the middle of the observed range, so the proposal doesn't
    happen to sit exactly on one particular board's own value.

    Returns {"threshold", "accuracy", "n_correct", "n_total", "misclassified":
    [index, ...]} - `misclassified` are indices into `values`/`labels`.
    """
    n = len(values)
    if n == 0:
        return {"threshold": None, "accuracy": None, "n_correct": 0, "n_total": 0, "misclassified": []}
    uniq = sorted(set(values))
    mid = (uniq[0] + uniq[-1]) / 2
    candidates = [uniq[0] - 1.0, uniq[-1] + 1.0] + [(a + b) / 2 for a, b in zip(uniq, uniq[1:])]

    best = None
    for t in candidates:
        pred = [(v >= t) if direction == "higher_is_better" else (v <= t) for v in values]
        correct = sum(p == truth for p, truth in zip(pred, labels))
        acc = correct / n
        key = (acc, -abs(t - mid))
        if best is None or key > best["_key"]:
            best = {
                "_key": key, "threshold": t, "accuracy": acc, "n_correct": correct, "n_total": n,
                "misclassified": [i for i, (p, truth) in enumerate(zip(pred, labels)) if p != truth],
            }
    best.pop("_key")
    return best


# Name, direction, and how to pull the value out of one build_metrics_report.py card.
_METRIC_DEFS = [
    ("visual_combined", "higher_is_better", lambda c: (c.get("visual") or {}).get("combined")),
    ("geo_max_position_error_mm", "lower_is_better", lambda c: (c.get("geometric") or {}).get("position_error_mm", {}).get("max")),
    ("geo_max_size_error_mm", "lower_is_better", lambda c: (c.get("geometric") or {}).get("size_error_mm", {}).get("max")),
    ("geo_mean_position_error_mm", "lower_is_better", lambda c: (c.get("geometric") or {}).get("position_error_mm", {}).get("mean")),
    # How many of our clusters had no matching cluster in the real file at all (metrics.cluster_compare) -
    # a structural "we produced something the real file doesn't have" signal, distinct from position/size
    # error on clusters that DID match. Added after manual inspection of the dalmia labels found it separates
    # 11/12 boards (only TAMILNADU STEELS stands out at 5, everything else is 0-2) where every other candidate
    # here caps at 10/12 - see CLAUDE.md "Step 3b" for the important caveat: this was found by looking at only
    # 2 negative labels, so treat it as a lead worth watching, not a validated rule.
    ("unmatched_ours", "lower_is_better", lambda c: (c.get("clusters") or {}).get("unmatched_ours")),
]
for _t in (2.0, 5.0, 10.0):
    def _area_pct(c, _t=_t):
        rows = (c.get("geometric") or {}).get("area_matched_pct") or []
        return next((r["pct"] for r in rows if r["tolerance_mm"] == _t), None)
    _METRIC_DEFS.append((f"geo_area_matched_within_{_t:g}mm_pct", "higher_is_better", _area_pct))


def propose_thresholds(cards: list[dict], labels: dict[str, bool]) -> dict:
    """One proposal per candidate metric, sorted best-accuracy-first, plus a
    non-threshold reference row for the content check (already a hard
    OK/FAIL, nothing to threshold). Only boards present in `labels` are used.
    """
    by_safe = {c["safe"]: c for c in cards}
    labelled = [(safe, ok) for safe, ok in labels.items() if safe in by_safe]
    unlabelled_cards = [c["safe"] for c in cards if c["safe"] not in labels]
    unknown_labels = [safe for safe in labels if safe not in by_safe]

    proposals = []
    for name, direction, getter in _METRIC_DEFS:
        safes, values, truths = [], [], []
        for safe, ok in labelled:
            v = getter(by_safe[safe])
            if v is not None:
                safes.append(safe)
                values.append(v)
                truths.append(ok)
        if len(set(values)) < 2:
            proposals.append({"metric": name, "direction": direction, "n_boards": len(values), "accuracy": None,
                              "note": "fewer than 2 distinct values among labelled boards - not enough to propose a threshold"})
            continue
        result = best_threshold(values, truths, direction)
        result["metric"] = name
        result["direction"] = direction
        result["misclassified"] = [safes[i] for i in result["misclassified"]]
        proposals.append(result)

    proposals.sort(key=lambda p: (p.get("accuracy") if p.get("accuracy") is not None else -1), reverse=True)

    content_rows = []
    for safe, ok in labelled:
        cc = (by_safe[safe].get("content_check") or {}).get("overall")
        if cc is None:
            continue
        predicted_ok = cc != "CONTENT_FAIL"
        content_rows.append((safe, predicted_ok == ok))
    content_summary = None
    if content_rows:
        n_correct = sum(1 for _, correct in content_rows if correct)
        content_summary = {
            "metric": "content_check (binary, not a threshold)", "n_correct": n_correct, "n_total": len(content_rows),
            "accuracy": n_correct / len(content_rows),
            "misclassified": [safe for safe, correct in content_rows if not correct],
        }

    return {
        "n_labelled": len(labelled), "n_ok": sum(1 for _, ok in labelled if ok),
        "n_not_ok": sum(1 for _, ok in labelled if not ok),
        "unlabelled_boards": unlabelled_cards, "unknown_labels_ignored": unknown_labels,
        "proposals": proposals, "content_check_reference": content_summary,
    }


def _render_md(brand: str, result: dict) -> str:
    lines = [
        f"# Threshold calibration proposal: {brand}",
        "",
        f"{result['n_labelled']} board(s) labelled ({result['n_ok']} OK, {result['n_not_ok']} NOT_OK). "
        "**This is a proposal, not a change** - metrics_config.json is untouched; decide by eye whether to apply one.",
        "",
    ]
    if result["unlabelled_boards"]:
        lines.append(f"Not yet labelled (excluded from this calibration): {', '.join(result['unlabelled_boards'])}")
        lines.append("")
    lines += ["| Metric | Direction | Best threshold | Accuracy | Correct/Total | Misclassified |",
              "|---|---|---|---|---|---|"]
    for p in result["proposals"]:
        if "note" in p:
            lines.append(f"| {p['metric']} | {p['direction']} | - | - | - | {p['note']} |")
            continue
        lines.append(
            f"| {p['metric']} | {p['direction']} | {p['threshold']:.3f} | {p['accuracy']:.0%} | "
            f"{p['n_correct']}/{p['n_total']} | {', '.join(p['misclassified']) or '-'} |"
        )
        if p["metric"] == "unmatched_ours":
            lines.append(
                f"> **Warning:** `unmatched_ours` was added as a candidate after eyeballing this exact "
                f"labelled set - its apparent separation rests on only {result['n_not_ok']} NOT_OK label(s). "
                f"Report only; not a validated rule until more negative labels confirm it."
            )
    if result["content_check_reference"]:
        c = result["content_check_reference"]
        lines += ["", f"**Reference (not a threshold):** content check alone gets {c['accuracy']:.0%} "
                      f"({c['n_correct']}/{c['n_total']}) agreement with your labels. "
                      f"Misclassified: {', '.join(c['misclassified']) or '-'}."]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brand")
    args = ap.parse_args()

    labels = load_labels(args.brand)
    cards, _ = build_cards(args.brand, want_images=False)
    result = propose_thresholds(cards, labels)

    out_dir = CALIBRATION_ROOT / args.brand
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "proposal.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    md = _render_md(args.brand, result)
    (out_dir / "proposal.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"-> {out_dir / 'proposal.json'}, {out_dir / 'proposal.md'}")


if __name__ == "__main__":
    main()
