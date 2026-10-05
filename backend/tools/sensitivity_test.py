"""Sensitivity test for the Phase 1 visual metric: does it actually drop
when the output is genuinely wrong, and by how much for each kind of wrong?

Not a test of the generation pipeline - a test of metrics.py itself, using
one already-generated "ours" PNG as a clean baseline and comparing it
against synthetic/real perturbations of known kind:

  - 5% / 10% horizontal shift (simulates a logo/panel placed slightly off
    from where it should be - the kind of drift validate_all.py's geometry
    diff already measures numerically; this checks the *visual* metric
    reacts to the same kind of error)
  - wrong shop name / wrong board: compared against a different real
    board's "ours" render at the same target page size (05 M Pandi vs. the
    03 NR TRADERS baseline) - same layout, same logos, different shop-name
    text and minor content differences, a reasonable stand-in for "the
    wrong shop's board got shown"
  - missing graphic: the central logo-panel region blacked out, simulating
    a dropped/failed shape

If PASS/FAIL flips or the combined score doesn't move for an obviously
wrong output, the metric (or its weights/threshold) needs fixing - this is
run to check that, not to tune the threshold to a preferred number (see
CLAUDE.md rules of engagement).

Usage:
    python sensitivity_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import metrics  # noqa: E402

BASELINE = ROOT / "dataset_analysis/validation/dalmia/generated/03_-_120_X_48_Inch_-_2_Nos_Double_Side_GSB_-_NR_TRADERS/NR_TRADERS.png"
WRONG_BOARD = ROOT / "dataset_analysis/validation/dalmia/generated/05_-_120_X_48_Inch_-_Nonlit_-_M_Pandi/M_Pandi.png"
OUT_DIR = ROOT / "dataset_analysis" / "sensitivity"


def _shift(img: Image.Image, frac: float) -> Image.Image:
    w, _ = img.size
    dx = round(w * frac)
    shifted = ImageChops.offset(img, dx, 0)
    return shifted


def _black_out_center(img: Image.Image, width_frac: float = 0.4) -> Image.Image:
    from PIL import ImageDraw

    img = img.copy()
    w, h = img.size
    bw = round(w * width_frac)
    x0 = (w - bw) // 2
    ImageDraw.Draw(img).rectangle([x0, 0, x0 + bw, h], fill=(0, 0, 0))
    return img


def run() -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    config = metrics.load_config()
    base_img = Image.open(BASELINE).convert("RGB")

    cases = []

    control_path = OUT_DIR / "control.png"
    base_img.save(control_path)
    cases.append(("control (identical image)", control_path))

    for frac in (0.05, 0.10):
        p = OUT_DIR / f"shift_{int(frac * 100)}pct.png"
        _shift(base_img, frac).save(p)
        cases.append((f"{frac:.0%} horizontal shift", p))

    p = OUT_DIR / "wrong_board.png"
    Image.open(WRONG_BOARD).convert("RGB").save(p)
    cases.append(("wrong shop's board (05 M Pandi vs. 03 NR TRADERS baseline)", p))

    p = OUT_DIR / "missing_graphic.png"
    _black_out_center(base_img).save(p)
    cases.append(("missing graphic (central 40% blacked out)", p))

    results = []
    for label, path in cases:
        vis = metrics.visual_similarity(BASELINE, path, config)
        results.append({"case": label, **vis})
    return results


def _render_table(results: list[dict]) -> str:
    baseline_combined = results[0]["combined"]
    lines = [
        "| Case | SSIM | phash sim | edge sim | combined | delta vs control | PASS? |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        delta = r["combined"] - baseline_combined
        lines.append(
            f"| {r['case']} | {r['ssim']:.3f} | {r['phash_similarity']:.3f} | {r['edge_similarity']:.3f} | "
            f"{r['combined']:.3f} | {delta:+.3f} | {'PASS' if r['pass'] else 'FAIL'} |"
        )
    return "\n".join(lines)


def main():
    results = run()
    table = _render_table(results)
    print(table)
    (OUT_DIR / "sensitivity_report.md").write_text(
        "# Sensitivity test: does the visual metric react to known-wrong output?\n\n"
        f"Baseline: `{BASELINE.relative_to(ROOT)}`\n\n" + table + "\n", encoding="utf-8",
    )
    print(f"\n-> {OUT_DIR / 'sensitivity_report.md'}")


if __name__ == "__main__":
    main()
