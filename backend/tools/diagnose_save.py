"""One-shot diagnostic: run the CorelEngine pipeline step by step, visibly,
against the dalmia master, logging a timestamp before/after each step plus
a live watchdog for any dialog CorelDRAW pops up. Does not click anything.

Writes a live log to backend/dataset_analysis/diagnose/diagnose_save.log
(flushed after every line, so it can be read while a step is still blocked)
and dialog screenshots to backend/dataset_analysis/diagnose/.

Usage:
    python diagnose_save.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("SIGNAGE_COREL_VISIBLE", "1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import corel_util  # noqa: E402
from app.layout import Obj, compute_layout, find_shopname_ids  # noqa: E402
from app.corel_watchdog import Watchdog  # noqa: E402

MASTER = Path(__file__).resolve().parents[2] / "signage_dataset" / "dalmia" / \
    "02 - 120 X 48 Inch - 2 Nos Double Side GSB - SRI KAVI STEELS.cdr"
OUT_DIR = Path(__file__).resolve().parents[1] / "dataset_analysis" / "diagnose"
LOG_PATH = OUT_DIR / "diagnose_save.log"
TARGET_W_IN, TARGET_H_IN = 150.0, 50.0

OUT_DIR.mkdir(parents=True, exist_ok=True)
_log_file = open(LOG_PATH, "w", encoding="utf-8")


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}"
    print(line)
    _log_file.write(line + "\n")
    _log_file.flush()


def check_memory() -> None:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    free_gb = stat.ullAvailPhys / (1024 ** 3)
    total_gb = stat.ullTotalPhys / (1024 ** 3)
    log(f"memory: {free_gb:.2f} GB free / {total_gb:.2f} GB total ({stat.dwMemoryLoad}% used)")
    if free_gb < 2.0:
        log(f"WARNING: less than 2 GB free RAM ({free_gb:.2f} GB) - CorelDRAW dialogs/rendering may be affected")


def check_output_path(path: Path) -> None:
    resolved = str(path.resolve())
    log(f"output path: {resolved} (length {len(resolved)})")
    if len(resolved) >= 260:
        log("WARNING: path length >= 260 chars - may hit Windows MAX_PATH issues")
    if "onedrive" in resolved.lower():
        log("WARNING: path is inside a OneDrive-synced folder - sync locks can block file writes")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        probe = path.parent / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        log("output folder is writable: OK")
    except Exception as e:
        log(f"WARNING: output folder not writable: {e}")


def main():
    log("=== diagnose_save starting ===")
    check_memory()
    out_cdr = OUT_DIR / "diagnose_target.cdr"
    check_output_path(out_cdr)
    log(f"master: {MASTER} ({MASTER.stat().st_size / 1e6:.1f} MB)")

    import pythoncom
    pythoncom.CoInitialize()

    log("STEP launch Corel: start")
    t0 = time.time()
    app, we_launched_it, pid = corel_util.dispatch_corel()
    log(f"STEP launch Corel: done in {time.time()-t0:.1f}s (pid={pid}, we_launched_it={we_launched_it})")

    watchdog = Watchdog(pid, log_fn=log) if pid else None
    if watchdog:
        watchdog.start()
        log(f"watchdog started for pid {pid}")
    else:
        log("WARNING: no pid captured - watchdog cannot run (SIGNAGE_REUSE_COREL set?)")

    def step(name, fn):
        log(f"STEP {name}: start")
        t = time.time()
        try:
            result = fn()
            log(f"STEP {name}: done in {time.time()-t:.1f}s")
            return result
        except Exception as e:
            log(f"STEP {name}: FAILED after {time.time()-t:.1f}s: {e}")
            raise

    try:
        doc = step("open master", lambda: app.OpenDocument(str(MASTER.resolve())))

        def _read_shapes():
            doc.Unit = 3
            page = doc.ActivePage
            pw, ph = float(page.SizeWidth), float(page.SizeHeight)
            shapes = [page.Shapes.Item(i) for i in range(1, page.Shapes.Count + 1)]
            objs = []
            for i, s in enumerate(shapes):
                kind = {6: "text", 5: "bitmap", 7: "group"}.get(int(s.Type), "shape")
                text = None
                if kind == "text":
                    try:
                        text = s.Text.Story.Text
                    except Exception:
                        pass
                objs.append(Obj(str(i), s.Name or f"object_{i+1}", kind,
                                 float(s.LeftX), float(s.BottomY), float(s.SizeWidth), float(s.SizeHeight), text))
            return page, pw, ph, shapes, objs

        page, pw, ph, shapes, objs = step("read shapes", _read_shapes)
        log(f"  page {pw:.1f}x{ph:.1f}mm, {len(shapes)} top-level shapes")

        new_w = TARGET_W_IN * 25.4
        new_h = TARGET_H_IN * 25.4

        def _resize_tile():
            shopname_ids = find_shopname_ids(objs, "SRI KAVI STEELS", "ஸ்ரீ கவி ஸ்டீல்ஸ்")
            placed = compute_layout(
                objs, pw, ph, new_w, new_h, tile=True,
                shop_name="SMOKE TEST SHOP", shopname_ids=shopname_ids,
            )
            page.SetSize(new_w, new_h)
            shapes_by_id = {str(i): s for i, s in enumerate(shapes)}
            for p in placed:
                base_id, _, tile_idx = p.id.partition("_tile")
                base_shape = shapes_by_id.get(base_id)
                if base_shape is None:
                    continue
                shape = base_shape if tile_idx in ("", "0") else base_shape.Duplicate()
                if shape.Locked:
                    continue
                shape.SetSize(p.w, p.h)
                shape.LeftX = p.x
                shape.BottomY = p.y
                if p.text is not None:
                    try:
                        shape.Text.Story.Text = p.text
                    except Exception:
                        pass
            return placed

        placed = step("page.SetSize + resize/tile + replace shop name", _resize_tile)
        log(f"  {len(placed)} objects placed")

        step("SaveAs", lambda: doc.SaveAs(str(out_cdr), None))
        step("PublishToPDF", lambda: doc.PublishToPDF(str(out_cdr.with_suffix(".pdf"))))

        def _export():
            flt = doc.ExportBitmap(str(out_cdr.with_suffix(".png")), 802, 1, 4, 0, 0, 96, 96,
                                    1, False, False, True, False, 0, None, None)
            flt.Finish()

        step("ExportBitmap", _export)
        step("close", lambda: doc.Close())
    finally:
        step("quit", lambda: corel_util.quit_corel(app, we_launched_it, pid))
        if watchdog:
            watchdog.stop()
        pythoncom.CoUninitialize()
        log("=== diagnose_save finished ===")


if __name__ == "__main__":
    main()
