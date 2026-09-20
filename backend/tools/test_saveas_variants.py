"""Step 4 isolation experiments: which specific SaveAs condition (if any)
triggers a blocking dialog. Each experiment launches a fresh CorelDRAW,
opens the dalmia master (read-only until the experiment's own SaveAs),
performs one variant, and reports whether a dialog appeared and how long
it took. Never calls doc.Save() on a document still pointed at the master
file itself (that would overwrite signage_dataset/) - see "save_after_saveas".

Usage:
    python test_saveas_variants.py [--visible]
"""
from __future__ import annotations

import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import corel_util  # noqa: E402
from app.corel_watchdog import Watchdog  # noqa: E402

MASTER = Path(__file__).resolve().parents[2] / "signage_dataset" / "dalmia" / \
    "02 - 120 X 48 Inch - 2 Nos Double Side GSB - SRI KAVI STEELS.cdr"
OUT_DIR = Path(__file__).resolve().parents[1] / "dataset_analysis" / "diagnose_saveas"
EXPERIMENT_TIMEOUT_S = 30.0

results: list[dict] = []


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}")


def run_experiment(name: str, fn) -> None:
    log(f"=== experiment: {name} ===")
    import pythoncom
    pythoncom.CoInitialize()
    app, we_launched_it, pid = corel_util.dispatch_corel()
    watchdog = Watchdog(pid, log_fn=log) if pid else None
    if watchdog:
        watchdog.start()
    dialog_appeared = False
    error = None
    t0 = time.time()
    try:
        doc = corel_util.run_with_timeout(
            lambda: app.OpenDocument(str(MASTER.resolve())), pid, "OpenDocument", timeout=EXPERIMENT_TIMEOUT_S,
        )
        try:
            fn(app, doc)
        finally:
            try:
                doc.Close()
            except Exception:
                pass
    except corel_util.CorelTimeout as e:
        error = str(e)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    elapsed = time.time() - t0
    if watchdog:
        time.sleep(1)  # let the watchdog catch anything that appeared right at the end
        watchdog.stop()
        dialog_appeared = len(watchdog.events) > 0
    corel_util.quit_corel(app, we_launched_it, pid, timeout=10)
    log(f"=== {name}: {'DIALOG APPEARED' if dialog_appeared else 'no dialog'}, "
        f"{'ERROR: ' + error if error else 'ok'}, {elapsed:.1f}s ===\n")
    results.append({"name": name, "dialog": dialog_appeared, "error": error, "seconds": round(elapsed, 1)})


def main():
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    Path(r"C:\temp").mkdir(parents=True, exist_ok=True)

    # (c) saving the master's own content unmodified, to a path that doesn't exist yet
    run_experiment("c_no_changes_new_path", lambda app, doc: doc.SaveAs(str(OUT_DIR / "c_new.cdr"), None))

    # (a) saving to a path that already exists
    existing = OUT_DIR / "a_existing.cdr"
    existing.write_bytes(b"placeholder")
    run_experiment("a_existing_path_default_options", lambda app, doc: doc.SaveAs(str(existing), None))

    # (d) saving after only page.SetSize
    def _setsize_then_save(app, doc):
        doc.Unit = 3
        doc.ActivePage.SetSize(2438.4, 914.4)  # 8x3ft, arbitrary change
        doc.SaveAs(str(OUT_DIR / "d_setsize.cdr"), None)
    run_experiment("d_setsize_only_new_path", _setsize_then_save)

    # (b) explicit StructSaveAsOptions, new path
    def _options_new(app, doc):
        opts = app.CreateStructSaveAsOptions()
        opts.Overwrite = True
        opts.Version = 0  # cdrCurrentVersion
        opts.EmbedVBAProject = False
        opts.KeepAppearance = True
        opts.IncludeCMXData = False
        doc.SaveAs(str(OUT_DIR / "b_options_new.cdr"), opts)
    run_experiment("b_explicit_options_new_path", _options_new)

    # (b) explicit StructSaveAsOptions (Overwrite=True), existing path
    existing_b = OUT_DIR / "b_existing.cdr"
    existing_b.write_bytes(b"placeholder")

    def _options_existing(app, doc):
        opts = app.CreateStructSaveAsOptions()
        opts.Overwrite = True
        opts.Version = 0
        opts.EmbedVBAProject = False
        doc.SaveAs(str(existing_b), opts)
    run_experiment("b_explicit_options_existing_path_overwrite_true", _options_existing)

    # (e) short path vs long project path
    run_experiment("e_short_path_c_temp", lambda app, doc: doc.SaveAs(r"C:\temp\diag_test.cdr", None))
    run_experiment("e_long_project_path", lambda app, doc: doc.SaveAs(str(OUT_DIR / "e_long_path_baseline.cdr"), None))

    # (f) doc.Save() vs doc.SaveAs() - Save() only ever called after SaveAs() has
    # already redirected the document away from the master file
    def _save_after_saveas(app, doc):
        doc.SaveAs(str(OUT_DIR / "f_save_after_saveas.cdr"), None)
        doc.Save()
    run_experiment("f_save_after_saveas_redirected", _save_after_saveas)

    log("\n=== SUMMARY ===")
    for r in results:
        log(f"{r['name']:<45} dialog={r['dialog']!s:<6} error={r['error']!s:<40} {r['seconds']}s")

    import json
    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
