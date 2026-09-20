# CorelDRAW SaveAs hang: diagnosis

## Resolution (found after this document's original investigation)

**Root cause found and fixed.** While validating the production hardening
(watchdog wired into `CorelEngine`, see CLAUDE.md), the dialog reproduced
live, twice in a row, and the watchdog caught it: title **"Save Drawing"**,
a standard Windows common-dialog file picker (class `#32770`) - the exact
dialog CorelDRAW shows when you manually do File → Save As, not a
warning/confirmation popup. Its address bar showed `C:\Users\<user>\OneDrive\Documents`
- a generic default location, unrelated to either the master or the target
path - which was the giveaway: CorelDRAW was falling back to its
**interactive save dialog because `SaveAs`/`PublishToPDF`/`ExportBitmap`
were being called with relative output paths**. `CorelEngine._process` already
resolved `master_path` to an absolute path before `OpenDocument` (a lesson
from earlier COM debugging, in CLAUDE.md), but never did the same for
`cdr_path`/`pdf_path`/`png_path` built from `out_dir` - and a caller passing
a relative `out_dir` (as the ad-hoc test script that first reproduced this
did) reproduced the hang 2/2 times. Adding `out_dir = out_dir.resolve()` at
the top of `_process` fixed it: 2/2 clean runs afterward, full pipeline,
same master and shop parameters, no dialog. The fix is one line in
`backend/app/engines.py`.

This means the earlier non-reproduction (below) was real but incomplete:
every earlier diagnostic script (`diagnose_save.py`,
`test_saveas_variants.py`) happened to construct its output paths under
`Path(__file__).resolve().parents[...]`, i.e. already absolute, so none of
them could have hit this. The bug was specific to relative `out_dir`
values reaching `CorelEngine`, which the FastAPI job path never did either
(`app/main.py` builds `out_dir` from `JOBS`, itself resolved from
`SIGNAGE_DATA`) - so this was reachable but hadn't actually bitten a real
job yet. Still worth having found and fixed before it did.

## Original investigation (superseded by the Resolution above)

**The exact dialog text was not initially captured.** The originally
-observed hang (Task Manager showing `CorelDRW.exe` at 0% CPU with a child
window titled "Save Drawing", during a `CorelEngine.process()` call) did
not reproduce in any of 9 follow-up attempts: 2 full-pipeline runs of
`backend/tools/diagnose_save.py` (one with CorelDRAW visible, one hidden)
and 7 targeted `SaveAs` isolation experiments in
`backend/tools/test_saveas_variants.py`. All 9 completed cleanly with no
dialog window and no error.

This meant the hang was not obviously a deterministic property of a single
call (a specific path, a specific option, an existing-vs-new file) *among
the variants tested* - all of which used absolute paths, which is exactly
the variable none of them varied. The rest of this section is kept as
originally written, for the record.

## What was built to investigate

- **`backend/tools/corel_watchdog.py`** - a `Watchdog` class that polls
  (every 2s, via `win32gui.EnumWindows`/`EnumChildWindows`, no COM calls -
  safe to run from a background thread alongside a blocking COM call in the
  main thread) for any visible top-level window owned by the launched
  `CorelDRW.exe` PID, logs its title/class and every child control's
  class+text (would catch a dialog's message and button labels), and
  screenshots the screen (`PIL.ImageGrab`) the moment a new window appears.
- **`backend/tools/diagnose_save.py`** - runs the full pipeline (launch,
  open master, read shapes, `page.SetSize` + resize/tile + shop-name
  replace, `SaveAs`, `PublishToPDF`, `ExportBitmap`, close, quit) one step
  at a time against the dalmia master (target 150×50in), with a timestamp
  before/after each step, live-flushed to
  `backend/dataset_analysis/diagnose/diagnose_save.log` so progress is
  visible on disk even if a step is still blocked. Respects
  `SIGNAGE_COREL_VISIBLE`.
- **`backend/tools/test_saveas_variants.py`** - 7 isolated `SaveAs`
  experiments (see below), each in its own fresh CorelDRAW instance with a
  30s per-call timeout, so one hanging experiment can't block the rest.

## Step 3: full-pipeline diagnostic timing

Both runs completed with no dialog. Free RAM was checked at the start of
each (`ctypes.GlobalMemoryStatusEx`) - the second run started under the 2GB
warning threshold, and still completed without incident, so low memory
alone did not reproduce it either.

| Step | Visible run | Hidden run |
|---|---|---|
| memory at start | 2.25 GB free (85% used) | 1.81 GB free (88% used, **below 2GB warning**) |
| launch Corel | 4.7s | 5.8s |
| open master | 2.1s | 1.7s |
| read shapes | 1.4s | 2.0s |
| page.SetSize + resize/tile + replace shop name | 1.6s | 3.2s |
| **SaveAs** | **1.1s - no dialog** | **1.3s - no dialog** |
| PublishToPDF | - (not reached before this table was written; see log) | 22.7s |
| ExportBitmap | - | 10.8s |
| close | - | 1.5s |
| quit | - | 15.4s |

(The visible run's later steps also completed without a dialog - full logs
are in `backend/dataset_analysis/diagnose_visible_run/diagnose_save.log`
and `backend/dataset_analysis/diagnose/diagnose_save.log` for the hidden
run.)

## Step 4: SaveAs isolation experiments

All 7 variants completed in 3.2-4.1s each, no dialog, no error:

| Experiment | Dialog? | Notes |
|---|---|---|
| (c) unmodified master, save to a path that doesn't exist yet | No | baseline |
| (a) save to a path that already contains a file | No | pre-created a dummy `.cdr` at the target path first |
| (d) `page.SetSize` only, then save to a new path | No | |
| (b) explicit `StructSaveAsOptions` (Overwrite=True, Version=cdrCurrentVersion, EmbedVBAProject=False, KeepAppearance=True, IncludeCMXData=False), new path | No | |
| (b) same explicit options, existing path | No | Overwrite=True didn't change behaviour - no prompt either way |
| (e) short path (`C:\temp\diag_test.cdr`) | No | |
| (e) long project path, same document | No | baseline for comparison with the short path |
| (f) `doc.Save()` after `doc.SaveAs()` had already redirected the document away from the master | No | **never** called `Save()` on a document still pointed at the master file, to avoid overwriting `signage_dataset/` |

None of the six conditions the user asked to isolate (existing vs. new
path, explicit vs. default save options, unmodified vs. `SetSize`-only vs.
resized+tiled content, short vs. long path, `Save()` vs. `SaveAs()`)
individually triggers a dialog on a fresh, isolated CorelDRAW instance.

## What this rules in / out

- **Ruled out** (didn't reproduce it): existing destination file, explicit
  `Overwrite=True` options object, path length/location, `SetSize` alone,
  `Save()` vs `SaveAs()`, low free memory (tested down to 1.81GB free).
- **Not ruled out**: state that only accumulates after many consecutive
  CorelDRAW automation cycles in one machine session - e.g. GDI/USER handle
  pressure, a per-session prompt (update check, font-cache rebuild, crash
  -recovery notice) that only fires once per Corel "session" and had
  already been dismissed or hadn't yet triggered in these short, freshly
  -launched test runs, or a transient condition from the two earlier
  native-level crashes seen in this session (a segfault during the dalmia
  validation batch, and a stuck instance from the Agarpathi readiness test)
  leaving something in a bad state that a single fresh launch doesn't
  reproduce.
- The original hang happened on the **same shop parameters** (150×50in,
  same master, tiling + shop-name replacement) used in `diagnose_save.py`'s
  successful runs, which rules out those specific *inputs* as the cause -
  it really does look like session state, not a parameter combination.

## What was actually changed

- `backend/app/engines.py`: `out_dir = out_dir.resolve()` added at the top
  of `CorelEngine._process`, before any path built from it is used.

## Defence in depth (kept regardless of the fix above)

The relative-path bug is fixed, but a *different* dialog (a real font
substitution prompt, a genuinely corrupt file, disk-full on save, ...)
could still show up in production, so the hardening built while diagnosing
this stays in place rather than being removed now that a root cause was
found:

- `corel_util.run_with_timeout` - a per-step timeout that force-kills the
  launched instance and raises a clear `CorelTimeout` naming the step,
  instead of hanging forever.
- `SIGNAGE_COREL_VISIBLE=1` and the prompt-suppression settings
  (`Optimization`, `PanoseMatching`, `ColorManager.PolicyForOpen/Import`).
- `app/corel_watchdog.Watchdog`, now wired into `CorelEngine` itself behind
  `SIGNAGE_COREL_WATCHDOG` (default on - see CLAUDE.md): any dialog on our
  CorelDRAW instance open ≥20s gets its title and every child control's
  text logged, a screenshot saved into the job's own output folder, and is
  dismissed via its own default button so the job fails cleanly with a
  clear error instead of hanging. This is exactly the mechanism that caught
  the dialog above.
