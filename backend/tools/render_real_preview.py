"""Render a real designer .cdr to PNG, read-only (open, export, close - no
save), for the visual comparison sheet (build_comparison.py). Reuses the
same launch/timeout/prompt-suppression plumbing as CorelEngine.

Usage:
    python render_real_preview.py <path.cdr> <out.png> [max_px]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import corel_util  # noqa: E402

CDR_PNG = 802
CDR_CURRENT_PAGE = 1
CDR_RGB_IMAGE = 4
CDR_MILLIMETER = 3


def render(cdr_path: Path, out_png: Path, max_px: int = 1600) -> None:
    import pythoncom

    pythoncom.CoInitialize()
    app, we_launched_it, pid = corel_util.dispatch_corel()
    doc = None
    try:
        doc = corel_util.run_with_timeout(
            lambda: app.OpenDocument(str(cdr_path.resolve())), pid, "OpenDocument",
        )
        doc.Unit = CDR_MILLIMETER
        page = doc.ActivePage
        page_w, page_h = float(page.SizeWidth), float(page.SizeHeight)
        longest_in = max(page_w, page_h) / 25.4
        dpi = max(36, min(150, max_px / longest_in))

        def _export():
            flt = doc.ExportBitmap(
                str(out_png.resolve()), CDR_PNG, CDR_CURRENT_PAGE, CDR_RGB_IMAGE,
                0, 0, dpi, dpi, 1, False, False, True, False, 0, None, None,
            )
            flt.Finish()

        corel_util.run_with_timeout(_export, pid, "ExportBitmap")
    finally:
        if doc is not None:
            try:
                doc.Close()
            except Exception:
                pass
        corel_util.quit_corel(app, we_launched_it, pid)
        pythoncom.CoUninitialize()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    render(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 1600)
    print(f"-> {sys.argv[2]}")


if __name__ == "__main__":
    main()
