"""Benchmark CorelDRAW's .cdr SaveAs options (StructSaveAsOptions) on scratch copies of generated boards.

Run from backend/:  ..\\.venv\\Scripts\\python.exe tests\\benchmark_saveas.py [board.cdr ...]
Not a pytest test (no test_ prefix): it needs a live CorelDRAW on Windows and takes a few minutes.

For every variant it measures doc.SaveAs time (REPS saves per variant, interleaved round-robin so drift in disk cache / RAM
hits every variant alike), then validates the first file it wrote:
  - format: form type CDRM and CoreVersion 2100 (CorelDRAW 2019, v21) via corel_util.cdr_file_format;
  - content: the file is reopened and rendered to PNG at a fixed size, and compared pixel by pixel with the render of the
    untouched scratch copy;
  - zip members the app itself reads (previews/page1.png - main._extract_cdr_preview's instant master preview).
Only files under a temp scratch directory are opened or written; the source boards are copied first and never opened.
The CorelDRAW instance is launched and quit through corel_util (tracked pid, prompt suppression).
"""
from __future__ import annotations

import json
import shutil
import statistics
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import corel_util  # noqa: E402

REPS = 3
RENDER_PX = 1600          # longest side of the comparison render
CDR_PNG, CDR_CURRENT_PAGE, CDR_RGB = 802, 1, 4

DEFAULT_BOARDS = [
    "data/jobs_v2/366946392a73/out/07385292d840/Shop_1.cdr",   # ~9 MB dalmia board (the common case)
    "data/jobs_v2/43ddf0d9e704/out/638555963892/Shop_1.cdr",   # ~125 MB, one large embedded bitmap
]

# every variant also gets Version=21 and Overwrite=True - exactly what save_cdr() sets today ("baseline")
VARIANTS = {
    "baseline": {},
    "cmx_off": {"IncludeCMXData": False},
    "thumb_none": {"ThumbnailSize": 0},        # cdrNoThumbnail
    "thumb_1k_mono": {"ThumbnailSize": 1},     # cdr1KMonoThumbnail
    "icc_off": {"EmbedICCProfile": False},
    "vba_off": {"EmbedVBAProject": False},
    "keep_appearance_off": {"KeepAppearance": False},
    "keep_appearance_on": {"KeepAppearance": True},
}
OPTION_NAMES = ["IncludeCMXData", "ThumbnailSize", "EmbedICCProfile", "EmbedVBAProject", "KeepAppearance", "Filter", "Range"]


def make_opts(app, extra: dict):
    o = app.CreateStructSaveAsOptions()
    o.Version = 21
    o.Overwrite = True
    for k, v in extra.items():
        setattr(o, k, v)
    return o


def render(doc, path: Path) -> np.ndarray:
    w, h = doc.ActivePage.SizeWidth, doc.ActivePage.SizeHeight
    scale = RENDER_PX / max(w, h)
    px_w, px_h = max(1, round(w * scale)), max(1, round(h * scale))
    flt = doc.ExportBitmap(str(path), CDR_PNG, CDR_CURRENT_PAGE, CDR_RGB, px_w, px_h, 72, 72, 1,
                           False, False, True, False, 0, None, None)
    flt.Finish()
    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.int16)


def compare(a: np.ndarray, b: np.ndarray) -> dict:
    if a.shape != b.shape:
        return {"identical": False, "note": f"size {b.shape} vs {a.shape}"}
    d = np.abs(a - b)
    return {"identical": bool(d.max() == 0), "max_diff": int(d.max()), "mean_diff": round(float(d.mean()), 4),
            "pct_px_changed": round(100.0 * float((d.max(axis=2) > 0).mean()), 4)}


def bench_board(app, pid, src: Path, work: Path) -> dict:
    scratch = work / "source_copy.cdr"
    shutil.copy2(src, scratch)                       # the only file opened; `src` itself is never touched
    doc = corel_util.run_with_timeout(lambda: app.OpenDocument(str(scratch)), pid, "open")
    try:
        doc.Unit = 3                                  # cdrMillimeter
        base_render = render(doc, work / "render_source.png")
        defaults = {}
        fresh = app.CreateStructSaveAsOptions()
        for n in OPTION_NAMES:
            try:
                defaults[n] = getattr(fresh, n)
            except Exception as e:                    # noqa: BLE001
                defaults[n] = f"<unreadable: {e}>"
    finally:
        doc.Close()

    # Timed saves: production saves each document ONCE, right after opening + editing it, so every timed save is the
    # first save of a freshly opened copy that has been made dirty by a no-op edit (a shape moved 1 mm and back - the
    # rendered result is unchanged). Variants are interleaved round-robin across reps.
    times = {v: [] for v in VARIANTS}
    firsts = {}
    for rep in range(REPS):
        for name, extra in VARIANTS.items():
            out = work / f"{name}_{rep}.cdr"
            d = corel_util.run_with_timeout(lambda: app.OpenDocument(str(scratch)), pid, f"open {name}")
            try:
                d.Unit = 3
                shapes = d.ActivePage.Shapes
                if shapes.Count:
                    s0 = shapes.Item(1)
                    s0.Move(1.0, 0.0)
                    s0.Move(-1.0, 0.0)
                opts = make_opts(app, extra)
                t0 = time.perf_counter()
                corel_util.run_with_timeout(lambda: d.SaveAs(str(out), opts), pid, f"saveas {name}")
                times[name].append(time.perf_counter() - t0)
            finally:
                d.Close()
            if rep == 0:
                firsts[name] = out
            else:
                out.unlink(missing_ok=True)       # timing-only copy (a 125 MB board x 8 variants x 3 reps adds up)

    results = {}
    for name, out in firsts.items():
        fmt = corel_util.cdr_file_format(out)
        with zipfile.ZipFile(out) as z:
            members = {i.filename: i.file_size for i in z.infolist()}
        d2 = corel_util.run_with_timeout(lambda: app.OpenDocument(str(out)), pid, f"reopen {name}")
        try:
            d2.Unit = 3
            cmp = compare(base_render, render(d2, work / f"render_{name}.png"))
        finally:
            d2.Close()
        results[name] = {
            "median_s": round(statistics.median(times[name]), 3),
            "runs_s": [round(t, 3) for t in times[name]],
            "size_mb": round(out.stat().st_size / 1e6, 2),
            "format": fmt,
            "v21_ok": fmt.get("form") == "CDRM" and fmt.get("version") == 2100,
            "has_page_preview": "previews/page1.png" in members,
            "has_thumbnail": "previews/thumbnail.png" in members,
            "render": cmp,
        }
    return {"source": str(src), "source_mb": round(src.stat().st_size / 1e6, 2), "defaults": defaults, "variants": results}


def print_table(board: dict) -> None:
    base = board["variants"]["baseline"]["median_s"]
    print(f"\n{board['source']}  ({board['source_mb']} MB)")
    print(f"  fresh StructSaveAsOptions defaults: {board['defaults']}")
    print(f"  {'variant':22s} {'median s':>9s} {'vs base':>8s} {'MB':>8s}  v21  page1.png  render")
    for name, r in board["variants"].items():
        pct = 100.0 * (r["median_s"] - base) / base if base else 0.0
        ren = "identical" if r["render"].get("identical") else f"DIFF {r['render']}"
        print(f"  {name:22s} {r['median_s']:9.3f} {pct:+7.1f}% {r['size_mb']:8.2f}  {'yes' if r['v21_ok'] else 'NO '}  "
              f"{'yes' if r['has_page_preview'] else 'NO ':9s}  {ren}")


def main(argv: list[str]) -> int:
    boards = [Path(p) for p in (argv or DEFAULT_BOARDS)]
    for b in boards:
        if "signage_dataset" in b.resolve().parts:
            raise SystemExit(f"refusing to benchmark a file under signage_dataset/: {b}")
        if not b.is_file():
            raise SystemExit(f"not found: {b}")
    print(f"free RAM: {corel_util.check_memory():.2f} GB")
    corel_util.ensure_com()
    app, launched, pid = corel_util.acquire_instance()
    if not launched:
        raise SystemExit("attached to a CorelDRAW that was already running - close it and rerun (a benchmark must not drive it)")
    out_all, ok = [], False
    root = Path(tempfile.mkdtemp(prefix="saveas_bench_"))
    try:
        for i, b in enumerate(boards):
            work = root / f"board{i}"
            work.mkdir()
            res = bench_board(app, pid, b, work)
            out_all.append(res)
            print_table(res)
        ok = True
    finally:
        del app                                      # drop our COM reference first: CorelDRAW only exits once the last one goes
        corel_util.release_instance(pid, ok)
        corel_util.wait_for_pending_quits()
        (root / "results.json").write_text(json.dumps(out_all, indent=2))
        print(f"\nresults: {root / 'results.json'} (scratch files kept there for inspection)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
