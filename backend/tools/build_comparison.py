"""Build a visual comparison sheet from a validate_all.py report: ours vs.
the designer's real board, side by side, for every board except the master.

Reads backend/dataset_analysis/validation/<brand>/validation_report.json
(already has ours_png/real_png paths - see validate_all.py) and writes
backend/dataset_analysis/compare/index.html plus a same-width copy of each
image pair under backend/dataset_analysis/compare/<safe>/.

Offline - no CorelDRAW, just PIL. Run after validate_all.py.

Usage:
    python build_comparison.py dalmia
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VALIDATION_ROOT = ROOT / "dataset_analysis" / "validation"
COMPARE_ROOT = ROOT / "dataset_analysis" / "compare"
DISPLAY_WIDTH = 900


def _resize_to_width(src: Path, dst: Path, width: int = DISPLAY_WIDTH) -> None:
    img = Image.open(src).convert("RGB")
    w, h = img.size
    new_h = max(1, round(h * width / w))
    img.resize((width, new_h), Image.LANCZOS).save(dst)


def build(brand: str) -> Path:
    report = json.loads((VALIDATION_ROOT / brand / "validation_report.json").read_text(encoding="utf-8"))
    # the master-as-its-own-target board has target size == its own page size
    # and 0% diff; simplest reliable skip is "diff max == 0.0 and no tile" but
    # explicit is clearer: the brand's master filename is in BRAND_MASTER in
    # validate_all.py - avoid importing it here (keeps this script standalone
    # against just the JSON) and instead skip whichever board has the lowest
    # target area (the master itself, "02 ... 120x48", is never the master
    # for a *different* file) - simplest robust rule: skip if this file IS
    # the one used to generate every other board, identified by max_diff==0.
    cards = []
    for b in report["boards"]:
        if "error" in b or abs(b.get("diff_pct", {}).get("max", 1)) < 1e-9:
            continue  # master-as-its-own-target sanity check, not a real comparison
        if "ours_png" not in b or "real_png" not in b:
            continue

        safe = Path(b["ours_png"]).parent.name
        card_dir = COMPARE_ROOT / safe
        card_dir.mkdir(parents=True, exist_ok=True)
        ours_out = card_dir / "ours_display.png"
        real_out = card_dir / "real_display.png"
        try:
            _resize_to_width(ROOT / b["ours_png"], ours_out)
            _resize_to_width(ROOT / b["real_png"], real_out)
        except Exception as e:
            print(f"skip {b['file']}: {e}")
            continue

        cards.append({
            "file": b["file"], "shop": b["shop_name"],
            "target": f"{b['target_mm']['w']:.0f}x{b['target_mm']['h']:.0f}mm",
            "max_diff": b["diff_pct"]["max"],
            "ssim": b.get("ssim"),
            "pass_2": b.get("pass_2pct"), "pass_5": b.get("pass_5pct"),
            "outlier": b.get("outlier_note"),
            "ours_rel": f"{safe}/ours_display.png", "real_rel": f"{safe}/real_display.png",
        })

    COMPARE_ROOT.mkdir(parents=True, exist_ok=True)
    (COMPARE_ROOT / "index.html").write_text(_render_html(brand, cards), encoding="utf-8")
    print(f"{len(cards)} boards -> {COMPARE_ROOT / 'index.html'}")
    return COMPARE_ROOT / "index.html"


def _render_html(brand: str, cards: list[dict]) -> str:
    def esc(s):
        return html.escape(str(s)) if s is not None else "-"

    card_html = []
    for c in cards:
        badge = "outlier" if c["outlier"] else ("pass" if c["pass_2"] else ("close" if c["pass_5"] else "fail"))
        diff_s = f"{c['max_diff']:.1f}%" if c["max_diff"] is not None else "-"
        ssim_s = f"{c['ssim']:.3f}" if c["ssim"] is not None else "-"
        outlier_html = f'<p class="outlier-note">{esc(c["outlier"])}</p>' if c["outlier"] else ""
        card_html.append(f"""
        <section class="card {badge}">
          <h2>{esc(c['file'])}</h2>
          <p class="meta">{esc(c['shop'])} &middot; target {esc(c['target'])} &middot;
             max diff {diff_s} &middot; SSIM {ssim_s} &middot;
             <span class="badge {badge}">{badge.upper()}</span></p>
          {outlier_html}
          <div class="pair">
            <figure><img src="{esc(c['ours_rel'])}" alt="ours"><figcaption>ours</figcaption></figure>
            <figure><img src="{esc(c['real_rel'])}" alt="designer's real file"><figcaption>designer's real file</figcaption></figure>
          </div>
        </section>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{esc(brand)} visual comparison</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #1a1a1e; color: #eee; margin: 0; padding: 24px; }}
  h1 {{ font-weight: 600; }}
  .card {{ background: #24242a; border-radius: 10px; padding: 16px 20px; margin-bottom: 20px;
           border-left: 6px solid #666; }}
  .card.pass {{ border-left-color: #3ecf5f; }}
  .card.close {{ border-left-color: #e0b94c; }}
  .card.fail {{ border-left-color: #e0524c; }}
  .card.outlier {{ border-left-color: #7a7a86; }}
  .card h2 {{ margin: 0 0 4px; font-size: 15px; font-weight: 600; color: #fff; }}
  .meta {{ color: #aaa; font-size: 13px; margin: 0 0 12px; }}
  .badge {{ padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; }}
  .badge.pass {{ background: #163; color: #6f6; }}
  .badge.close {{ background: #542; color: #fd6; }}
  .badge.fail {{ background: #522; color: #f88; }}
  .badge.outlier {{ background: #333; color: #bbb; }}
  .outlier-note {{ color: #d9a; font-size: 12px; margin: -6px 0 12px; }}
  .pair {{ display: flex; gap: 16px; flex-wrap: wrap; }}
  .pair figure {{ margin: 0; }}
  .pair img {{ max-width: 100%; border: 1px solid #444; border-radius: 4px; display: block; }}
  .pair figcaption {{ text-align: center; color: #888; font-size: 12px; margin-top: 4px; }}
</style>
</head>
<body>
<h1>{esc(brand)}: ours vs. designer's real file</h1>
<p class="meta">Generated by backend/tools/build_comparison.py from validate_all.py's report.
Master-as-its-own-target sanity check is omitted (trivially identical).</p>
{''.join(card_html)}
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brand")
    args = ap.parse_args()
    build(args.brand)


if __name__ == "__main__":
    main()
