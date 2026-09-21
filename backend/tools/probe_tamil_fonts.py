"""Targeted, single-file Tamil font probe via CorelDRAW COM.

Opens ONE real board (the dalmia master, read-only intent - the document is
never SaveAs'd or saved over; `doc.Close()` at the end discards any in-memory
edits, exactly like dump_objects.py's read-only pattern), finds its existing
Tamil shopname text shape, then - one font at a time, sequentially, never in
parallel - overwrites that shape's text with a fixed Tamil test string and
tries each candidate font, reading back what actually stuck (CorelDRAW does
not raise on an unrecognized font name; it silently keeps the previous one -
see CLAUDE.md "Shop name replacement" - so the read-back, not the absence of
an exception, is the real signal) and exporting a small PNG of just that
shape via `ExportBitmap` (`cdrSelection`) so the render can be looked at by
eye, not just measured.

Never touches signage_dataset/ - the source file is opened, read from and
written to only in CorelDRAW's in-memory document, then closed unsaved.
Output PNGs/report go under backend/dataset_analysis/tamil_font_probe/.

Uses corel_util directly (dispatch/run_with_timeout/quit), the same
lightweight direct-COM pattern dump_objects.py uses - this is a single
targeted probe, not a multi-job batch, so the heavier worker-subprocess
supervisor (corel_supervisor.run_batch) is not needed here; run_with_timeout
still gives every individual COM call its own hard timeout and force-kill
recovery.

RAM protocol: refuses to even launch CorelDRAW if free RAM is below
`--min-ram-gb` (default 1.5, the same floor corel_supervisor.run_batch
enforces elsewhere). Fonts are tried strictly one at a time in a single
document session (opening/closing CorelDRAW once, not once per font, is
still "one CorelDRAW job at a time" - there is only ever one CorelDRAW
instance alive during this whole probe).

Usage:
    python tools/probe_tamil_fonts.py [--min-ram-gb 1.5] [--timeout 120]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import corel_util  # noqa: E402
from app.fonts import installed_fonts  # noqa: E402

DATASET = ROOT.parent / "signage_dataset"
SOURCE_FILE = DATASET / "dalmia" / "02 - 120 X 48 Inch - 2 Nos Double Side GSB - SRI KAVI STEELS.cdr"
OUT_ROOT = ROOT / "dataset_analysis" / "tamil_font_probe"
CDR_MILLIMETER = 3
CDR_TEXT_SHAPE = 6
CDR_PNG = 802
CDR_SELECTION = 2
CDR_RGB_COLOR_IMAGE = 4

# Always-tried candidates (task requirement) plus whatever locally-installed
# fonts look Tamil/Indic-capable by name, so a font present on THIS machine
# but not in the fixed list still gets exercised.
BASE_CANDIDATES = ["Nirmala UI", "Noto Sans Tamil", "Latha"]
TEST_TEXT = "தமிழ் பரிசோதனை"  # "Tamil test" - fixed sample string, same for every font tried


def _extra_local_tamil_fonts(installed: list[str]) -> list[str]:
    keywords = ("tamil", "nirmala", "latha", "vijaya", "shruti", "gautami", "kartika", "tunga", "mangal", "indic")
    found = [f for f in installed if any(k in f.lower() for k in keywords)]
    return [f for f in found if f not in BASE_CANDIDATES]


def _find_tamil_shopname_shape(page):
    """First text shape anywhere on the page whose current content contains a
    Tamil-range codepoint (U+0B80-U+0BFF) - avoids depending on any specific
    master's known shop name string, so this works against any board.
    """
    def walk(shapes):
        for i in range(1, shapes.Count + 1):
            s = shapes.Item(i)
            if int(s.Type) == CDR_TEXT_SHAPE:
                try:
                    text = s.Text.Story.Text
                except Exception:
                    text = ""
                if any(0x0B80 <= ord(ch) <= 0x0BFF for ch in text or ""):
                    return s
            try:
                children = s.Shapes
            except Exception:
                children = None
            if children is not None and children.Count > 0:
                found = walk(children)
                if found is not None:
                    return found
        return None

    for i in range(1, page.Layers.Count + 1):
        layer = page.Layers.Item(i)
        found = walk(layer.Shapes)
        if found is not None:
            return found
    return None


def probe(min_ram_gb: float, timeout: float) -> dict:
    free = corel_util.check_memory()
    if free < min_ram_gb:
        return {"status": "refused", "reason": f"free RAM {free:.2f} GB is below the {min_ram_gb:.1f} GB floor"}

    installed = installed_fonts()
    candidates = list(BASE_CANDIDATES) + _extra_local_tamil_fonts(installed["fonts"])

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    app, launched, pid = corel_util.dispatch_corel()
    results = []
    try:
        doc = corel_util.run_with_timeout(
            lambda: app.OpenDocument(str(SOURCE_FILE.resolve())), pid, "OpenDocument", timeout,
        )
        try:
            doc.Unit = CDR_MILLIMETER
            page = doc.ActivePage
            shape = _find_tamil_shopname_shape(page)
            if shape is None:
                return {"status": "error", "reason": "no Tamil-content text shape found on this board"}

            for font in candidates:
                entry = {"font": font, "installed": font in installed["fonts"]}
                try:
                    def _apply(_font=font):
                        shape.Text.Story.Text = TEST_TEXT
                        shape.Text.Story.Font = _font
                        return None

                    corel_util.run_with_timeout(_apply, pid, f"set_font[{font}]", timeout)
                    font_readback = shape.Text.Story.Font
                    w_mm, h_mm = float(shape.SizeWidth), float(shape.SizeHeight)
                    entry["font_readback"] = font_readback
                    entry["stuck"] = (font_readback == font)
                    entry["width_mm"] = round(w_mm, 2)
                    entry["height_mm"] = round(h_mm, 2)
                    entry["render_success"] = entry["stuck"] and w_mm > 0.0 and h_mm > 0.0

                    safe = "".join(c if c.isalnum() else "_" for c in font)
                    png_path = OUT_ROOT / f"{safe}.png"

                    def _export(_shape=shape, _png=png_path):
                        # Exact call shape verified working in app/scene_export.py's
                        # _export_leaf - the two trailing None args are required
                        # (PaletteOptions/ExportArea are VT_DISPATCH-typed optional
                        # params; passing 0 instead of None raises a pywin32
                        # wrapper TypeError, not a COM error - see CLAUDE.md).
                        doc.ClearSelection()
                        _shape.AddToSelection()
                        px_w = max(64, min(2000, round(_shape.SizeWidth / 25.4 * 96)))
                        px_h = max(64, min(2000, round(_shape.SizeHeight / 25.4 * 96)))
                        export_filter = doc.ExportBitmap(
                            str(_png.resolve()), CDR_PNG, CDR_SELECTION, CDR_RGB_COLOR_IMAGE,
                            px_w, px_h, 96, 96, 1, False, True, True, False, 0, None, None,
                        )
                        export_filter.Finish()
                        doc.ClearSelection()

                    corel_util.run_with_timeout(_export, pid, f"export[{font}]", timeout)
                    entry["png"] = str(png_path.relative_to(ROOT))
                    entry["export_success"] = png_path.exists() and png_path.stat().st_size > 0
                except Exception as e:
                    entry["error"] = str(e)
                    entry.setdefault("render_success", False)
                    entry.setdefault("export_success", False)
                results.append(entry)
                print(f"  {font}: installed={entry['installed']} stuck={entry.get('stuck')} "
                      f"width_mm={entry.get('width_mm')} render_success={entry.get('render_success')} "
                      f"export_success={entry.get('export_success')}")
        finally:
            try:
                doc.Close()  # never SaveAs / save - discards the in-memory font/text edits
            except Exception:
                pass
    finally:
        corel_util.quit_corel(app, launched, pid)

    return {"status": "done", "source_file": SOURCE_FILE.name, "test_text": TEST_TEXT,
            "installed_fonts_source": installed["source"], "results": results}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-ram-gb", type=float, default=1.5)
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    report = probe(args.min_ram_gb, args.timeout)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    out_path = OUT_ROOT / "report.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if report["status"] != "done":
        print(report)
        sys.exit(1)
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()
