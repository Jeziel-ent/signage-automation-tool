"""Step 3: a standalone, click-to-label HTML page for calibrate_threshold.py.

Generates dataset_analysis/metrics_report/<brand>/label.html - open it
directly in a browser (no server needed; images are referenced the same way
build_metrics_report.py's index.html does, so run that first). Click OK /
NOT OK per board by eye, then "Download labels.json" and save it as
dataset_analysis/labels/<brand>_labels.json - that's what
calibrate_threshold.py reads.

Re-running this script after you've already labelled some boards preloads
your previous choices (it reads that same labels.json back in, if present),
so labelling can be done in more than one sitting without starting over.

Usage:
    python tools/build_metrics_report.py dalmia   # first, if not already done
    python tools/build_label_page.py dalmia
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.build_metrics_report import REPORT_ROOT, build_cards  # noqa: E402
from tools.calibrate_threshold import LABELS_ROOT  # noqa: E402


def build(brand: str) -> Path:
    # want_images=True: this page needs the resized ours/real thumbnails to exist and be
    # referenced (build_metrics_report.py's own images/index.html live in the same directory,
    # so this reuses them - re-resizing is cheap, well under a second for a dozen boards).
    cards, _ = build_cards(brand, REPORT_ROOT / brand, want_images=True)
    cards = [c for c in cards if "ours_rel" in c]

    labels_path = LABELS_ROOT / f"{brand}_labels.json"
    existing = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}

    out_root = REPORT_ROOT / brand
    out_root.mkdir(parents=True, exist_ok=True)
    out_path = out_root / "label.html"
    out_path.write_text(_render(brand, cards, existing), encoding="utf-8")
    print(f"{len(cards)} boards -> {out_path}")
    print("Open it in a browser, mark each board OK / NOT OK, then click \"Download labels.json\" and save it as")
    print(f"  {labels_path}")
    return out_path


def _render(brand: str, cards: list[dict], existing: dict) -> str:
    def esc(s):
        return html.escape(str(s))

    rows = []
    for c in cards:
        current = existing.get(c["safe"], "")
        rows.append(f"""
        <section class="board" data-safe="{esc(c['safe'])}">
          <h2>{esc(c['file'])}</h2>
          <p class="meta">{esc(c['shop'])} &middot; target {esc(c['target'])} &middot; tile {esc(c['tile'])}</p>
          <div class="pair">
            <figure><img src="{esc(c['ours_rel'])}" loading="lazy"><figcaption>ours</figcaption></figure>
            <figure><img src="{esc(c['real_rel'])}" loading="lazy"><figcaption>designer's real file</figcaption></figure>
          </div>
          <div class="choice" role="group">
            <button type="button" class="ok{' active' if current == 'OK' else ''}" data-value="OK">OK</button>
            <button type="button" class="notok{' active' if current == 'NOT_OK' else ''}" data-value="NOT_OK">NOT OK</button>
            <button type="button" class="clear{' active' if not current else ''}" data-value="">unlabelled</button>
          </div>
        </section>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{esc(brand)} - label boards for threshold calibration</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #1a1a1e; color: #eee; margin: 0; padding: 24px; }}
  h1 {{ font-weight: 600; }}
  .toolbar {{ position: sticky; top: 0; background: #1a1a1e; padding: 10px 0; border-bottom: 1px solid #333;
             display: flex; align-items: center; gap: 16px; z-index: 10; }}
  .toolbar button {{ background: #3a6; color: #fff; border: none; padding: 8px 16px; border-radius: 6px;
                     font-size: 14px; cursor: pointer; }}
  .toolbar button:hover {{ background: #4c8; }}
  #summary {{ color: #ccc; font-size: 13px; }}
  .board {{ background: #24242a; border-radius: 10px; padding: 16px 20px; margin: 20px 0; border-left: 6px solid #666; }}
  .board.labelled-OK {{ border-left-color: #3ecf5f; }}
  .board.labelled-NOT_OK {{ border-left-color: #e0524c; }}
  .board h2 {{ margin: 0 0 4px; font-size: 15px; color: #fff; }}
  .meta {{ color: #aaa; font-size: 13px; margin: 0 0 10px; }}
  .pair {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .pair figure {{ margin: 0; flex: 1 1 380px; }}
  .pair img {{ max-width: 100%; border: 1px solid #444; border-radius: 4px; display: block; }}
  .pair figcaption {{ text-align: center; color: #888; font-size: 12px; margin-top: 4px; }}
  .choice {{ margin-top: 12px; display: flex; gap: 8px; }}
  .choice button {{ padding: 6px 14px; border-radius: 6px; border: 1px solid #555; background: #333; color: #ccc; cursor: pointer; font-size: 13px; }}
  .choice button.ok.active {{ background: #163; border-color: #3ecf5f; color: #6f6; }}
  .choice button.notok.active {{ background: #522; border-color: #e0524c; color: #f88; }}
  .choice button.clear.active {{ background: #444; border-color: #888; color: #ddd; }}
</style>
</head>
<body>
<h1>{esc(brand)}: label boards for threshold calibration (Step 3)</h1>
<div class="toolbar">
  <button id="download">Download labels.json</button>
  <span id="summary"></span>
</div>
<p class="meta">Click OK or NOT OK by eye for each board below, based on the two images. This never talks to a
server - your choices live only in this page until you click Download, then save the file as
<code>backend/dataset_analysis/labels/{esc(brand)}_labels.json</code> and run
<code>python tools/calibrate_threshold.py {esc(brand)}</code>.</p>
{''.join(rows)}
<script>
(function() {{
  var labels = {json.dumps(existing)};

  function summarize() {{
    var boards = document.querySelectorAll('.board');
    var ok = 0, notOk = 0;
    boards.forEach(function(b) {{
      var v = labels[b.dataset.safe];
      b.classList.remove('labelled-OK', 'labelled-NOT_OK');
      if (v === 'OK') {{ ok++; b.classList.add('labelled-OK'); }}
      if (v === 'NOT_OK') {{ notOk++; b.classList.add('labelled-NOT_OK'); }}
    }});
    document.getElementById('summary').textContent =
      ok + ' OK, ' + notOk + ' NOT OK, ' + (boards.length - ok - notOk) + ' unlabelled of ' + boards.length;
  }}

  document.querySelectorAll('.board').forEach(function(board) {{
    var safe = board.dataset.safe;
    board.querySelectorAll('.choice button').forEach(function(btn) {{
      btn.addEventListener('click', function() {{
        var v = btn.dataset.value;
        if (v) labels[safe] = v; else delete labels[safe];
        board.querySelectorAll('.choice button').forEach(function(b) {{ b.classList.toggle('active', b === btn); }});
        summarize();
      }});
    }});
  }});

  document.getElementById('download').addEventListener('click', function() {{
    var blob = new Blob([JSON.stringify(labels, null, 2)], {{ type: 'application/json' }});
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = '{esc(brand)}_labels.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }});

  summarize();
}})();
</script>
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
