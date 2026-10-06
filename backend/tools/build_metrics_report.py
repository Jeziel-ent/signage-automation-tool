"""Phase 1 deliverable: an HTML side-by-side report (ours vs. designer's
real file) for every board in a brand's validate_all.py report, with every
metrics.py score and pass/fail flag next to the images - this is the
artifact GATE 1 asks the user to review by eye before any threshold in
metrics_config.json is treated as calibrated.

Reads:
  - dataset_analysis/validation/<brand>/validation_report.json (target
    sizes, ours_png/real_png paths, our tiling decision)
  - dataset_analysis/ours_dumps_cache/<brand>/<safe>.json (cache_ours_dumps.py)
  - dataset_analysis/real_dumps_cache/<brand>/<safe>.json (already produced
    by validate_all.py)
  - the PNGs at ours_png/real_png (cache_real_renders.py renders the real
    ones; ours are already there from generation)

A board missing its real PNG or either shape dump still gets a card, with
whichever metric categories are available and a note on what's missing -
this report should never crash because one board's cache is incomplete.

Offline - no CorelDRAW, just PIL/numpy/scipy. Run after validate_all.py +
cache_ours_dumps.py + cache_real_renders.py.

Usage:
    python build_metrics_report.py dalmia
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

from tools import metrics  # noqa: E402
from tools.cache_ours_dumps import OURS_DUMPS_CACHE_ROOT  # noqa: E402
from tools.validate_all import (  # noqa: E402
    BRAND_MASTER, REAL_DUMPS_CACHE_ROOT, VALIDATION_ROOT, _bucket_role, _flatten_leaves,
)

REPORT_ROOT = ROOT / "dataset_analysis" / "metrics_report"
DISPLAY_WIDTH = 900


def _resize_to_width(src: Path, dst: Path, width: int = DISPLAY_WIDTH) -> None:
    img = Image.open(src).convert("RGB")
    w, h = img.size
    new_h = max(1, round(h * width / w))
    img.resize((width, new_h), Image.LANCZOS).save(dst)


def build_cards(brand: str, out_root: Path | None = None, want_images: bool = True) -> tuple[list[dict], dict]:
    """The per-board metric computation shared by the HTML report (`build`,
    below) and `calibrate_threshold.py` (Step 3 - which only needs the
    numbers, not the resized thumbnail files `want_images` controls).
    Returns (cards, config).
    """
    report = json.loads((VALIDATION_ROOT / brand / "validation_report.json").read_text(encoding="utf-8"))
    cfg = BRAND_MASTER[brand]
    shopname_hints = [cfg["shop_name"], cfg["shop_name_local"]]
    config = metrics.load_config()
    out_root = out_root or (REPORT_ROOT / brand)
    if want_images:
        out_root.mkdir(parents=True, exist_ok=True)

    cards = []
    for b in report["boards"]:
        if "error" in b or abs(b.get("diff_pct", {}).get("max", 1)) < 1e-9:
            continue  # master-as-its-own-target sanity check, not a real comparison
        if "ours_png" not in b or "real_png" not in b:
            continue

        safe = Path(b["ours_png"]).parent.name
        card = {
            "file": b["file"], "shop": b["shop_name"], "safe": safe,
            "target": f"{b['target_mm']['w']:.0f}x{b['target_mm']['h']:.0f}mm",
            "tile": f"{b['our_tile']['axis']},{b['our_tile']['n']}",
            "geometry_max_diff_pct": b["diff_pct"]["max"],
            "content_check": b.get("content_check"),
        }
        page_w, page_h = b["target_mm"]["w"], b["target_mm"]["h"]

        ours_png_path, real_png_path = ROOT / b["ours_png"], ROOT / b["real_png"]
        if ours_png_path.exists() and real_png_path.exists():
            try:
                if want_images:
                    card_dir = out_root / safe
                    card_dir.mkdir(parents=True, exist_ok=True)
                    _resize_to_width(ours_png_path, card_dir / "ours.png")
                    _resize_to_width(real_png_path, card_dir / "real.png")
                    card["ours_rel"], card["real_rel"] = f"{safe}/ours.png", f"{safe}/real.png"
                card["visual"] = metrics.visual_similarity(ours_png_path, real_png_path, config)
            except Exception as e:
                card["visual_error"] = str(e)
        else:
            card["visual_error"] = "real PNG not rendered yet - run cache_real_renders.py"

        ours_dump_path = OURS_DUMPS_CACHE_ROOT / brand / f"{safe}.json"
        real_dump_path = REAL_DUMPS_CACHE_ROOT / brand / f"{safe}.json"
        if ours_dump_path.exists() and real_dump_path.exists():
            ours_dump = json.loads(ours_dump_path.read_text(encoding="utf-8"))
            real_dump = json.loads(real_dump_path.read_text(encoding="utf-8"))
            ours_shapes = _bucket_role(_flatten_leaves(ours_dump), page_w, page_h, shopname_hints)
            real_shapes = _bucket_role(_flatten_leaves(real_dump), page_w, page_h, shopname_hints)
            card["clusters"] = metrics.cluster_compare(ours_shapes, real_shapes, page_w, page_h, config)
            card["geometric"] = metrics.geometric_accuracy(ours_shapes, real_shapes, config)
            card["counts"] = metrics.counts_compare(ours_shapes, real_shapes)
            card["layout_checks"] = metrics.layout_checks(ours_shapes, page_w, page_h, config)
        else:
            card["cluster_error"] = "shape dumps not cached - run cache_ours_dumps.py"

        cards.append(card)

    return cards, config


def build(brand: str) -> Path:
    out_root = REPORT_ROOT / brand
    cards, config = build_cards(brand, out_root, want_images=True)
    (out_root / "index.html").write_text(_render_html(brand, cards, config), encoding="utf-8")
    n_visual = sum(1 for c in cards if "visual" in c)
    n_cluster = sum(1 for c in cards if "clusters" in c)
    print(f"{len(cards)} boards -> {out_root / 'index.html'} ({n_visual} with visual metrics, {n_cluster} with cluster metrics)")
    return out_root / "index.html"


def _render_html(brand: str, cards: list[dict], config: dict) -> str:
    def esc(s):
        return html.escape(str(s)) if s is not None else "-"

    vc = config["visual"]

    def _content_failed(c):
        cc = c.get("content_check")
        return bool(cc and cc["overall"] == "CONTENT_FAIL")

    def _passes(c):
        if any(chk["status"] == "fail" for chk in c.get("layout_checks", [])):
            return False
        if _content_failed(c):
            return False
        return bool(c.get("visual", {}).get("pass"))

    n_pass = sum(1 for c in cards if "visual" in c and _passes(c))
    n_scored = sum(1 for c in cards if "visual" in c)
    n_content_fail = sum(1 for c in cards if _content_failed(c))
    n_content_checked = sum(1 for c in cards if c.get("content_check") and c["content_check"]["overall"] != "NOT_CHECKED")

    tolerances = config.get("geometric", {}).get("area_match_tolerances_mm", [2.0, 5.0, 10.0])
    summary_rows = []
    for c in cards:
        geo = c.get("geometric")
        cc = c.get("content_check")
        area_cells = "".join(
            "<td>-</td>" if not geo else
            f"<td>{'-' if r['pct'] is None else format(r['pct'], '.0f') + '%'}</td>"
            for r in ((geo["area_matched_pct"] if geo else [{"pct": None}] * len(tolerances)))
        )
        content_cell = esc(cc["overall"]) if cc else "-"
        content_cls = {"CONTENT_OK": "check-pass", "CONTENT_FAIL": "check-fail", "NOT_CHECKED": "check-warn"}.get(
            cc["overall"] if cc else "", "")
        summary_rows.append(f"""
        <tr>
          <td><a href="#{esc(c['safe'])}">{esc(c['shop'])}</a></td>
          <td>{esc(c['target'])}</td>
          <td>{'-' if 'visual' not in c else f"{c['visual']['combined']:.3f}"}</td>
          <td>{'-' if not geo else f"{geo['position_error_mm']['max']:.1f}"}</td>
          <td>{'-' if not geo else f"{geo['size_error_mm']['max']:.1f}"}</td>
          {area_cells}
          <td class="{content_cls}">{content_cell}</td>
        </tr>""")
    summary_table_html = f"""
    <table class="metric-table summary-table">
      <tr>
        <th>shop</th><th>target</th><th>visual combined</th>
        <th>max position error (mm)</th><th>max size error (mm)</th>
        {"".join(f"<th>area within {t:g}mm</th>" for t in tolerances)}
        <th>content check</th>
      </tr>
      {"".join(summary_rows)}
    </table>"""

    card_html = []
    for c in cards:
        visual = c.get("visual")
        hard_fail = any(chk["status"] == "fail" for chk in c.get("layout_checks", [])) or _content_failed(c)
        if hard_fail:
            badge = "fail"
        elif visual:
            badge = "pass" if visual["pass"] else "fail"
        else:
            badge = "unscored"
        header = (
            f'<p class="meta">{esc(c["shop"])} &middot; target {esc(c["target"])} &middot; '
            f'tile {esc(c["tile"])} &middot; geometry max diff {c["geometry_max_diff_pct"]:.1f}% &middot; '
            f'<span class="badge {badge}">{badge.upper()}</span></p>'
        )

        if visual:
            visual_html = f"""
            <table class="metric-table">
              <tr><th>SSIM</th><td>{visual['ssim']:.3f}</td>
                  <th>phash similarity</th><td>{visual['phash_similarity']:.3f} (Hamming {visual['phash_hamming']})</td></tr>
              <tr><th>edge similarity</th><td>{visual['edge_similarity']:.3f}</td>
                  <th>combined</th><td><b>{visual['combined']:.3f}</b> (threshold {vc['pass_threshold']})</td></tr>
            </table>"""
        else:
            visual_html = f'<p class="err">{esc(c.get("visual_error"))}</p>'

        geo = c.get("geometric")
        if geo:
            area_cells = "".join(f"<th>within {row['tolerance_mm']:g}mm</th>" for row in geo["area_matched_pct"])
            area_vals = "".join(
                f"<td>{'-' if row['pct'] is None else format(row['pct'], '.0f') + '%'}</td>"
                for row in geo["area_matched_pct"]
            )
            geo_html = f"""
            <table class="metric-table">
              <tr><th>position error (mm)</th><td>max {geo['position_error_mm']['max']:.1f}, mean {geo['position_error_mm']['mean']:.1f}</td>
                  <th>size error (mm)</th><td>max {geo['size_error_mm']['max']:.1f}, mean {geo['size_error_mm']['mean']:.1f}</td></tr>
              <tr>{area_cells}</tr>
              <tr>{area_vals}</tr>
            </table>
            <p class="meta">area matched % = share of the real file's total cluster area whose matched cluster has
            BOTH position and size error within that tolerance (metrics_config.json's "geometric" section).</p>"""
        else:
            geo_html = '<p class="err">no geometric accuracy data (needs both shape dumps)</p>'

        if "clusters" in c:
            cl, ct = c["clusters"], c["counts"]
            diff_rows = "".join(
                f"<tr><td>{esc(d['role'])}</td><td>{d['dx_pct']:.1f}</td><td>{d['dy_pct']:.1f}</td>"
                f"<td>{d['dw_pct']:.1f}</td><td>{d['dh_pct']:.1f}</td><td>{d['max_pct']:.1f}</td></tr>"
                for d in cl["diffs"]
            )
            cluster_html = f"""
            <table class="metric-table">
              <tr><th>clusters ours/real</th><td>{cl['ours_clusters']}/{cl['real_clusters']}</td>
                  <th>matched</th><td>{cl['matched']} (unmatched {cl['unmatched_ours']}/{cl['unmatched_real']})</td></tr>
              <tr><th>shapes ours/real</th><td>{ct['ours_shapes']}/{ct['real_shapes']}</td>
                  <th>text shapes ours/real</th><td>{ct['ours_text']}/{ct['real_text']}</td></tr>
              <tr><th>max cluster diff</th><td colspan="3">{'-' if cl['max_diff_pct'] is None else f"{cl['max_diff_pct']:.1f}%"}</td></tr>
            </table>
            <details><summary>per-cluster diffs ({len(cl['diffs'])})</summary>
              <table class="metric-table"><tr><th>role</th><th>dx%</th><th>dy%</th><th>dw%</th><th>dh%</th><th>max%</th></tr>
              {diff_rows}</table>
            </details>"""
            checks_html = "".join(
                f'<li class="check-{chk["status"]}">{esc(chk["check"])}: {chk["status"].upper()} - {esc(chk["detail"])}</li>'
                for chk in c.get("layout_checks", [])
            )
            cluster_html += f'<ul class="checks">{checks_html}</ul>'
        else:
            cluster_html = f'<p class="err">{esc(c.get("cluster_error"))}</p>'

        cc = c.get("content_check")
        if cc:
            def _row(label, f):
                cls = {"CONTENT_OK": "check-pass", "CONTENT_FAIL": "check-fail", "NOT_CHECKED": "check-warn"}[f["status"]]
                return (f'<tr class="{cls}"><td>{esc(label)}</td><td>{esc(f["status"])}</td>'
                        f'<td>{esc(f["expected"])}</td><td>{esc(f["found"])}</td></tr>')

            if cc["overall"] == "CONTENT_OK":
                overall_cls = "pass"
            elif cc["overall"] == "CONTENT_FAIL":
                overall_cls = "fail"
            else:
                overall_cls = "unscored"
            content_html = f"""
            <table class="metric-table">
              <tr><th>field</th><th>status</th><th>expected</th><th>found</th></tr>
              {_row("shop name", cc["shop_name"])}
              {_row("phone", cc["phone"])}
              {_row("gst", cc["gst"])}
            </table>
            <p class="meta">overall: <span class="badge {overall_cls}">{esc(cc['overall'])}</span></p>"""
        else:
            content_html = '<p class="err">no content check recorded for this board</p>'

        images_html = ""
        if "ours_rel" in c:
            images_html = f"""
            <div class="pair">
              <figure><img src="{esc(c['ours_rel'])}" alt="ours"><figcaption>ours</figcaption></figure>
              <figure><img src="{esc(c['real_rel'])}" alt="designer's real file"><figcaption>designer's real file</figcaption></figure>
            </div>"""

        card_html.append(f"""
        <section class="card {badge}" id="{esc(c['safe'])}">
          <h2>{esc(c['file'])}</h2>
          {header}
          <div class="grid">
            <div class="col">{images_html}</div>
            <div class="col">
              <h3>visual similarity</h3>{visual_html}
              <h3>geometric accuracy (mm)</h3>{geo_html}
              <h3>content check</h3>{content_html}
              <h3>cluster / count / layout</h3>{cluster_html}
            </div>
          </div>
        </section>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{esc(brand)} metrics report</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #1a1a1e; color: #eee; margin: 0; padding: 24px; }}
  h1 {{ font-weight: 600; }}
  .summary {{ color: #ccc; margin-bottom: 20px; }}
  .card {{ background: #24242a; border-radius: 10px; padding: 16px 20px; margin-bottom: 24px; border-left: 6px solid #666; }}
  .card.pass {{ border-left-color: #3ecf5f; }}
  .card.fail {{ border-left-color: #e0524c; }}
  .card.unscored {{ border-left-color: #7a7a86; }}
  .card h2 {{ margin: 0 0 4px; font-size: 15px; font-weight: 600; color: #fff; }}
  .card h3 {{ font-size: 13px; color: #aaa; margin: 14px 0 6px; text-transform: uppercase; letter-spacing: .04em; }}
  .meta {{ color: #aaa; font-size: 13px; margin: 0 0 10px; }}
  .badge {{ padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; }}
  .badge.pass {{ background: #163; color: #6f6; }}
  .badge.fail {{ background: #522; color: #f88; }}
  .badge.unscored {{ background: #333; color: #bbb; }}
  .grid {{ display: flex; gap: 24px; flex-wrap: wrap; }}
  .grid .col {{ flex: 1 1 380px; min-width: 320px; }}
  .pair {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .pair figure {{ margin: 0; flex: 1 1 260px; }}
  .pair img {{ max-width: 100%; border: 1px solid #444; border-radius: 4px; display: block; }}
  .pair figcaption {{ text-align: center; color: #888; font-size: 12px; margin-top: 4px; }}
  .metric-table {{ border-collapse: collapse; font-size: 12px; width: 100%; margin-bottom: 6px; }}
  .metric-table th {{ text-align: left; color: #999; font-weight: 500; padding: 3px 8px 3px 0; white-space: nowrap; }}
  .metric-table td {{ padding: 3px 12px 3px 0; color: #eee; }}
  details summary {{ cursor: pointer; color: #9cf; font-size: 12px; margin: 4px 0; }}
  .checks {{ list-style: none; padding: 0; margin: 8px 0 0; font-size: 12px; }}
  .checks li {{ padding: 2px 0; }}
  .check-pass {{ color: #6f6; }}
  .check-warn {{ color: #fd6; }}
  .check-fail {{ color: #f88; }}
  .err {{ color: #d9a; font-size: 12px; }}
  .summary-table {{ margin-bottom: 24px; }}
  .summary-table th, .summary-table td {{ padding: 4px 10px; border-bottom: 1px solid #333; }}
  .summary-table a {{ color: #9cf; text-decoration: none; }}
</style>
</head>
<body>
<h1>{esc(brand)}: Phase 1 metrics report</h1>
<p class="summary">
  Generated by backend/tools/build_metrics_report.py. Visual PASS threshold (combined SSIM + phash + edge
  score &ge; {vc['pass_threshold']}, weights {vc['ssim_weight']}/{vc['phash_weight']}/{vc['edge_weight']}) is an
  <b>uncalibrated default</b> in backend/tools/metrics_config.json - not yet confirmed by eye.
  {n_pass}/{n_scored} boards currently pass it. Master-as-its-own-target sanity check is omitted.
  Content check (does the generated text actually say what was requested - shop name / phone / GST):
  {n_content_fail} board(s) CONTENT_FAIL out of {n_content_checked} with at least one field actually checked;
  a CONTENT_FAIL also flips that board's overall badge to FAIL regardless of its visual score.
</p>
<h2>Summary: all {len(cards)} boards</h2>
{summary_table_html}
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
