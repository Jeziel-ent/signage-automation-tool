# CLAUDE.md

Guidance for working in this repo.

## What this is

Designer flow: upload a master `.cdr` → pick a brand → add shops with W×H →
**Generate** → per-shop `.cdr`, print PDF, PNG preview, and an object
position/size report. Backend is FastAPI (`backend/app`), frontend is
React + Vite (`frontend/`).

**Two UIs currently coexist** while the new one is built out in phases
(see "New UI" below): the original single-page `App.jsx` flow described
just above (`/api/jobs`, `/api/brands`, `/api/parse-shops`) is untouched
and still works; the new sidebar-shell UI (`/api/v2/...`, SQLite-backed)
is being built alongside it, not as a replacement yet. Engine-side work
(layout rules, validation, tiling) is unaffected by either UI and
continues independently - none of it was touched building the new UI.

## New UI (in progress, phased - `frontend/src/pages/`, `backend/app/db.py`)

A ground-up UI rebuild, kept deliberately separate from the original
`App.jsx`/`/api/jobs` flow above (both currently work; nothing from the
old flow was removed or changed). Building in phases, each reviewed
before the next starts:

- **Phase A (done)**: app shell (red/white/black theme, left sidebar,
  React Router) + the "Automation" page - upload a master with a real
  XHR upload-progress bar, an instant preview extracted from the `.cdr`
  itself (see below - no CorelDRAW needed for this), brand management,
  and a shops table where each row converts independently with live
  step-by-step progress, reusing the existing `CorelEngine`/
  `corel_supervisor`/single-worker queue unchanged.
- **Phase B (done)**: "Recently generated" page - list of past
  jobs/shops with filters, download links and "Open in editor".
- **Phase C (done)**: the canvas editor at `/editor/:jobId/:shopId`
  (opened in a new browser tab) - a CorelDRAW-rendered scene, select/move/
  resize, layers tree, undo/redo, every edit stored as a replayable
  operation list. See "Phase C: editor v1" below.
- **Phase D (done)**: "Save and Generate" - replays the editor's operation
  list through COM on the converted .cdr and exports CDR/PDF/PNG/JPEG, with
  a real progress bar; double-click text editing. See "Phase D" below.

### A real discovery: `.cdr` files (X4+) are ZIP archives

CorelDRAW's own file format for X4 and later is a zip archive - confirmed
by opening a real generated `.cdr` with Python's `zipfile` (`is_zipfile`
returns `True`), which lists `previews/page1.png` and
`previews/thumbnail.png` among its members, both valid, directly
-decodable PNGs. This means the Automation page can show a real preview
of the just-uploaded master **the instant the upload finishes**, with no
CorelDRAW call at all - `main.py`'s `_extract_cdr_preview()` just opens
the upload as a zip and pulls `previews/page1.png` straight out. Falls
back to a clear "no preview available" message (not a crash) if the file
turns out not to be zip-based (a pre-X4 `.cdr`) or has no matching entry.

### Backend: `app/db.py` (SQLite) + `/api/v2/...` endpoints in `main.py`

New, additive endpoints under `/api/v2/` - the pre-existing `/api/...`
endpoints are untouched. Storage is SQLite (`<SIGNAGE_DATA>/signage.db`,
`app/db.py`: `brands`, `jobs`, `shops` tables) rather than the old
in-memory `_jobs` dict, specifically so an uploaded master, its shop list
and any completed conversions survive a server restart - verified by hand
(kill the server mid-session, restart it, `GET /api/v2/jobs/{id}` still
returns the same shops and their `done` status).

- `POST /api/v2/upload` (`master` file + `brand` form field) - saves the
  master, extracts its preview (above), creates a `jobs` row. The upload
  progress bar itself is a **frontend** concern
  (`components/UploadDropzone.jsx` uses a raw `XMLHttpRequest` with
  `xhr.upload.onprogress` - `fetch()` doesn't expose upload progress
  reliably across browsers) - the backend just receives an ordinary
  multipart body; `UploadFile` already spools large files to disk itself,
  so no special handling was needed for the 300MB target.
- `POST /api/v2/jobs/{job_id}/shops` - add one shop row (name,
  width+unit, height+unit, optional reference note); persisted
  immediately, independent of conversion.
- `POST /api/v2/shops/{shop_id}/convert` - submitted to the **same**
  single-worker `ThreadPoolExecutor` the old `/api/jobs` flow already
  uses (`main.py`'s module-level `_pool`), so "one CorelDRAW job at a
  time" holds across both UIs, not just within the new one. For
  `CorelEngine`, routes through `corel_supervisor.run_batch` as a
  single-job batch (same hang-protected worker-subprocess path
  `validate_all.py` uses - see "Process isolation"); for `MockEngine`,
  calls `engine.process()` directly in-process (no subprocess needed,
  there's no COM/hang risk with the mock). Neither `engines.py` nor
  `layout.py` needed any changes for this.
- `GET /api/v2/shops/{shop_id}/status` - polled every ~800ms by the
  frontend while a shop is converting. `_STEP_PERCENT` maps
  `CorelEngine`'s existing named steps (`launch`/`open`/`tile_resize`/
  `saveas`/`pdf`/`png` - see "Production hardening") to a rough
  completion percentage and reads them **live off `corel_supervisor`'s
  own heartbeat file** (written before each step of the running job) -
  real backend-driven progress, not a fake timer, though necessarily
  coarse (discrete step jumps) since COM gives step transitions, not
  byte-level progress within a step. Verified against a live conversion:
  the UI showed 55% (tile_resize) → 88% (pdf) → 97% (png) → Done, driven
  entirely by these real heartbeat reads.
- `GET /api/v2/shops/{shop_id}/files/{filename}` - serves a completed
  shop's output files (cdr/pdf/png/report), same path-containment guard
  pattern as the old `/api/jobs/{id}/files/...` endpoint.

Width and height each have their **own** unit dropdown in the new UI
(`in`/`cm`/`mm`/`ft`) - `engines.py`'s `_process` still only accepts one
`shop["unit"]` for both dimensions (unchanged, per this phase's "don't
touch engine layout logic" instruction), so `main.py`'s convert worker
pre-converts both to mm itself via `layout.to_mm()` before building the
`shop` dict the engine sees - the engine never has to know the UI offered
two independent units.

### Post-Phase-A refinements (review feedback)

**Smoothed progress.** The per-row Convert bar no longer jumps between the
backend's discrete step thresholds. `frontend/src/hooks/useSteppedProgress.js`
eases the displayed value toward the end of whichever step the backend
last reported, paced by **measured** step durations: `GET
/api/v2/step-estimates` (`db.get_step_timing_estimates`) averages every
completed shop's own `report.json` `timings_s`, so the pacing improves as
real conversions accumulate (a step with no data yet falls back to a 4s
default). The displayed value is capped 1 point below the step's end
threshold until the backend confirms the next step, and only reaches 100
when status is `done`. Verified live: samples 0.5s apart on a 180x48 board
read `1,3,12,15,33,38,44,47,51,53,54,54,54...` - continuous easing that
plateaus at 54 (just under `tile_resize`'s 55) while the slow step runs,
then advances only when the real step event arrives. The hook takes
generic `steps`/`currentStepKey`/`done`/`estimates`, so Phase D's export
progress reuses it as-is. Caveat: MockEngine's reports carry no
`timings_s`, so estimates stay empty (defaults apply) until a real
CorelEngine run has completed.

**Reference field.** UI unchanged (free-text `reference`), but `shops` now
also has a nullable `reference_file_path` column (`db._MIGRATIONS` adds it
to an already-created database via an idempotent `ALTER TABLE`), and
`POST /api/v2/jobs/{id}/shops` accepts it. No upload UI yet - waiting on
what the field should actually be.

**Verified through the UI, real CorelEngine, brand `dalmia`** (uploaded
one of the already-generated dalmia `.cdr` outputs as the master - nothing
from `signage_dataset` touched): a 180x48in board and a mixed-unit
120in x 4ft board (correctly converted to 3048x1219.2mm).

| Board (new UI) | Visual combined | Same-size benchmark (old CLI pipeline) |
|---|---|---|
| 180x48in | 0.795 PASS | 0.785 (board 02-180) |
| 120in x 4ft | 0.977 PASS | 0.976 / 0.972 / 0.955 (boards 03 / 05 / 14) |

No regression from routing conversion through the v2 API. Caveat: the v2
API doesn't yet pass `master_shop_name`/phone/GST, so the master's own
shop name is kept as-is - the new UI doesn't expose shop-name replacement
yet.

### Phase B: Recently generated (`pages/RecentlyGenerated.jsx`)

`GET /api/v2/recent` (`db.list_all_shops_with_job`) returns every shop
across all jobs, newest first, joined with its job's brand/master filename:
name, size + units, reference, status, error, timestamps, and `files`
(cdr / pdf / preview / report, or `null` before a conversion finishes).
`report_json` is deliberately left out - the list stays light.

The page shows preview thumbnail, brand, shop (+ master filename), size
(each dimension with its own unit), created time, status pill (failure
reason on hover), per-file download buttons, and "Open in editor" (done
rows only; opens `/editor/:jobId/:shopId` in a new tab). Brand and status
filters are client-side; the list auto-refreshes every 3s while any row is
queued/converting. Downloads reuse the path-guarded
`/api/v2/shops/{id}/files/{filename}` route.

Approximate / not real Corel: with MockEngine the thumbnail and "preview"
download are the mock SVG, not a CorelDRAW render (with CorelEngine they are
the real PNG). The Phase B
screenshots were taken against a seeded mock-engine database (done / new /
failed rows across two brands), not real conversions - real conversions
already exercise the same rows via Phase A.

### Phase C: editor v1 (`frontend/src/editor/`, `backend/app/scene_*.py`)

**Scene export (real CorelDRAW).** `GET /api/editor/{job}/{shop}/scene`
returns the converted board as a scene: page size, a layer tree (layers ->
groups / PowerClips -> shapes) and per shape x/y/w/h (mm, origin
bottom-left, bbox), rotation, type, name, visibility, lock, text
(content/font/size). The first request builds it and answers **202 with
live progress** until done (the editor polls); the finished scene is
cached under `<out_dir>/scene/`. The build runs the `scene_export` job kind
in the corel_worker subprocess via `corel_supervisor.run_batch` (single
CorelDRAW job at a time on the shared `_pool`; inherits the 1.5 GB free-RAM
refusal floor - which the endpoint reports as **503 with the reason**,
retryable with `?retry=1` -, the hang watchdog and orphan cleanup).

Every leaf shape is exported by CorelDRAW itself, one at a time,
selection-only with a transparent background (`Document.ExportBitmap`
with `cdrSelection`, or `Document.Export` to `cdrSVG`): **curves/rectangles/
ellipses as SVG** (crisp at any zoom), **text, bitmaps and PowerClips as
PNG**. Text is never SVG because Corel's SVG text uses `<font>` elements,
which browsers do not render (an SVG that contains one falls back to PNG
too). PNG size is ~100 dpi clamped to 96-2048 px on the long side. Groups
have no image - the canvas draws their children; a PowerClip is one image of
the clipped result. A full-page reference render (`page.png`) is exported
too. Verified live: **CorelDRAW's `Shapes.Item(1)` is the TOPMOST shape**
(`OrderToFront` moved a shape to index 1), so every child list is reversed
to bottom -> top; the `Guides` layer is `IsSpecialLayer` and skipped; a
selection export's pixel size maps exactly onto `SizeWidth/SizeHeight`.

Measured on real generated dalmia boards: **120x48in = 138 shapes, 10 s of
export (about 40 s end to end including CorelDRAW launch/open/quit), 419 KB
of images; 216x48in = 200 shapes, 18 s of export (47 s end to end), 608 KB.**
The editor's composite of those per-shape images vs CorelDRAW's own
full-page render of the same file: mean absolute pixel difference 2.3 / 255,
0.002% of pixels differing strongly (only the Tamil footer that already
renders as tofu boxes in the source - missing font, see limitations).

**Operation list = the contract** (`app/scene_ops.py`, mirrored line for
line by `frontend/src/editor/ops.js`). The visible scene is always
`apply_ops(base_scene, ops)`. Ops: `move`, `resize` (a selection box mapped
onto another; descendants scale with it, text `size_pt` follows height),
`order` (front/back/forward/backward), `reorder` (parent + index - layer
drag and drop), `visibility` (object or layer), `group`, `ungroup`, `text`
(content/font/size; marks the node `stale`), `delete`, `paste` (also how
duplicate/cut+paste work: the op carries the node snapshots with fresh
ids and a `src` back-reference), `page` (page size only - objects stay).
Edits are autosaved (`PUT /api/editor/{job}/{shop}/ops`, table
`editor_ops`); the PUT replays the list on the pristine scene first and
answers **422 without saving** if it cannot be replayed.
`GET /api/editor/.../replayed` returns the server-side replay - this is
what Phase D will drive through COM. **Drift guard:**
`backend/tests/fixtures/ops_golden.json` holds hand-computed cases run by
both `test_scene_ops.py` (Python) and `src/editor/ops.test.mjs` (JS, `npm
test`); verified live that the browser's scene and the server's replay of
the saved ops differ by 0.0000 mm across all 138 shapes after a 10-op
session.

**Canvas: plain SVG, not react-konva.** Every shape is already an image
Corel rendered, so the canvas only places, hit-tests and transforms
`<image>` elements: SVG gives resolution-independent zoom of the SVG
leaves, free DOM for the ~140-330 elements, exact mm units via a single
scale transform, and no extra dependency; Konva would only add
rasterisation and a wrapper. Details worth knowing: clicks are hit-tested
against each image's **alpha channel** (`alphaMaps.js`, a 192 px grid per
image, preloaded in idle time) so a click on a transparent corner falls
through to the object underneath, as in CorelDRAW; drags update the
`<image>` attributes directly (no React re-render) and commit one op on
release; wheel = zoom about the cursor, middle-mouse drag = pan, shift+wheel
= sideways; rulers use "nice" 1/2/5 steps in the chosen unit (default `in`),
run from the page's bottom-left, and carry red double-headed dimension arrows
with the page W/H above and left of the canvas; corner handles keep
proportions, side handles do not, Shift on a corner frees it.

**Selection model.** A group or PowerClip is ONE object on click;
double-click enters a group and selects the child under the cursor (a
"context" - Esc leaves it and selects the group); Ctrl/Shift+click toggles
within the same parent; marquee selects fully-enclosed objects; Ctrl+A
selects the current layer/context. Shortcuts: Ctrl+Z, Ctrl+Shift+Z / Ctrl+Y,
Ctrl+C/X/V, Ctrl+A, Ctrl+G (group), Ctrl+U (ungroup), Delete, Esc. Layers
panel: tree in Object Manager order (top of stack first), eye toggle per
row/layer, per-group ungroup button, Group/Ungroup buttons, click <->
canvas selection both ways, HTML5 drag and drop (above / below / into a
group, also inside groups). The properties panel shows X/Y as the
**centre** of the selection (CorelDRAW's default reference point).

**Bugs found and fixed while building it** (each with a regression test):
group/PowerClip children were not reversed to bottom -> top like layer
children (caught by the fake-COM test); deleting both children of a group
refreshed the already-removed group and raised a KeyError; and, only visible
on the real engine, the worker's atomic heartbeat write
(`tmp.replace(path)`) raised `PermissionError` on Windows whenever the web
API or supervisor happened to be reading the file at that instant - the
editor's 800 ms progress polling plus one heartbeat per image made it
fail a real export. `_write_json` now retries for up to ~3 s and
`export_scene` emits at most ~2 image heartbeats per second.

**Real CorelDRAW vs approximate - be honest about these:**
- Real: every pixel on the canvas is a CorelDRAW render of that shape at
  export time; positions/sizes/tree/z-order are read from COM.
- Approximate: after a **resize** the image is stretched, not re-rendered
  (fine for logos, not identical to Corel for text or strokes); after a
  **text change** the canvas draws the new text as live SVG text (see "Live
  text on the canvas" - browser font metrics, not Corel's) and marks the
  object "edited" until Phase D regenerates it; rotated shapes
  keep their rotation baked into the image and are resized by bounding box.
- Not implemented: editing **inside** a PowerClip (deliberately deferred:
  none of the exported dalmia boards contains one, and doing it right needs
  unclipped per-child renders plus the clip outline; its contents are
  listed in the tree but read-only - move/resize applies to the clipped
  result as one object); locked objects/layers are shown but not editable.

**Follow-up round (built after the first review):**
- **Arrow-key nudge**: 0.1 in (1 mm when the unit is mm/cm), Shift x10,
  Ctrl x0.1. A held key or quick burst is merged into ONE `move` op (800 ms
  window, same objects) so undo is not one step per key repeat.
- **Snapping** (toolbar checkbox, on by default; Alt bypasses it): while
  moving, the selection's left/centre/right and bottom/middle/top snap to the
  page edges/centre and to the edges/centres of the other objects in the same
  context within 6 screen px, with dashed red guide lines; while resizing,
  only the edges being dragged snap. Proportional corner drags are not
  snapped (snapping one edge would break the aspect ratio; hold Shift on a
  corner to free it and snap). Logic is pure and tested (`snapMove`,
  `snapResize`, `snapTargets` in `model.js`).
- **Layer reordering**: new `layer_order` op {id, index} (Python + JS +
  golden `base2`/`layer_cases`); layer rows in the tree drag above/below
  each other; objects can also be dropped INTO a layer row. Verified live
  against CorelDRAW that `Page.Layers.Item(i)` lists layers top-first (a
  layer created above another appears earlier), so `walk_page`'s reversal
  to bottom -> top is right. The mock scene now has two layers so this is
  exercisable without Corel; real dalmia masters have a single layer.
- **Font-installed check**: `GET /api/fonts` (`app/fonts.py`) lists installed
  families via GDI `InstalledFontCollection` (PowerShell, cached; registry
  fallback; `available:false` on other OSes, where the check is skipped
  rather than blocking edits). The Font field's suggestions are that list,
  and naming an uninstalled font is refused with an explanation (CorelDRAW
  silently ignores it) - no op is created. A board's existing font that is
  not installed is flagged too. Font-linking cases like Arial-for-Tamil are
  fine because the check is by family name of what CorelDRAW is asked to use.
- **Layers tree wording + properties tabs**: the layers tree already mirrored
  the file's real structure (checked against the cached master dump: 133
  curves, 4 groups, 3 texts, 2 bitmaps, one layer - identical counts), so
  the ~130 flat "Curve" rows of a dalmia board are what CorelDRAW's Object
  Manager shows too. Row wording now follows the Object Manager ("Group of 3
  Objects", "Artistic Text: ...", "Paragraph Text: ...", "Bitmap", a name the
  designer set in Corel wins); the scene export records `text.kind` from
  `Shape.Text.Type` for this (scenes cached before this change show
  "Artistic Text" for every text). The properties panel is tabbed -
  Dimensions (W/H, keep proportions), Position (X/Y, stacking order), Text
  (enabled only for a single text object) - and capped in height so the
  layers tree keeps the rest of the column.
- **Page W/H**: changing the fields opens a dialog with two choices - "Re-convert
  at this size" (creates a new shop on the same job at the new size, runs
  the real layout engine on the ORIGINAL master, shows the usual convert
  progress and opens that board's editor; the current board and its edits
  are left untouched and NOT carried over) or "Change the page only" (the
  old `page` op, undoable). A "scale everything" option was deliberately not
  built: it would be a second, worse layout engine inside the editor.
- "Save and Generate" saves the operation list and says so; the export
  popup (cdr/pdf/png/jpeg) is Phase D.
- Real dalmia masters are ungrouped curves, so the layers tree of a real
  board is ~130 "Curve" rows (this is the documented ungrouped-logo
  limitation); grouping them in the editor works but is a manual step.

**RAM.** Scene export needs one CorelDRAW instance for 20-50 s and
respects the same free-RAM floor as conversion (1.5 GB, 503 below it).
Free RAM was 1.8-4.0 GB on this machine during the work; conversions
took 15 s in Phase A but 75 s once while memory was tight. The browser side
is light (138 images = 420 KB; largest decoded leaf capped at 2048x2048).
Do not run a scene build while a validation batch is running.

### Phase D: Save and Generate (`backend/app/export_replay.py`, `frontend/src/editor/ExportDialog.jsx`)

**Flow.** The toolbar button flushes the saved edits, opens a popup (formats
CDR/PDF/PNG/JPEG, multi-select, plus options), and `POST
/api/editor/{job}/{shop}/export` queues a job on the shared single-worker
pool. It runs in the corel_worker subprocess (`export_replay` job kind, via
`corel_supervisor.run_batch`: RAM floor, watchdog, orphan cleanup). Progress
is the worker heartbeat (`launch, open, replay i/n, verify, cdr, pdf, png,
jpeg`); the server returns a `plan` whose step widths follow measured
durations of earlier exports (`db.get_export_step_estimates`), and the popup
drives `useSteppedProgress` with it - the same hook as the convert bar.
Results: `exports` table, files under `<out>/exports/<id>/`, downloadable, the
original converted .cdr is opened, replayed, `SaveAs`'d elsewhere and closed
with `Dirty=False` (never saved over). **Every output file is written by
CorelDRAW**; the mock engine writes clearly-flagged placeholders.

**Replay = shadow scene + COM.** `Replayer` applies each op to a shadow scene
with `scene_ops.apply_op` (so an op that cannot apply raises the same error as
in the editor), then does the matching CorelDRAW call: `Move`, `SetSize` +
assignable `LeftX/BottomY`, `Order*`, `Visible`, `Text.Story`, `Delete`,
`Duplicate` (paste), `Group` via `CreateShapeRangeFromArray`, `Ungroup`,
`MoveToLayer`, `Layer.MoveAbove/MoveBelow`, `Page.SetSize`. Verified live and
relied on: `Shapes.Item(1)` is topmost; forward/backward are plain
one-step swaps; `MoveToLayer` also pulls a shape OUT of a group; there is no
API to add a shape to an existing group (done by ungroup + regroup, the group
keeps its editor id); `Shape.Ungroup()` returns nothing. z-order is settled
against the shadow scene after structural ops. Objects a later `paste`
copies (cut + paste) are hidden, not deleted, until the end.
After the last op the real document is re-walked and compared with the
shadow scene (structure, z-order, positions, visibility, text) -
`report.verification` ({ok, compared, mismatches}) and a warning if it differs.
Edited text is compared by anchor (left/centre/right) with a few-mm tolerance,
because new content changes glyph metrics. **Live result** on a real
generated dalmia board: 14 mixed operations (move, resize, order, hide,
group, ungroup, text edit, paste, delete, cut+paste, reorder into and out of a
group, move) applied, 142 objects compared, and through the UI a 3-op session
verified "all 142 objects match". Unit tests use a fake CorelDRAW document
modelling exactly those verified behaviours (`test_export_replay.py`).

**Options (only ones CorelDRAW honours - each checked live).** PDF: colour
(keep/RGB/CMYK), embed fonts vs convert text to curves, image dpi. PNG/JPEG:
size by longest side (1600/4000/8000 px) or dpi, PNG background
transparent/white, anti-aliasing. Sizes are passed as explicit pixel
width/height because CorelDRAW rounds a dpi to a whole number (600 px
requested came out 539 px until this was fixed; now exact, verified up to
13500 x 4500). Hard limits, enforced in the popup AND the API (422): 20000 px
on the long side, 200 MP - the earlier documented 28800 x 10800 export shows
why. A large raster also needs more free RAM than the 1.5 GB floor (503 with
the reason). **JPEG quality is deliberately not offered**: probing showed
`ExportBitmap`'s compression argument and `StructExportOptions.Compression`
do not change a JPEG's size, i.e. COM cannot control it, and re-encoding
outside CorelDRAW would break "output always from Corel".

**Text editing.** Double-click a text object (or F2 on a selected one): an
overlay textarea opens (Enter = new line, Ctrl+Enter or click away = apply,
Esc = cancel) and applying creates a `text` op. Double-clicking a group still
enters it first. The canvas cannot re-typeset Corel text, so the object shows
an "edited" badge until the export - whose result is shown in the popup. Font
warnings: the editor warns when the text's font is not installed on this
machine, and the export popup lists edited text in uninstalled fonts before
generating; at replay `Story.Font` is read back and a warning recorded if
CorelDRAW ignored it. Setting `Story.Text` replaces the run, so per-character
formatting inside one text object is not preserved.

**Approximate / not covered:** a rotated shape resized in the editor is
resized by bounding box in Corel too (verification tolerance applies);
editing inside PowerClips and text-frame refitting after a long edit are not
done (a long name can run past its slot - check the popup's preview);
cross-machine font differences are only warned about, not fixed.
**RAM/time (measured):** launch about 4 s, replay of 14 ops about 6 s, PDF 1-18 s
(driven by embedded bitmaps), PNG/JPEG 1-3 s at 4000 px; one CorelDRAW
instance per export, same free-RAM floor as everything else.

**Update: export moved to the Shops queue row; the editor only saves.** The editor toolbar's "Save and Generate" is now
**"Save Changes"** (`EditorPage.saveChanges`): it flushes the op list (PUT .../ops), posts `{type: "saved", jobId, shopId}` on
`BroadcastChannel("signage-editor")`, focuses `window.opener` and calls `window.close()` (allowed because "Open in editor" uses
`window.open`); if the browser refuses, a toast says the save worked. The Automation page listens and shows a queue notice. A done
row's Convert cell has a Download icon that opens `components/ExportModal.jsx` ("Export Signage Files"; `editor/ExportDialog.jsx`
was deleted). It builds the scene if the board was never opened in the editor (202 progress), reads `/replayed` for the page size, and
offers four cards, each with a checkbox and its own single-format download: **PDF** (Print-Ready CMYK / Digital Web RGB, image dpi
150/300/600, crop marks, include bleed, text as curves), **JPG** (RGB/CMYK, 72/150/300 dpi), **CDR** (v21 recommended / v27 native = 0 /
X7 = 17, editable text / text as curves), **PNG** (transparent, 72/150/300 dpi, padding 0/10/25/50 mm). "Download Selected" exports
the ticked formats and auto-downloads the zip (or the single file); per-file links and "Download All (.zip)" stay. A dpi that would
break the 20000 px / 200 MP limits is disabled; the default is the largest of 72/150 that fits. Mapping: `utils/exportOptions.js`
(tested). Backend (`export_replay.normalize_options`): `png`/`jpeg` blocks override the shared `raster` block per format
(`raster_sizes`); PDF `CropMarks`/`IncludeBleed` (both exist on PDFSettings in the v27 typelib, checked with `LoadRegTypeLib`; set and
read back like the other settings, the bleed LIMIT stays CorelDRAW's default and is reported); JPEG CMYK = `cdrCMYKColorImage` (5),
read back with PIL (`report.jpeg_mode`, warning if not CMYK); PNG padding widens `_page_export_area` by N mm per side; CDR
`version` goes through `save_cdr(doc, path, version)` / `check_cdr_format(..., version)`; CDR text-as-curves converts every text shape
(groups and PowerClips via `index_doc`) and counts what is left (`report.cdr_text_to_curves`), and the CDR is then written LAST
(`export_order`, also in `plan_steps`) so the PDF/PNG/JPEG keep editable text. **Not offered: JPEG quality**. COM cannot control it
(see above), and the JPG card says so. **Not verified live in CorelDRAW:** crop marks/bleed, CMYK JPEG, padding, X7/native saves
and text-to-curves were tested with fake COM + a mock-engine browser run only (Edge via playwright-core: modal, all controls,
request body, zip + single-file downloads, Esc, Save Changes closing the tab and the notice in the main tab); each has a
read-back warning for when CorelDRAW ignores it. Known layout issue (predates this): at 1440 px the Shops table is wider than its
card once a shop is done (727 px in 617), so the Editor/trash columns need a sideways scroll; the Download icon adds 30 px.

**"Create Print File" - the Print Details summary sheet (`app/print_sheet.py`, `POST /api/print-sheet/generate`,
`components/PrintFileModal.jsx`).** A button at the bottom-left of the Shops Queue (disabled until a shop is converted) opens "Create
Print File Details": header/title (prefilled `<BRAND> - ACP BOARD`), project no, date (`<input type="date">`, today in LOCAL time -
`utils/printSheet.todayISO`, not `toISOString`), location, board type (free text + suggestions), a checklist of the queue's converted
shops (all ticked, numbered by their S.no) with live Total Qty / Total Sq.Feet, and PDF/JPEG. The server renders the sheet with **Pillow**
(no CorelDRAW, so it never waits behind conversions; ReportLab is not installed) after the reference
`assets/DT-2026-00065__...print-details-2...jpg`: A4 landscape at 300 dpi (3508x2480 - the reference's own ratio), geometry measured
off the reference in 2000-px-wide coordinates (`_R`), red slanted "PRINT DETAILS" block + grey title band, DATE / PROJECT NO (value in
red) with the location right-aligned, divider, red board-type banner + QTY / Sq.feet, a drawn yellow folder with a red "CDR" emblem
captioned "CDR & PDF", and per shop a bottom-aligned thumbnail with a drop shadow and a caption `05 - 214 X 36 Inch - ACP BOARD - NAME`
(mixed units: `10 Feet X 48 Inch`). Headings are Bahnschrift Bold SemiCondensed (closest installed match), captions Segoe UI, Tamil
text Nirmala UI; off Windows DejaVu / Pillow's default. Qty = one per shop; Sq.feet = sum of each board's area, whole feet
(reference: 26676.25 sq in = 185.25 -> "185"), one decimal below 10. The grid grows from 3 to 6 columns, then continues on further
pages that repeat the header (multi-page PDF; multi-page JPEG = zip). Thumbnail = the shop's latest finished editor export PNG/JPEG,
else its conversion preview PNG; MockEngine's SVG preview draws a "No preview available" box. Files are rendered to a temp dir and
removed after sending; the name is `Print_Details_<project>_<DD.MM.YYYY>.<ext>`. Deviations: the module is `app/print_sheet.py`, not
`app/engines/print_sheet_generator.py` (an `app/engines/` package would shadow `app/engines.py`); the caption uses the sheet's board
type for every shop (the reference shows ACP-LIT under an ACP BOARD header - there is no per-shop board type field). Limits: no
complex-script shaping (Pillow without libraqm), so Tamil in a name renders with unshaped conjuncts; the PDF is a raster page. Tests:
`test_print_sheet.py` (11: reference totals, captions, date, grid/pagination, pixel checks of the layout colours, PDF/JPEG/zip, API
validation 404/409/422, thumbnail preference), `printSheet.test.mjs` (4). Checked in Edge against a mock backend: button disabled
until a conversion, date = today, only converted shops listed, totals update on untick, JPEG download named from the server; sheets
rendered from real CorelDRAW previews viewed side by side with the reference.
**Update: the Print Details sheet is A4 PORTRAIT now (supersedes the landscape layout described just above).** 210 x 297 mm =
595.28 x 841.89 pt, 15 mm margins, geometry in points (`_P` = 300/72; canvas 2480 x 3508 px; the PDF's MediaBox reads 595.2 x 841.92
from whole-pixel rounding). Sections: header 120 pt (red rounded "PRINT DETAILS" badge + the title in near-black bold, a red rule, a
#F4F4F4 bar with DATE / PROJECT NO - values red - and the location right-aligned), summary 40 pt (red pill with the board type,
"QTY : n Nos", "Total Sq.feet : n" right-aligned), the shop grid, footer 30 pt ("Generated DD.MM.YYYY HH:MM", "Page X of Y"). Grid
(`plan_grid`): 2 columns x 3 rows (cards ~249 x 171 pt) when every shop fits, else 3 x 4 per page (12 per page, further pages repeat
header and summary). Card: white, #E0E0E0 outline, 4 pt radius; the preview aspect-fitted on #F9F9F9; "CDR" / "PDF" chips when those
files exist (`SheetShop.has_cdr/has_pdf`, from `asset_zip.pick_sources` - the same rule as the ZIP); the caption wrapped to 3 lines.
Fonts Arial / Arial Bold (the spec's Helvetica/Arial), Nirmala UI for Tamil. The reference-matched landscape design (Bahnschrift, slanted
banners, folder graphic) was replaced, not kept as an option. Tests: `test_print_sheet.py` (12 - grid geometry inside the margins,
pixel checks of each section). Rendered from real previews at 4 and 9 shops and viewed.
**Update 2: A4 portrait IN THE REFERENCE'S STYLE (supersedes the rounded-badge portrait layout just above).** The reference's own
elements are laid out in its units (the reference scaled to 2000 wide) and mapped onto the portrait page at `K` = 0.372 pt per unit
(the page is `REF_W` = 1600 units wide): red angled "PRINT DETAILS" block + grey title band (0-125), DATE / PROJECT NO (values red, caps
at 188 / 308) with the location / region right-aligned on the project line, a full-width dark rule (428-432), the red angled board-type bar
(430-532) with QTY / Sq.feet, a thin dark frame round the page; Bahnschrift Bold SemiCondensed headings, Segoe UI captions (Nirmala UI for
Tamil). Grid from unit 568: card #1 is ALWAYS the yellow "CDR & PDF" folder (drawn by `_draw_folder` from the reference's folder geometry,
mapped into the box - `assets/folder_cdr_icon.png` did not exist and a drawing stays crisp), then one card per shop, previews bottom-aligned
in their box with a soft shadow (as on the reference), captions centred, at most 2 lines (`_wrap` ends an overflow with "..."). `plan_grid(n)`
counts the folder: 2 columns x 3 rows (box 150 pt) while everything fits on one page, else 3 x 3 (box 120 pt, 9 per page); further pages
repeat the header and show "Page X of Y" (no footer otherwise - the reference has none). The CDR/PDF chips and the generated-time footer of
the previous portrait version were dropped to match the reference; `SheetShop.has_cdr/has_pdf` remain (still filled by the endpoint) but are
not drawn. Tests: `test_print_sheet.py` (grid incl. the folder, 2-line captions, pixel checks of the angled blocks, rule, folder-first and
bottom-aligned previews).
`print_sheet` is imported INSIDE the route (like every other Pillow use in the app), so a Python without Pillow still starts the
server and only this route answers 503 with the pip command. A "Not Found" from this button means the running server predates the
route - the dev server here runs without `--reload`, so restart it. (Its process list shows the base Python 3.10 exe, which has no
packages; that is only the Windows venv launcher's child - the parent is `.venv\Scripts\python.exe`, which has Pillow.)

**"Generate ZIP" (`app/asset_zip.py`, `components/GenerateZipModal.jsx`; supersedes the earlier one-click "Download ZIP").**
Next to "Create Print File". Opening the modal POSTs `/api/export-zip` (the shops as they were when it opened - the page's
`printable` array is new on every render, and StrictMode runs effects twice in dev, so a ref allows ONE build per attempt), which
builds `Signage_Assets_Export.zip` = `<NN>_<SHOP>.jpg` at the root, `CDR&PDF/cdr/<NN>_<SHOP>.cdr`, `CDR&PDF/pdf/<NN>_<SHOP>.pdf`
(`asset_zip.FILES_DIR`; NN = queue S.no; names keep any script WITH combining marks - `\w` alone drops Tamil vowel signs and
the virama; the same bug in the per-export `_zip_name` was fixed) and answers `{token, download, summary}`. The archive stays on
the server for the modal: "Download ZIP File" is a plain link to `GET /api/export-zip/{token}` (streamed to disk, never held in
page memory - CDRs are 9-300 MB; any number of downloads) and the WeTransfer card uploads the same file. Closing the modal sends
`DELETE /api/export-zip/{token}`; if an upload is still reading it, it is removed when that upload ends; anything left is swept
after an hour. Sources per shop and format (`pick_sources`): the newest finished editor export of that format whose `ops` equal
the shop's CURRENT saved edits, else the conversion's file, with a note when the shop has edits no export contains. The JPG is an
export's JPEG, or a PNG (export, else the conversion preview) re-encoded as JPEG q95 on white - CorelDRAW's pixels, but a conversion
preview is only 36-150 dpi (1600 px target; a 120 in board is 4320 px), so print-resolution JPGs need an editor export; the
modal says "Packaging JPGs...", not "high-res". Missing files (MockEngine: no PDF, SVG preview) are listed in the modal, never faked.

**"Generate WeTransfer Link" (`app/wetransfer_uploader.py`, `POST /api/export-wetransfer` {token | shop_ids} -> {job_id},
`GET /api/export-wetransfer/{job_id}` -> {status queued|running|success|failed, step, progress, wetransfer_url, error}).**
WeTransfer RETIRED its Public API in 2020 and issues no keys (its support article and a 503 from developers.wetransfer.com,
checked 2026-09-28), so - the project owner's decision, made knowing the terms/fragility risk - the uploader drives the website
itself: Playwright + the installed Microsoft Edge (`channel="msedge"`, no Chromium download), headless, on its own one-thread pool
(never `_pool`), polled by the card every second (setup steps fill 10 %, then the page's own upload %). Every run accepts
WeTransfer's Terms ("I agree") on the company's behalf and sends client artwork to a third party. A size check (`SIGNAGE_WETRANSFER_MAX_GB`,
default 3 = the free-transfer limit when written) answers 413 before starting; without Playwright the route answers 503 with the
pip command. **UNVERIFIED against the real site**: only the first two screens were observed live (cookie banner "Reject All",
Terms "I agree"); a follow-up probe of the send panel was blocked by this session's permission policy, so the link-mode switch,
file input, submit button and link location are best guesses. Each step fails with a named error and a screenshot in
`<SIGNAGE_DATA>/wetransfer_debug/`, and screens needing an account / e-mail code / paid plan are detected and reported (free link
transfers may now require one). What IS verified: the uploader's browser mechanics end to end through real Edge against a LOCAL
stand-in page (`tests/fixtures/wetransfer_standin.html`: consent clicks, hidden file input, link mode, submit, % progress, link
extraction; and stopping at an e-mail-verification screen with a screenshot); the API job lifecycle with a fake uploader; the modal
in Edge with the WeTransfer endpoints intercepted (loading state, animated tick, download, progress text, link, Copy Link -> clipboard
+ "Copied!" toast, Open Link = new tab with noopener, DELETE on close, one build). First real use: run it once by hand and read the
screenshot if it fails. Tests: `test_asset_zip.py` (8), `test_wetransfer.py` (9, 2 of them drive Edge locally and skip without it),
`assetZip.test.mjs` (3), `generateZip.test.mjs` (2).

**WeTransfer: first live run + server-link fallback.** The first real run failed at `link_mode`; its screenshot showed the send
panel with the file already attached (76 MB, "2.9 GB remaining" of the 3 GB free allowance): e-mail fields, a "3 days" expiry button, an
ICON-ONLY "..." button beside it (no accessible name that matched) and "Transfer". `_set_link_mode` now finds that button by an anchored
name, else by position (the icon-only button nearest above the submit button, inside its panel), then picks a `link` choice among
radio/menuitemradio/menuitem/option/tab/switch/button/label. Found while testing it: a loose `/more/` name pattern hit the panel's
"Add more" (files) button first - on the live page that would have opened the file dialog - so name patterns are anchored. Consent:
"Reject All" first, "Accept" only on a banner without a reject option. Each on-page step fails after `NAV_STEP_MS` (15 s). Every failure
now also writes `wetransfer_<time>_<step>_controls.json` (visible controls: text, aria-label, role, test id, position) next to the
screenshot - read it before changing selectors again. The menu's contents and the link-mode submit label are STILL unobserved.
**Fallback** (`main._wt_worker`): if the upload fails for any reason - or is skipped (Playwright missing, ZIP over
`SIGNAGE_WETRANSFER_MAX_GB`) - the job still ends `success` with `fallback_used: true`, `wetransfer_error` (the reason, also logged),
`message`, `local_only`, `expires_at`, and `wetransfer_url` = a server link `/api/shared/<id>/Signage_Assets_Export.zip`: a COPY in
`<SIGNAGE_DATA>/shared_zips/<id>/` (the modal's archive is deleted on close), id = `secrets.token_urlsafe(18)`, kept `SIGNAGE_SHARE_DAYS`
(default 3, WeTransfer's own default), 410 once expired, swept on the next share. The host comes from the address the server listens
on (`request.scope["server"]`): 127.0.0.1 -> `local_only` (the dev server here runs without `--host`, so such a link opens only on
this computer and the modal says to start it with `--host 0.0.0.0`); 0.0.0.0 -> this machine's LAN address (UDP-connect trick, no
packet sent); `SIGNAGE_PUBLIC_BASE_URL` overrides. Never an internet link. The modal shows "Generated direct server link (WeTransfer
upload fallback)." with the reach/expiry and a "Why WeTransfer failed" disclosure. Checked: stand-in variants in Edge (icon-only menu
found by position; no-link menu -> diagnostics JSON), API fallback/expiry/bad-id/local-only tests, and the whole fallback in Edge
against a mock backend with the size limit forcing the skip (link label, notice, the link downloads the ZIP; 0 requests left the machine).

**WeTransfer: sender e-mail + e-mailed code (OTP), done by the person.** The second live run got past link mode (the controls dump
showed the "..." menu is a "Send email" / "Create link" radio pair, and the submit button is `data-testid="uploaderForm-transfer-button"`,
"Get a link") and stopped because WeTransfer requires a sender address: a required `input[type=email]` labelled "Your email", with the
hint "We ask for your email to keep the community safe." Flow now (the project owner's explicit request, after the first attempt to
automate this was refused by the session's permission policy - the code is typed by the person, never read from a mailbox):
1. Modal card: "Enter Sender Email" (`type=email`, validated, remembered in localStorage) -> "Proceed with Upload" ->
   `POST /api/export-wetransfer {token, sender_email}` (a malformed address is 422; no address at all skips straight to the server link).
2. The uploader fills "Your email" after choosing link mode, reads it back, clicks the submit test id, and fails with a named step if
   WeTransfer still shows its e-mail hint.
3. If a code screen appears (`CODE_RE`, plus visible code inputs: one-character boxes or a code/otp-named field) it calls `ask_code`:
   the worker sets the job to `requires_otp` (`session_id` = job id, `otp_deadline`, `otp_error`) and waits on a per-job queue with the
   browser open - Playwright's sync API cannot move to another request, so the "session" is that waiting thread. The card shows
   "WeTransfer sent a 6-digit verification code to <email>." with a numeric box and "Submit OTP & Generate Link" ->
   `POST /api/export-wetransfer/verify-otp {session_id, otp_code}` (422 malformed, 404 unknown, 409 not waiting). A rejected code asks
   again with the reason, up to `MAX_CODE_ATTEMPTS` (3); no code within `SIGNAGE_WETRANSFER_OTP_WAIT_S` (default 300 s, not the
   60 s asked for - reading the e-mail can take longer) or 3 rejections -> the 3-day server-link fallback. Status polling (1 s) carries
   every state; the POST itself answers {job_id} rather than `requires_otp`.
**The code has letters AND digits** (a real one read like `953GYV`; the first modal only took digits and dropped the letters): the box is `type=text`, `normalizeOtp` (utils/generateZip.js, tested) keeps A-Z/0-9, upper-cases typing and pasting (" 953-gyv " -> "953GYV"), caps at 8; Submit is enabled once anything is typed and needs 4+ characters; `verify-otp` strips spaces/dashes, upper-cases and accepts 4-10 of [A-Z0-9]; the uploader clears the box(es) and types the upper-cased code.
`SIGNAGE_WETRANSFER_URL` (TESTING ONLY; a Windows path must be a real `file:///D:/...` URI - a Git Bash `/d/...` path became `D:/d/...` once and the job correctly fell back) points a whole server's uploader at `tests/fixtures/wetransfer_standin.html`, whose `?code=1` screen accepts 953GYV: the full flow was run that way in Edge (email validation, upload, code prompt, wrong code -> "did not accept",
right code -> we.tl link; 0 requests left the machine). NOT run against the real wetransfer.com: whether WeTransfer shows a code at all,
and what that screen looks like, is still unobserved - the next real run's `_controls.json` will show it if the code step fails.

**Live PowerClip contents (scene version 3).** The scene export now renders every PowerClip
child to its own image (a bitmap child cannot be selection-exported inside the clip - E_FAIL, verified
live - so a duplicate is moved out onto the layer, exported and deleted; the same for EVERY leaf inside
a PowerClip, because a selection SVG export of a vector shape inside a clip is written EMPTY -
`viewBox="0 0 nan nan"` - which made version 2 draw e.g. a board's whole maroon bottom banner as nothing,
leaving white Tamil text on cream; `_svg_has_drawing` now rejects such files and falls back to PNG) and records `frame_rect` (true
when the frame is an axis-aligned rectangle, incl. a 4-node curve). For such a "live" PowerClip
(`model.js livePowerclip`) the canvas draws the contents itself inside `<clipPath id="powerclip-<id>">`
(`Canvas.jsx SceneImages`), instead of the flat container image, so moving/resizing a child or the whole
container updates real pixels during the drag; ops carry absolute coordinates as before. Non-rect or
rotated frames, or scenes without child images, keep the flat image + outline. Scenes cached before
version 3 that contain a PowerClip are rebuilt once when opened. Verified live: scene export of the
AL MADEENA board (352 leaves, 0 failures, 0 empty SVGs; a browser composite of the scene vs CorelDRAW's own page render
differs by 3.2/255 mean, was 20.3/255 with the empty SVGs); the browser drag itself
was NOT exercised in a real browser this session (no Playwright installed) - only unit tests
(`model.test.mjs`), the production build and the fake-COM export tests.

**Stacking order inside a PowerClip.** `order` (front/forward/backward/back) is now allowed on a PowerClip child (`_check_editable` /
`checkEditable` `allow_powerclip`, both engines): it restacks the child among the clip's own contents only - never out of the clip - and marks
the container `stale`; `reorder`/`group`/`ungroup`/`delete` are still refused there. The Properties panel's Order buttons are enabled for a
clipped object (only a lock disables them). Replay: `Replayer._op_order` calls the same `Order*` method and then, for a PowerClip child, settles
the clip's z-order against the shadow scene (`_settle` reads `PowerClip.Shapes`), and `verify()` compares it. **Not verified live in
CorelDRAW** that `Order*` on a clipped shape restacks within the clip - fake COM only; the settle step and verification cover it if it does
not. Golden: 4 `powerclip_cases` with a new `expect_children` check, the old "order is blocked" error replaced by "reorder is blocked".
**Layers-panel drag inside a PowerClip.** Clipped rows are draggable; `planDrop` and the `reorder` op (both engines) accept a reorder whose
`parent` is the object's CURRENT parent when it is in a clip (or the destination is/sits in one) - i.e. a restack among its siblings, at any
depth inside the clip - and refuse moving into or out of a clip ("cannot be moved into or out of a PowerClip"); a restack marks the clip
`stale`. Such rows have no "into" drop zone. Replay: same parent, so `Replayer._op_reorder` only `_settle`s the clip (PowerClip.Shapes); not
verified live in CorelDRAW. This also closes a gap: dragging an outside object into a group that sits inside a PowerClip used to be accepted
by the op engines. Tests: 2 golden cases + 1 golden error, a planDrop test (`model.test.mjs`), 4 replay tests.

### Frontend: `frontend/src/` structure

`main.jsx` wraps the app in `<BrowserRouter>` and imports `theme.css`
(design tokens - colors/spacing/radius/shadows, the single file to edit
for a theme change) then `styles.css` (component styles, built on those
tokens). `App.jsx` is now just the route table: `/editor/:jobId/:shopId`
is a top-level route with no sidebar (it opens in its own browser tab, so
there's no shell to share); everything else renders inside `Shell`
(sidebar + `<Routes>` for `Automation`/`RecentlyGenerated`).
`pages/Automation.jsx` holds all of Phase A's logic; `components/
UploadDropzone.jsx` is the standalone click-or-drag upload control.
`pages/RecentlyGenerated.jsx` is Phase B; `pages/EditorPage.jsx` (Phase C)
owns the editor state (ops + undo cursor, selection, autosave, shortcuts,
toolbar) and composes `src/editor/` - `Canvas.jsx`, `Rulers.jsx`,
`LayersPanel.jsx`, `PropertiesPanel.jsx`, plus the pure, unit-tested
`ops.js` / `model.js` / `units.js` / `view.js` and `alphaMaps.js`.

Verified by hand with Playwright (headless Chromium) driving the actual
dev servers - both engines: a full upload → preview → add-brand →
add-shop → convert → open-editor-in-new-tab pass against `MockEngine`,
and the same pass again against the real `CorelEngine` (confirmed real
step-by-step progress and real output files on disk, ~15s end to end for
a 120×48in same-as-master-size board). Zero browser console errors in
either run. No automated frontend tests yet (this phase's testing was
manual + scripted browser verification, not unit tests) -
`backend/tests/test_main_v2.py` covers the new API endpoints against
`MockEngine` (brand add/list, upload+preview extraction including the
non-zip and no-preview-found fallback paths, add-shop validation, convert
-to-done, path-traversal rejection on the file-serving endpoint).

## Layout rules (`backend/app/layout.py`)

Pure Python, unit-tested, no CorelDRAW dependency — takes a list of `Obj`
(id, name, kind, x, y, w, h in mm, optional `text`, origin bottom-left like
CorelDRAW) plus old/new page size and returns `Placed` objects for the new
size.

Role is decided by a name-prefix tag on the shape in CorelDRAW's Object
Manager (case-insensitive), checked in this order: `bg`, `frame`, `fixed`,
`shopname`, `text`, `logo`. Untagged shapes fall back to heuristics: ≥90% of
page area → `bg`; text shape → `text`; anything else → `logo`. (`shopname`
has no untagged fallback — see "Shop name replacement" below for how it's
found on real, untagged masters.)

| Role | Behaviour |
|---|---|
| `bg` | stretches to exactly fill the new page (0,0 to new_w,new_h) |
| `frame` | keeps its margins to the four page edges, stretches between them. If the new page is too small for the margins to fit, falls back to uniform scaling instead (with a warning). |
| `fixed` | keeps its original size; keeps its distance to whichever edge (or centre) it was closest to on the original page |
| `text` / `logo` | scaled by the uniform "fit" factor `min(new_w/old_w, new_h/old_h)`; centre point stays at the same proportional position on the page |
| `shopname` | like `text`/`logo` when not tiling; when tiling, moved into the gap between panel copies (see below) and its content replaced |

After placement, `text`/`logo`/`fixed`/`shopname` shapes are clamped inside
the page (respecting `safe_margin`) and annotated with warnings: clamped
position, scaled below 25%, aspect ratio changed by >2x/<0.5x, or larger than
the page. These warnings surface in the job's report JSON, not as hard
failures — the job keeps going and lets the designer review manually.

`compute_layout` is the single source of truth for placement math; both
engines call it with the same signature so their outputs are comparable.
Everything above (bg/frame/fixed/text/logo, `tile=False` by default) is
**unchanged** from before this round of work, and the original 7 unit tests
still pass unmodified — tiling and shop-name replacement are additive,
opt-in (`tile=True`, `shop_name=...`) parameters that can't change existing
callers' output.

## Designer dataset analysis

`signage_dataset/` has two real brands (Agarpathi, dalmia), each a master
`.cdr` (the first/lowest-numbered file) plus the designer's own manual
resizes of it, named `"<code> - <W> X <H> <unit> - <type> - <shop name>"`.
Studying these against the master (via `backend/tools/dump_objects.py` +
manual comparison, before writing any rule) found:

- **Real masters are completely untagged** — no `bg_`/`logo_`/`text_` name
  prefixes anywhere in either brand. The name-tag system only helps masters
  built specifically for this tool going forward.
- **Logos are often raw ungrouped curves, not CorelDRAW groups.** The dalmia
  master's two logos are ~130 individual `curve` shapes sitting directly on
  the layer (only 4 of 142 shapes are actually inside a `group`). This means
  per-shape scaling (each shape resized around its own centre, the existing
  `text`/`logo` rule) would visually shatter such a logo into misaligned
  fragments on **any** resize, tiled or not — there is no reliable signal
  that ungrouped fragments belong together besides bounding-box proximity.
  This wasn't fixed (out of scope: it needs a clustering heuristic or, more
  reliably, the design team grouping logos in future masters) — flagging it
  here since it'll look like a regression the first time someone tests a
  dalmia-shaped master.
- **Wide/tall boards repeat the artwork, they don't just shrink it.** A
  35×4ft board from a 12×4ft master (Agarpathi) has the entire
  logo+product-name+photo panel duplicated **twice**, side by side, each
  copy near its original size — not one copy shrunk to fit. A 2×6ft board
  from the same master stacks the panel **twice**, vertically. This is what
  `compute_layout(..., tile=True)` now reproduces.
- **The shop-name text moves into the gap between copies**, both for
  horizontal tiling (centred in the horizontal gap) and vertical tiling
  (centred in the vertical gap) — never inside a repeated panel.
- **The designer redesigns internal layout for extreme ratios, not just
  scale+repeat.** The 2×6ft case's panel elements were repositioned
  *closer together* horizontally (not just scaled down uniformly) to avoid
  becoming illegibly thin — confirmed by `backend/tools/analyze_designs.py`
  showing our rigid contain-fit-scaled panel covers only ~60% of the new
  page height there, vs ~80% in the designer's hand-tuned version. This is
  a real, currently-unclosed gap: our tiling scales the whole panel as one
  rigid unit (see below for why), which is a defensible first approximation
  but will never fully match manual creative redesign.
- **Elements can be dropped entirely for very constrained sizes** — the
  2×6ft file has no product-photo bitmap and no badge/logo group at all,
  vs. 7 top-level objects in most other resizes; the designer judged there
  wasn't room and cut them. Nothing automatic does this; it's a known,
  accepted gap.
- Raw dumps for the ~19 files sampled (masters + wide/tall/square/small
  variants of Agarpathi, and dalmia's 120×48/216×48/others) are kept in
  `backend/dataset_analysis/dumps/*.json` for reference — regenerate with
  `dump_objects.py` if the dataset changes.

## Tiling (`compute_layout(..., tile=True)`)

Opt-in, off by default. When enabled:

1. `_tile_plan` compares `new_w/page_w` and `new_h/page_h`; whichever is
   larger and exceeds `TILE_ASPECT_THRESHOLD` (1.4) decides the tiling axis,
   and the tile count is `round()` of that ratio. Below the threshold on
   both axes: no tiling, falls through to the plain per-object scaling above
   (this is why the existing tests, none of which pass `tile=True`, are
   unaffected regardless of this logic).
2. Every `logo` object (graphics/groups/bitmaps) becomes part of "the
   panel" — their combined bounding box is treated as one rigid unit,
   contain-fit-scaled (`min(cell_w/panel_w, cell_h/panel_h)`) into an equal
   cell per copy, and centred within its cell. This is what gives the
   "even gaps" — gaps emerge from centring, not a separate gap parameter.
   **`text` that isn't the shop name is never tiled**, even when the logos
   beside it are — found via the dalmia validation batch (below): a
   standalone small-print line (e.g. "authorized dealer" / phone number)
   got duplicated into every panel copy, which real designer files never
   do. Fixed by restricting the panel to `role == "logo"` only; `text`
   always uses the plain per-object scale+centre path regardless of
   tiling. Covered by
   `test_tile_does_not_duplicate_standalone_footer_text`.
3. The shop-name shape(s) are placed as their own rigid unit, centred on
   the page, at the same scale as the panel (capped at 1.0 so it's never
   enlarged past the master's own size).
4. Validated against real files via `backend/tools/analyze_designs.py`
   (see below): correctly predicts "no tiling" and 2-panel vertical tiling
   exactly; **overshoots by one panel on the most extreme case sampled**
   (a 35×4ft board predicts 3 horizontal panels, the designer used 2) — a
   known, documented approximation from a single data point, not something
   to over-fit further without more real examples.

`CorelEngine` (COM) realizes tiling by reusing the original shape for tile 0
and calling `Shape.Duplicate()` for tiles 1..N-1 of each panel object, then
positioning every copy per `compute_layout`'s output — see `engines.py`.

## Validation: all 13 dalmia files

`backend/tools/validate_all.py dalmia` generates every real dalmia shop
(parsed from its own filename via `batch_import.parse_shop_lines`) from the
dalmia master through the **real `CorelEngine`** (live COM, via the worker
-subprocess architecture in "Production hardening" below), then compares
the result against that shop's own real designer file - both dumped and
flattened to leaf shapes (`_flatten_leaves`: every non-`group` shape
regardless of nesting) so a real file that happens to group its logos
differently from the master doesn't produce a false mismatch. Matching is
greedy-nearest-centre within the same role bucket **and** within 2x size of
each other (`_size_similar`) - added after the first pass matched two very
differently-sized fragments among ~130 packed dalmia logo curves and
reported a meaningless ~48% "difference" that was actually a bad match, not
a real one. `TOLERANCE_PCT = 2.0` / `TOLERANCE_LOOSE_PCT = 5.0` (top of
`validate_all.py`) are the two pass/fail cutoffs reported for every board
(as % of the target page's width/height on the worst matched object);
change them there. A real designer file's own dump is cached under
`dataset_analysis/real_dumps_cache/<brand>/` after the first COM read, so
re-running validation (e.g. after a rule change) doesn't re-dump files
whose real content hasn't changed.

**Result: 4/12 pass at 2%, 6/12 at 5%** (board 12 excluded from both - see
below). Output in
`backend/dataset_analysis/validation/dalmia/validation_report.{json,md}`.

| File | Target | Tile | Objects ours/real | Max diff % | 2% | 5% |
|---|---|---|---|---|---|---|
| 02 (120×48, = master's own size) | 3048×1219 | none | 138/138 | 0.0 | PASS | PASS |
| 02 (180×48) | 4572×1219 | x,2 | 272/116 | 77.1 | FAIL | FAIL |
| 03 (120×48) | 3048×1219 | none | 138/138 | 1.9 | PASS | PASS |
| 05 (120×48) | 3048×1219 | none | 138/136 | 4.8 | FAIL | PASS |
| 06 (180×60) | 4572×1524 | x,2 | 272/115 | 73.8 | FAIL | FAIL |
| 09 (144×60) | 3658×1524 | none | 138/138 | 14.5 | FAIL | FAIL |
| 10 (144×60) | 3658×1524 | none | 138/138 | 2.0 | FAIL | PASS |
| 11 (216×48) | 5486×1219 | x,2 | 330/116 | 80.7 | FAIL | FAIL |
| 11 (240×60) | 6096×1524 | x,2 | 272/115 | 73.7 | FAIL | FAIL |
| 12 (120×60) | 3048×1524 | none | 138/136 | 49.7 | *excluded* | *excluded* |
| 13 (144×60) ×2 variants | 3658×1524 | none | 138/138 | 1.8 | PASS ×2 | PASS ×2 |
| 14 (120×48) | 3048×1219 | none | 138/138 | 10.6 | FAIL | FAIL |

`KNOWN_OUTLIERS` in `validate_all.py` excludes board 12 (120×60) from the
pass-rate denominator specifically - its real file shows a large,
systematic whole-composition shift (up to ~1.5m, confirmed not a matching
artifact: size diffs on the worst objects are tiny, position diffs are
large and vary per object) unlike every other board in its size class,
looking like a one-off manual recomposition rather than a general rule gap.
It's still generated and reported, just not counted for/against the rate.

`backend/tools/failure_analysis.py <report.json>` groups the failures into
2 causes (a board can only land in one, picked by whichever signal is
largest - see the script for the exact rule): "we tiled but designer didn't
(over-tiled)" (the 4 `x,2` boards) and "logo/text position or size drift"
(the rest, mostly small 2-14.5% hand-nudges plus board 12's outlier shift).

### Per-brand tiling rule attempt (`backend/app/brand_rules/`) - tried, made it worse

`backend/tools/derive_brand_rules.py` clusters the master's `logo` shapes
by bounding-box proximity (margin tuned by hand to 20mm - see the script's
`cluster()` docstring for why 3mm and 50mm+ both give worse clusters), then
matches each cluster's size against the real wide files to derive, from
data rather than a guess, which cluster is "the small triangle that
repeats" vs "the wordmark that also repeats at extreme aspect" vs badges
that never repeat - `backend/app/brand_rules/dalmia.json` is the result,
loaded automatically by `CorelEngine` via `layout.load_brand_rule(brand)`
whenever `shop["brand"]` is set. `_place_brand_ruled_panel` in `layout.py`
tiles each named group independently instead of one rigid panel, with
"never repeats" groups anchored at their original proportional page
position (an earlier version gave them their own tile cell too, which
measurably made every tiled board's diff *worse*, not better, by starving
the actually-repeating groups of space - fixed, but see below).

**Honest result: this made the tiled boards worse, not better** (77-81%
max diff above, vs 67-77% for the same 4 boards under the old single-panel
rule). The per-group cell-division approach is more *semantically* correct
(it matches which graphic the designer actually repeats) but the designer
also freely repositions/rescales elements per board in ways this rigid
per-group tiling still can't capture - same "manual creative redesign"
limitation as ever, now with an extra layer of approximation on top rather
than a fix for it. **The brand rule stays wired in** (removing it isn't
obviously better either, and the underlying data - which cluster repeats
when - is genuinely useful for a future, less rigid layout strategy) but
should not be described as an improvement until it's actually shown to be
one on more data.

**Net honest read**: the tool gets same-size and mildly-resized boards
right, and the tiling-specific fixes made along the way (stop tiling
footer text; per-group brand rule) are each individually correct/justified
by real evidence, but neither closed the *dominant* gap: wide-format tiling
is fundamentally a creative decision the designer makes per board, not a
geometric rule to discover. This is the same limitation documented in
"Designer dataset analysis" throughout - now measured from several angles,
not closed.

## Shop name replacement

Since real masters are untagged, `compute_layout` can't find "the" shop-name
text by name. Two ways to identify it:

- **Explicit tag** (recommended for new masters going forward): name the
  shape `shopname` (or `shopname_...`) in CorelDRAW's Object Manager.
- **Content match on old/untagged masters**: `find_shopname_ids(objects,
  old_name, old_name_local)` does a case-insensitive substring match against
  known text on the master (e.g. the shop name the master currently shows)
  and returns the matching object id(s) to pass as `shopname_ids=`.

Once identified, pass `shop_name`/`shop_name_local` to `compute_layout`; the
returned `Placed.text` (and `Placed.font`, for Tamil content) is what
`CorelEngine` writes back via `shape.Text.Story.Text = ...` /
`shape.Text.Story.Font = ...` (see COM notes below — the property is
`Font`, not `CharacterFontName`).

**Font choice matters and was verified, not assumed**: `TAMIL_FONT` in
`layout.py` is `"Nirmala UI"` — the Indic UI font that ships with Windows —
**not** `"Noto Sans Tamil"`, which is commonly recommended online but is
**not installed** on a stock Windows/CorelDRAW machine. Confirmed on this
dev machine: setting `Text.Story.Font = "Noto Sans Tamil"` silently no-ops
(CorelDRAW doesn't raise; the property reads back `""` afterwards) while
`"Nirmala UI"` sticks and renders correctly. `CorelEngine._set_replacement_text`
reads the font back after writing it and appends a warning if it didn't
stick, rather than trusting the write blindly — because CorelDRAW won't
tell you it failed. If a target machine has neither font, tofu boxes are
back; check `Text.Story.Font` interactively before hardcoding a new default.

**What real designer files actually use, read from every cached dump**:
every text shape in all 13 cached dalmia real files reports `font="Arial"`
for its Tamil content (the "authorized dealer" footer and the shop-name
text) and `font="Yu Gothic Medium"` for the Latin phone/GST line - both
installed on this machine (confirmed via
`System.Drawing.Text.InstalledFontCollection`), alongside `Nirmala UI`
(installed) and `Noto Sans Tamil` (not installed, as above). Arial has no
native Tamil glyphs, so this almost certainly means Windows' automatic
font-linking/fallback is silently substituting a real Tamil-capable font
at *render* time while the document's own metadata still says "Arial" -
a well-known Windows behaviour for complex scripts, not a documented
CorelDRAW feature, and one this codebase doesn't rely on: `TAMIL_FONT`
sets `"Nirmala UI"` explicitly for replacement text rather than leaving the
font as whatever Latin font the master happened to use, because relying on
automatic fallback being configured the same way on every machine that
runs a job is a fragile assumption to build on. Agarpathi's text shapes
all report `font=None` (COM's `Text.Story.Font` raises when a text run
mixes fonts/sizes internally - `dump_objects.py`'s `_text_info` already
catches that and records `None` rather than guessing) - its real font(s)
aren't currently readable this way; would need per-character-run font
reads (`Story.Range(i, i+1).Font`), not implemented.

## Wide-board panel sequence (dalmia) - derivation, implementation, and two real bugs found along the way

`derive_brand_rules.py`'s automated cluster-size matching (see "Per-brand
tiling rule attempt") got the *identity* of the repeated elements wrong,
which is why using it made results worse. Re-examined by eye against the
real rendered PNGs of all 4 wide dalmia boards
(`dataset_analysis/compare/*/real.png`, from `cache_real_renders.py`) plus
their clustered geometry (`derive_brand_rules.cluster()` on each file's
`logo`-role leaf shapes), the master's 3 non-text graphics are, left to
right on the *master itself* (120x48, untiled):

1. **Tamil logo card** - white card, Tamil "டால்மியா பாரத் சிமெண்ட்"
   wordmark + icon. ~50 leaf shapes, height ≈ 0.67 of page height.
2. **"Roof Column Foundation Expert" graphic** - white house-shaped
   silhouette, no card background. ~58 leaf shapes, height ≈ 0.49 of page
   height.
3. **Small "Dalmia Bharat CEMENT" badge** (English) - sits in the
   top-right corner, much smaller than the other two. ~24 leaf shapes,
   height ≈ 0.16 of page height.

On every sampled wide board, all three are laid out left-to-right, evenly
spaced by centre-to-centre gap (consistent with the existing panel-tiling
approach's "N evenly-spaced cells" logic - the spacing math isn't the part
that's wrong), but **as three (or four) *different* elements, not one
element repeated**:

- The **Tamil logo card** appears once, close to its master proportional
  height (0.60-0.67 across all 4 samples) - never repeated, never resized
  beyond that range.
- The **small badge is enlarged** to roughly match the Tamil card's height
  (0.16 -> 0.60-0.67) and re-rendered as a matching white "card" (still
  showing "Dalmia Bharat / CEMENT", English) - this is what
  `derive_brand_rules.py`'s blind size-matching mistook for "the Tamil
  card, duplicated": confirmed by eye on `02 (180x48)` and `11 (216x48)`'s
  real renders, the left and right cards show **different content**
  (Tamil vs. English), not two copies of the same graphic. Always exactly
  one copy, never further enlarged/shrunk beyond matching the Tamil card.
- The **Roof Column Foundation Expert graphic** is the one true *repeat*
  element, used as a spacer/filler between the two branded cards, at
  roughly its master proportional height (0.44-0.49 across all samples):
  **once** at aspect ratios up to ~4.0 (`06` 180x60=3.0, `02` 180x48=3.75,
  `11-240` 240x60=4.0 - Tamil card / Roof graphic / Dalmia Bharat card,
  3 panels total), **twice** at the widest sampled aspect (`11-216`
  216x48=4.5 - Tamil card / Roof graphic / Dalmia Bharat card / Roof
  graphic again, 4 panels total, confirmed by eye against its real render).
  The exact aspect threshold for the 3-to-4 panel jump is only bounded to
  "somewhere in (4.0, 4.5]" by this data - a single sample, not something
  to over-fit further without more real wide files at intermediate
  aspects.
- A thin, full-page-width decorative accent strip (a `logo`-role shape,
  ~10% of page height, not visible as a distinct graphic in the renders -
  likely a background rule line) appears in 3 of the 4 samples
  (`02`, `11-216`; not detected in `06` or `11-240`'s clustering, cause
  not investigated further) - unrelated to the panel sequence itself.

This is a materially different, more specific rule than either the
original generic "duplicate the whole panel as one rigid unit" tiling or
`derive_brand_rules.py`'s "which cluster repeats, by blind size match" -
it names the *distinct roles* (fixed card, enlarged card, repeating
filler) rather than treating tiling as "duplicate everything" or "duplicate
whichever cluster's size matches."

### Implementation: `layout._place_panel_sequence` + `brand_rules/dalmia.json`'s `panel_sequence` key

Per GATE 2 feedback (Phase 2's general example-interpolation approach was a
proven regression - see "Example-based layout engine" below - while this
specific, hand-verified rule was worth keeping), the sequence above is now
implemented directly rather than left as a research note. `compute_layout`
checks `brand_rule.get("panel_sequence")` before falling back to the older
`groups` (aspect-repeat-table) schema, which stays available for a future
brand that fits it. `_place_panel_sequence` buckets each panel object into
whichever named group's *master bbox, expanded by a margin* contains its
centre (not "whichever group is nearest," which - see below - swept an
unrelated decorative shape into the wrong bucket), places the sequence for
the target aspect (`sequence_3`/`sequence_4`) into N evenly-spaced cells,
and scales/positions each group by its own `target_h_frac`/`target_cy_frac`
(fractions of page height, averaged from the 4 real samples) rather than a
uniform contain-fit - this is what lets the badge be *enlarged* instead of
kept at its native size. Anything that doesn't belong to a named group
(the accent strip) falls back to an ordinary proportional-position
placement, same as `_place_brand_ruled_panel`'s "never repeats" groups.

**Two real bugs found and fixed while getting this correct, both confirmed
live against 02 (180x48)'s actual rendered PNG, not just numbers:**

1. **Nearest-group matching (no size/containment gate) swept the
   full-width decorative accent strip into the `tamil_card` bucket.** Its
   centre is geometrically closer to the Tamil card's master bbox centre
   than to the other two groups, even though it isn't part of any of
   them - a single 2345mm-wide stray shape then blew up that whole
   bucket's bounding box, corrupting its scale and centring. **Fixed** by
   requiring a shape's centre to actually fall inside a group's bbox
   (expanded by 50mm) to join it; anything outside every group's bbox
   falls back to a proportional-position placement instead of being
   force-assigned. Confirmed: before the fix, the accent strip visually
   vanished from its correct position and warped the Tamil card's layout;
   after, it renders as a normal thin horizontal line, unrelated to the
   panel sequence.
2. **A pre-existing, more consequential bug in `CorelEngine._resize_and_tile`
   itself, unrelated to this feature but only surfaced by it**: whether a
   placed shape reused the original COM shape or called `.Duplicate()` was
   decided by checking if its id's tile-index suffix was literally `"0"` -
   correct only when every group's copies are always numbered `0..N-1`
   starting at 0 (true for `_place_tiled_panel` and `_place_brand_ruled_panel`'s
   *repeating* groups, false for any group appearing once at a non-zero
   sequence position - which is most of them in a panel_sequence, and was
   already true for `_place_brand_ruled_panel`'s "never repeats" badge,
   likely unnoticed because a small badge's leftover "ghost" copy is much
   less visually obvious than a full logo's). The result: the *original*
   shape was left untouched at its old position while a *new*, correctly
   -placed duplicate was also created - two visible copies of the roof
   graphic side by side on the first fixed render, confirmed by comparing
   `orig` (pre-transform) vs. placed coordinates in the job's own
   report.json. **Fixed** in `engines.py` by tracking which `base_id`s have
   already been placed *across the whole job*, independent of the id's
   tile-index string: a base_id's first placement reuses the original
   shape, every subsequent placement of the *same* base_id duplicates -
   correct for every panel scheme, not just this one.

**A third bug, found once all 4 boards could be compared**: the first fix
used a *flat average* of `target_h_frac`/`target_cy_frac` across all 4
samples. This visibly overflowed 06 (180x60)'s enlarged badge off the page
edge, because that board's real proportions don't sit at the average.
**Fixed** by replacing the flat average with a per-group `size_table`
(one `{aspect, target_h_frac, target_w_frac, target_cy_frac}` row per
sampled board, read directly off its own real render) and
`layout._interp_size_table`, which linearly interpolates between the two
nearest sampled aspects at layout time (clamped, never extrapolated,
outside `[3.0, 4.5]`). A **fourth** bug surfaced fixing this: the enlarged
badge card's real width/height ratio does not match its master shape's own
ratio (it's redesigned to roughly match the Tamil card's proportions, not
algebraically scaled up) - deriving width from height via the master
bbox's aspect ratio *also* overflowed the cell even with per-aspect
sizing. Fixed by storing `target_w_frac` explicitly (measured, not
derived) and scaling by `min(implied-scale-from-height,
implied-scale-from-width)` in `_place_panel_sequence`, so a group is never
stretched past either constraint.

**Final result, confirmed on all 4 wide boards** (visual `combined` score,
see "Metrics suite"; `validate_all.py`'s leaf-shape geometry diff % kept
alongside for reference, but see below for why it's the less trustworthy
number here):

| Board | Aspect | Before (single rigid panel) | After (panel_sequence) | Geometry diff % (after) | 0.75 pass? |
|---|---|---|---|---|---|
| 02 (180x48) | 3.75 | 0.380 | **0.730** | 65.4% | FAIL (just under) |
| 06 (180x60) | 3.00 | 0.411 | **0.669** | 63.5% | FAIL |
| 11 (216x48) | 4.50 | 0.347 | **0.538** | 84.0% | FAIL |
| 11 (240x60) | 4.00 | 0.403 | **0.716** | 68.2% | FAIL |

All 4 improved substantially (+0.10 to +0.35), none yet cross the
provisional 0.75 threshold - 02 (180x48) is closest, 0.02 under. 11 (216x48)
improved the least in absolute score, plausibly because it's the one board
using `sequence_4` (4 panels, the least-evidenced regime - a single
sample informed its `aspect_split`/sizing to begin with) and/or because its
own real design has the most going on (4 distinct elements to place
correctly instead of 3). None of the 4 pass yet; this remains an
**honest partial improvement, not a fix** - the leaf-shape geometry diff %
barely moved for any of them (77-85% before and after) despite the large
visual gains, consistent with that metric's already-documented noise on
~130-fragment packed curve matching (see "Validation: all 13 dalmia
files") - the visual score is the more trustworthy signal for judging this
specific change. **Net effect on the full dalmia set: still 7/12 pass at
0.75** (unchanged - the 4 wide boards were already the only failures in
the non-outlier set, and stay failures, just less badly wrong).

**Fixed properly, not just worked around again**: several `--only <substr>`
reruns during this work (without `--resume`) again truncated
`validation_report.json` to just the filtered board(s), as originally
documented above - it happened at least twice more in this round, each
time recovered by hand (rebuilding the missing boards' entries offline
from their still-valid cached dumps). Recurring often enough to be a real
design flaw, not a one-off mistake, so `validate_all.py` now always seeds
`boards` from the existing `validation_report.json` (if one exists)
regardless of `--resume`: without `--resume`, every previously-recorded
board *not* about to be regenerated this run is kept, and only the
boards actually being rerun get their entries replaced; `--resume` keeps
its existing behaviour (skip regenerating boards that already succeeded).
A bare `--only <substr>` run can no longer silently discard the rest of
the report.

### Three more fixes from a visual review of the wide-board renders

Found by looking at the actual rendered PNGs rather than only the scores,
after the per-aspect sizing work above:

**a) The enlarged badge needs a white card, not just bigger bare
logo/text.** Every real wide board puts the "Dalmia Bharat Cement" badge
inside a white card matching the Tamil card's own size and drop shadow -
confirmed by eye earlier (see the panel-sequence description above) but
not actually built: `_place_panel_sequence` was scaling up the master's
*bare* badge shapes (icon + English text, no card - the master's own
badge sits directly on the blue background) to card size, which visually
reads as an oversized logo, not a matching card.

Fixed with a new `card_from` mechanism, since the badge has no card
background of its own to scale: a group can declare `"card_from":
"<other_group_id>"`, and `_place_panel_sequence` then (1) splits the
*template* group's own shapes into "background" vs. "content" by bounding
-box area ratio (`BG_AREA_RATIO = 0.5` - a shape covering half or more of
its group's own bbox is a card/shadow, not logo/text content; verified
against the master's Tamil card: two duplicate-pair shapes at 94.7%/79.5%
area ratio are clearly the white card + drop shadow, the remaining ~46
shapes at 1-5% each are the icon and Tamil text), (2) duplicates the
template's background shapes, sized via the *template's own* `size_table`
(both cards measure out nearly identical anyway - see the per-aspect data
above) but positioned in the borrowing group's own sequence cell, and (3)
places the borrowing group's own content centred inside at a measured
`card_content_frac` (`{w, h, cx, cy}`, fractions of the card) - read
directly off the master's own Tamil card: its icon+text occupies 80.4% of
the card's width, 50.0% of its height, centred at (45.8%, 56.4%) of the
card's box. `enlarged_badge_card` in `brand_rules/dalmia.json` now uses
`card_from: "tamil_card"` instead of its own `size_table`. Covered by
`test_panel_sequence_card_from_gives_the_bare_group_a_matching_card_background`
and `..._content_matches_measured_proportion`.

**Two more bugs found getting `card_from` to actually render correctly**
(both only visible by looking at the rendered PNG, not from any number):

- **The duplicated card rendered on top of the badge's own content,
  hiding it completely.** `Shape.Duplicate()` stacks the new card directly
  above the template it was copied from in z-order, not above *this*
  group's own content, which keeps whatever z-order position it already
  had in the master. Fixed by adding `Placed.bring_to_front` (set on the
  content shapes placed by `card_from`) and having `CorelEngine` call
  `Shape.OrderToFront()` on them right after positioning.
- **Even visible, the wordmark text was invisible on the new card**: it
  rendered as the colourful icon with no "Dalmia Bharat CEMENT" text at
  all. Read the shapes' actual fill via COM to find out why - the badge's
  English wordmark curves are a uniform CMYK (0,0,0,0) - pure white,
  styled for the master's own dark blue page background - while the Tamil
  card's own text is CMYK (95,80,4,0), a dark blue. White-on-white is
  invisible. Fixed by adding `Obj.fill_cmyk` (read via COM,
  `Shape.Fill.UniformColor` - note `.RGBRed` etc. raise "Incompatible
  color model" on a CMYK fill, so CMYK is read directly rather than tried
  as a fallback) and `Placed.recolor_cmyk`: `_place_panel_sequence` finds
  the template's own non-white content colour and applies it to any
  *pure-white* content shape being moved onto the borrowed card (never
  touches the icon's own multi-coloured curves, which aren't white).
  `CorelEngine` applies it via `Shape.Fill.UniformColor.CMYKAssign(...)`
  and reads the colour back to confirm it stuck, the same verify
  -don't-trust pattern already used for font writes (see "Shop name
  replacement"). Covered by
  `test_panel_sequence_card_from_recolors_white_content_to_match_template_text`
  and `..._does_not_recolor_non_white_content`.

**b) The shop name belongs in the bottom bar, not a gap between panels.**
The gap-centring shopname placement (`_place_shopname_in_gap`, built for
the original single-rigid-panel tiling, before `panel_sequence` existed)
put the shop name near the page's vertical centre on wide boards instead
of its normal spot in the bottom text bar alongside the "authorized
dealer" footer and phone/GST line - confirmed by eye on 11-216's render,
where it showed up as small text wedged between two panels. Real boards
(tiled or not) always keep it in the bottom bar. Fixed: `compute_layout`
now only defers the shop name to the gap-centring path for the generic
single-panel/brand-ruled-groups tiling schemes; a `panel_sequence` brand
rule lets it fall through to the same plain proportional scale+centre
placement every other fixed text shape uses, landing it back in its
natural bottom-bar position. Covered by the existing panel_sequence tests
(none of which special-case the shop name's placement path anymore) plus
a new metrics.py check:

`metrics.layout_checks`'s new `shopname_in_bottom_bar` check - fails if
any shopname shape's vertical centre sits more than
`layout.bottom_bar_margin_mm` (30mm) outside the y-range spanned by the
other `text`-role shapes (footer, phone/GST) on the SAME generated board;
passes trivially if there's no shopname or no other fixed text to compare
against. This is a hard-fail check (counts toward a board's overall
PASS/FAIL in `build_metrics_report.py`, same as `text_overlap`).

**c) Board 12 (120x60) - analyzed, not turned into a rule.** Compared 12's
real file against 03 (120x48, a same-master-width board at the master's
own height) to test the hypothesis that a taller-than-usual board scales
content to fill the extra height rather than leaving it at the uniform
-fit size. Found: the Tamil card's height fraction *did* grow (0.668 in
03 -> 0.612 in 12) more than the "no scale" prediction (0.534, i.e.
`0.668 / (60/48)`) but less than "scaled by the full height ratio, same
fraction as 03" (0.668 unchanged) - a partial effect, not a clean factor.
More tellingly, clustering 12's own logo shapes (even at a widened 40mm
margin, well above the 20mm normally used) merges what are two separate
groups on every other sampled board (the roof graphic and the badge) into
one 82-shape blob - the designer moved them closer together on this
board, not just resized them uniformly. This is the same "manual creative
redesign" pattern already documented for extreme aspect ratios (see
"Designer dataset analysis"), and there is exactly **one** 120x-tall
-height sample in the whole dataset to test any hypothesis against - per
the explicit instruction driving this analysis (apply a rule only if it
holds for more than one sample), **no rule was implemented**. Board 12
stays exactly as already labelled: a known outlier
(`validate_all.py`'s `KNOWN_OUTLIERS`), reviewed by hand, not covered by
any geometric rule - a REVIEW case, not a fixable gap.

**Final result on all 4 wide boards, after (a) and (b) above** (visual
`combined` score; geometry diff % kept for reference only - see "Metrics
suite" for why it's the less trustworthy number for this kind of change):

| Board | Aspect | Before this round | After | 0.75 pass? |
|---|---|---|---|---|
| 02 (180x48) | 3.75 | 0.380 | **0.785** | **PASS** |
| 06 (180x60) | 3.00 | 0.411 | **0.727** | FAIL (just under) |
| 11 (216x48) | 4.50 | 0.347 | **0.620** | FAIL |
| 11 (240x60) | 4.00 | 0.403 | **0.821** | **PASS** |

Two of the four wide boards now pass outright (02, 240x60), a genuine
first for this master's tiled boards. **Net effect on the full dalmia
set: 9/12 pass at 0.75, up from 7/12** before this round. 06 (180x60) is
close (0.02 under); 11 (216x48) - the one board using `sequence_4`, the
least-evidenced regime (a single sample informs its sizing) - remains the
furthest from passing, consistent with it being the hardest case in the
dataset from the start.

## Per-shop content replacement (phone / GST / address)

Real masters embed shop-specific contact details, not just the shop name -
found live in every cached dalmia real dump: a combined text shape reading
`"Phone No. <digits>\rGST NO. <code>"` (two lines, `\r`-separated; the GST
line is sometimes absent entirely, e.g. the M Pandi file). Before this,
`CorelEngine` never touched this shape, so every generated board kept the
*master's own* phone/GST digits regardless of which shop it was for -
found via GATE 1 review.

Unlike the shop name, the phone/GST shape's **label** text ("Phone No." /
"GST NO.") is stable across every real file even though the value after it
is exactly what's different per shop - the opposite of the shop-name case,
where the *value* (a known old shop name) is what's stable enough to match
against. So it's found by that label instead:

- `find_contact_ids(objects)` (`layout.py`) - regex match for `phone\s*no`
  or `gst\s*no` (case-insensitive) in a text shape's content, no per-shop
  hint needed.
- `_contact_replacement(o, phone, gst, address_lines)` rebuilds the text,
  substituting only the value after a matched label (preserving whatever
  punctuation/spacing the master's own label already uses) or appending a
  new "Phone No. …" / "GST NO. …" line if that label wasn't present yet
  (e.g. adding a GST line to a master variant that didn't have one).
  `address_lines`, if given, are appended as further lines in the same
  shape - there's no separate address shape in any sampled master, so this
  is the only place free-text per-shop content like an address can go.
- `compute_layout(..., phone=, gst=, address_lines=, contact_ids=)` sets
  the result on `Placed.text` exactly like shop-name replacement, and
  `CorelEngine` writes it via the same `_set_replacement_text` (renamed
  from `_set_shopname_text` - it's no longer shopname-specific).

Recognized `shop` dict keys: `phone`, `gst`, `address_lines` (list[str]).
The frontend (`App.jsx`) collects these plus `shop_name_local` per shop row
and omits any that are blank (sending `""` would mean "replace with
blank", not "leave alone" - `compute_layout` treats `None` as "don't touch
this field", so the frontend must never send an empty string for a field
the user left blank).

### Text-fit: shrinking/wrapping replacement text so it doesn't collide

Real shop names vary wildly in length ("M Pandi" vs. "SAFI STEEL TRADERS
PRIVATE LIMITED") but CorelDRAW artistic text (what these masters use -
fixed absolute font sizes, not an auto-fit paragraph frame) doesn't wrap on
its own. Before this fix, a long replacement name grew wider than the
shape's layout box and could collide with the neighbouring phone/GST line -
confirmed live on two real boards (14 "A 1 SEVAN STAR ENTERPRISES", 09
"SAFI STEEL TRADERS PRIVATE LIMITED") via metrics.py's new `text_overlap`
check (below), both regenerated and reverified clean after the fix (14's
shop-name font shrank from the master's 178pt to 129.8pt to fit; no wrap
needed in either sampled case, but the wrap path is exercised by
`test_engines`-adjacent... - no, there's no COM-based unit test for this,
see "Tests").

`CorelEngine._fit_text(shape, target_w_mm, warnings)`, called right after
`_set_replacement_text` writes new text: if `shape.SizeWidth` exceeds the
layout's target width (`Placed.w`) by more than 5%, shrinks `Story.Size` in
10% steps down to `layout.MIN_TEXT_PT * 1.3` (a floor with headroom below
the floor itself), then, if still too wide, wraps at the space nearest the
middle of the string (one manual `\r` line break - these are artistic text
shapes, so this is the only way to get a second line). Gives up and appends
a warning rather than looping forever or shrinking illegibly.

`layout.MIN_TEXT_PT = 40.0` is a floor derived from real data, not a
guess: every text font size across all 13 cached dalmia real files ranges
80.7-300pt (large-format signage read from a distance, not desktop print).
40pt sits comfortably below the smallest real value - it exists to catch a
genuinely broken shrink loop, not to model legibility-at-viewing-distance
(never specified anywhere in this dataset). `metrics_config.json`'s
`layout.min_text_pt` uses the same value for its own legibility check -
keep the two in sync if either changes.

## Example-based layout engine (Phase 2, `backend/app/example_engine.py`) - honest result: not yet an improvement

Built to replace the rigid geometric rules with predictions drawn from how
real designers actually resized this exact master, instead of a formula.
Pure Python, no CorelDRAW - not yet wired into `CorelEngine`/`compute_layout`
(this is offline validation work; see "Not yet integrated" below).

**Design**: a master's shapes are grouped into a fixed set of *named
entities* (`master_entities`) - `bg` (1), `shopname` (1), `text` (each
individual non-shopname text shape, e.g. the footer and phone/GST lines,
tracked separately from each other), and `logo_cluster` (bbox-proximity
-clustered logo shapes, same `derive_brand_rules.cluster()` used
elsewhere). For each real board, `match_entities` matches its own entities
back to the master's, then `build_examples` records each match as a
page-size-independent transform (proportional centre + size + repeat
count) in `backend/brand_data/<brand>/examples.json`. To predict a new
target size, `predict_entities` finds the nearest example(s) by aspect
ratio - uses one directly if within 3% aspect tolerance, else linearly
interpolates between the two nearest - and `predict_layout` lets a brand
register a hand-verified override (`WIDE_PANEL_OVERRIDES`) for regimes
where geometry-only matching is known to fail (see next).

**A real matching bug, found and fixed**: `find_shopname_ids` only
recognizes a board's shopname text when it happens to still contain the
*master's own* old shop name - true only for same-shop resizes. For every
other real shop, the board's shopname text silently falls back to a
generic `text` role, which broke `match_entities`' original design (fixed
1:1 `shopname`-to-`shopname` mapping) for 12 of 13 real dalmia boards.
Fixed by matching `shopname` and `text` entities *together*, by nearest
relative page-height rather than by matching kind - the master's shopname
entity is reliably the smallest of the three text-like shapes by height
fraction across every sampled board, regardless of what its content says.

**A second, harder bug: geometry-only matching cannot identify the
enlarged badge on wide boards.** Applying the same nearest-relative-height
matcher to `logo_cluster` entities reproduces exactly the mistake
`derive_brand_rules.py` made (see "Per-brand tiling rule attempt") via a
completely different algorithm - strong, convergent evidence this is a
real limit of geometry-only matching for this master, not an algorithm bug
to keep patching. The enlarged badge (see "Wide-board panel sequence")
ends up closer in relative height to the Tamil card than to its own
un-enlarged master size, so the matcher assigns it as "a second copy of
the Tamil card" and, having nothing left to match the true small badge
against, assigns the master's actual badge entity to an unrelated
full-width decorative strip instead. **Fix applied**: `WIDE_PANEL_OVERRIDES`
lets a brand supply a hand-verified function
(`dalmia_wide_panel_entities`, built from the "Wide-board panel sequence"
findings above, not from the automatic matcher) that `predict_layout`
uses for `logo_cluster` entities specifically whenever the target is
wide/tall enough to trigger tiling - the one regime the generic matcher is
known to get wrong. Non-logo entities still use the generic prediction
even for wide targets, since matching those doesn't have this ambiguity.

**Leave-one-out validation (`backend/tools/leave_one_out.py`,
`dataset_analysis/leave_one_out/dalmia/report.md`) - the honest accuracy
number, and it's a negative result**: for the 9 plain (non-tiled) real
dalmia boards, excluding board 12 (the already-known outlier), the
example-based prediction's mean max-diff is **25.7%**, against the
existing rule-based engine's **5.4%** on the same boards (both measured
the same way - proportional position/size error against each board's own
real layout). **The example-based approach is worse, not better, on this
data** - most plausibly because a real designer's per-board placement
carries its own small, idiosyncratic "hand-nudges" (documented since the
original dalmia validation - see "Validation: all 13 dalmia files"), and
copying one specific nearest example's exact transform inherits that
example's own noise, whereas the existing rule (uniform scale + centred,
proportional positioning) is a smoother prediction that isn't thrown off
by any single example's idiosyncrasies - especially with only ~8 examples
per leave-one-out fold, most clustered near the master's own aspect ratio
(2.4-2.5), leaving little genuine diversity to interpolate between.
**Per the project's own rules of engagement, this is reported as a
negative result, not tuned or reframed to look better**: the example-based
predictor as built should not replace the existing rule-based engine for
plain resizes.

The wide-panel rule's own comparison in that same report is **not a valid
measurement yet**: `leave_one_out.py` computes each wide board's "ground
truth" entities using the *same* generic nearest-relative-height matcher
that's already been shown to mis-identify the enlarged badge - so the
100% "repeat count mismatch" / "present mismatch" figures reported for
wide boards reflect comparing a correct hand-verified prediction against
an incorrectly-extracted ground truth, not a real accuracy number. Fixing
this needs the wide boards' ground truth extracted the same
hand-verified way as the prediction (or, more robustly, an actual
colour/content signal so the *general* matcher stops needing a per-brand
override at all) - not yet done.

**Not integrated, by decision**: none of `example_engine.py` is wired into
`CorelEngine` or the website - `compute_layout`/`load_brand_rule` are still
what actually runs a job. Given the negative plain-board result above,
integrating the general example engine as-is would be a regression. Per
GATE 2, the wide-panel rule's *findings* (which entity is which, which one
repeats) were instead folded directly into `layout.py` as a targeted,
rule-based fix (`_place_panel_sequence` - see "Wide-board panel sequence"
above) rather than shipping the general example-interpolation approach
this validation shows underperforms. `example_engine.py` and its tests
remain in the codebase for reference/future use, not as dead code to
delete, but aren't on any runtime path.

## Engine split (`backend/app/engines.py`)

`get_engine(kind)` returns one of:

- **`CorelEngine`** (`kind="corel"`, or `"auto"` on Windows) — drives a real
  CorelDRAW install via COM (`pywin32`). Windows-only; raises at construction
  time on any other platform. Defaults `tile=True` for real jobs (override
  per-shop with `shop["tile"] = False`); `MockEngine`'s demo scene doesn't
  opt in, so it renders exactly as before.
- **`MockEngine`** (`kind="mock"`, or `"auto"` off Windows) — ignores the
  uploaded master entirely and runs `compute_layout` against a small
  hardcoded demo scene, rendering an SVG preview instead of a real `.cdr`.
  Lets the whole UI/API be exercised on Linux/macOS/CI without CorelDRAW.
  Does not use tiling or shop-name replacement.

Both implement `process(master_path, shop, out_dir) -> dict` with the same
return shape (`files`, `report`, optional `note`), so `app/main.py` and the
frontend don't need to know which engine ran. Recognized `shop` dict keys
beyond `name`/`width`/`height`/`unit`/`safe_margin`: `tile` (bool),
`shop_name_local` (Tamil display name), `master_shop_name` /
`master_shop_name_local` (what the master's own text currently says, for
`find_shopname_ids` content matching). `SIGNAGE_ENGINE` env var
(`auto|corel|mock`) selects the engine; `app/main.py` re-resolves it per job
(not cached), and runs jobs one at a time via a single-worker
`ThreadPoolExecutor` since CorelDRAW is a single desktop instance.

## Production hardening (`backend/app/corel_util.py`, `corel_watchdog.py`)

All CorelDRAW process lifecycle, timeout and dialog-handling logic lives in
`corel_util.py`, shared by `CorelEngine` and the dev tools
(`dump_objects.py`) so there's one place to get it right:

- **A fresh CorelDRAW instance per job by default.** `acquire_instance()` /
  `release_instance(pid, success)` pool one launched instance across
  `SIGNAGE_COREL_RECYCLE_N` jobs (env var, **default 1** = always fresh -
  raise it only once reuse-across-jobs has been validated in production). A
  failed job's instance is never reused regardless of the counter - it's
  quit and discarded, since whatever went wrong (timeout, force-kill,
  unknown COM state) makes it untrustworthy for the next job. Bypassed
  entirely by `SIGNAGE_REUSE_COREL=1` (dev-only: attach to an already
  -running CorelDRAW, e.g. one you have open, and never touch it).
- **Orphaned-instance cleanup at job start.** `cleanup_orphaned_instances()`
  tracks every PID we launch in `<SIGNAGE_DATA>/corel_launched_pids.json`,
  logs every `CorelDRW.exe` currently running, and force-kills only the
  ones in that file (i.e. ones *we* launched in a previous run and never
  cleaned up - e.g. the process crashed before reaching `quit_corel`).
  A `CorelDRW.exe` not in the file (a designer's own open session) is
  logged and left alone.
- **A hard per-step timeout.** `run_with_timeout(fn, pid, op_name)` wraps
  `open`, `tile_resize`, `saveas`, `pdf` and `png` individually - each has
  its own `timings_s` entry in the job's `report.json`. A `threading.Timer`
  (touches only the OS process by PID, via `taskkill` - never calls a COM
  method cross-thread, which would be unsafe) force-kills the instance if
  a step exceeds `SIGNAGE_COREL_TIMEOUT_S` (default 300s), and the step
  raises `CorelTimeout` naming itself, so the shop fails with a clear
  "which step, killed after how long" message instead of hanging the whole
  job queue forever. `app/main.py`'s per-shop `try/except` already turns
  that into `status: "error"` for that shop and moves on to the next one.
- **A watchdog for stuck dialogs**, behind `SIGNAGE_COREL_WATCHDOG`
  (**default on**). `corel_watchdog.Watchdog` polls every 2s via
  `win32gui.EnumWindows`/`EnumChildWindows` (no COM calls - safe to run
  from a background thread alongside the blocking COM call in the main
  thread) for any window owned by the job's CorelDRW.exe PID. A dialog
  still open **20s** after first appearing gets its title and every child
  control's text logged, a screenshot saved into *the job's own output
  folder*, and is dismissed by clicking its actual default button (found
  via the `BS_DEFPUSHBUTTON` style, not guessed by position/text) so the
  job can fail cleanly instead of hanging. This is exactly the mechanism
  that caught the "Save Drawing" dialog below.
- **Free-RAM check at job start** (`corel_util.check_memory()`,
  `ctypes.GlobalMemoryStatusEx`) - logs it always, warns if under 2GB, and
  the value + a `report["warnings"]` entry both land in the job's
  `report.json` (`free_ram_gb`).
- `SIGNAGE_COREL_VISIBLE=1` launches CorelDRAW visibly instead of hidden,
  for watching a stuck dialog live instead of just reading the log after.
- Best-effort prompt suppression on every launch (`app.Optimization = True`,
  `app.PanoseMatching` set to silently substitute missing fonts,
  `ColorManager.PolicyForOpen/Import.WarnOnMismatchedProfiles/WarnOnMissingProfiles
  = False`) - each independently try/excepted and logged, since not every
  CorelDRAW build exposes every property.

### The "Save Drawing" hang: found and fixed

A real, live hang was diagnosed via the watchdog above (see
`docs/corel-save-hang-diagnosis.md` for the full investigation, including
9 non-reproductions before the actual repro). **Root cause: `cdr_path` /
`pdf_path` / `png_path` (built from `out_dir`) were never resolved to
absolute paths before being passed to `SaveAs`/`PublishToPDF`/
`ExportBitmap`** - unlike `master_path`, which already was (see
`OpenDocument` note below). A relative `out_dir` makes CorelDRAW fall back
to showing its interactive "Save Drawing" file-picker dialog (confirmed:
its address bar showed a generic `...\OneDrive\Documents`, unrelated to
either path) instead of saving silently. **Fix**: `_process` now does
`out_dir = out_dir.resolve()` as its first line. Reproduced 2/2 times
before the fix, confirmed clean 2/2 times after. The hardening above (which
was built *while* this was still unreproduced) stays in place regardless -
a different dialog (a real font substitution prompt, a disk-full save, a
genuinely corrupt file) could still occur in production, and now fails
loudly and cleanly instead of hanging.

### Process isolation: the Dispatch hang and the worker/supervisor split

The dialog above wasn't the only hang found. Live, during validation, the
*entire Python process* running `CorelEngine` went **"Not Responding" for
30+ minutes**, stuck inside `win32com.client.Dispatch("CorelDRAW.Application")`
itself - the very first call that launches CorelDRAW. Every other COM call
here goes through `run_with_timeout`, which force-kills by a PID it already
knows; `Dispatch()` has no PID to kill by until it returns, so nothing
existed to recover it. Two layers fixed this:

1. **`corel_util._dispatch_with_timeout()`** wraps `Dispatch()` itself: a
   background `threading.Timer` watches for a *new* `CorelDRW.exe` PID
   appearing (COM/DCOM activation can spawn the process before the Python
   call returns) and kills it on timeout (`DISPATCH_TIMEOUT_S`, 45s),
   which breaks the pending RPC handshake and unblocks the call. This works
   because blocking COM/Windows calls release the GIL, so the timer thread
   keeps running even while the main thread is stuck in `Dispatch()`.
2. **`corel_worker.py` + `corel_supervisor.py`**: the outer safety net, for
   anything that gets past (1) - e.g. if the hang is deep enough in
   DCOM/RPC that even a killed PID doesn't unblock the call. All CorelDRAW
   work for a batch of shops now runs in a **separate OS process**
   (`python -m app.corel_worker`), so a hang anywhere can be recovered by
   killing that whole process from outside - something a hung process can
   never reliably do to itself (that's exactly what was observed).
   - `corel_supervisor.run_batch(jobs, results_path, overall_timeout_s=600)`
     starts the worker, and polls `results_path` (rewritten after *every*
     job) and `<results_path>.heartbeat` (rewritten before every *step* of
     the *current* job, via `CorelEngine.process(..., on_step=...)`) for
     progress. If neither changes for `overall_timeout_s`, it kills the
     worker's whole process tree (`taskkill /T /F`) plus, since CorelDRAW
     isn't always a real child process of a COM-launching script,
     `corel_util.cleanup_orphaned_instances()` to catch it via the PID
     -tracking file regardless. Pads any job the worker never reached with
     a clear error naming the job and (if a heartbeat exists) the step it
     was stuck on.
   - `corel_worker.py` processes a whole batch in one process, reusing one
     CorelDRAW instance via the existing `acquire_instance`/`release_instance`
     pool, recycling every `SIGNAGE_COREL_RECYCLE_N` shops - **defaulted to
     5 inside the worker** (batches) vs. **1** for a single interactive job
     (unset elsewhere) - and immediately after any failure regardless of
     the counter, unchanged from the existing pool logic.
   - Refuses to start a batch at all if free RAM is under 1.5GB
     (`corel_supervisor.RefusedToStart`), separately from `CorelEngine`'s
     own <2GB *warning* at each job's start - this is a harder floor
     because a whole batch, not one job, is about to run.
   - Tested with a fake worker (`tests/fake_hanging_worker.py`) that just
     hangs, or completes some jobs before hanging, or completes normally -
     no real CorelDRAW needed to exercise the timeout/kill/progress logic
     (`tests/test_corel_supervisor.py`).
   - `validate_all.py` uses this for its whole per-brand batch, and also
     skips a real designer file's dump entirely if
     `dataset_analysis/real_dumps_cache/<brand>/<safe>.json` already exists
     from an earlier run - both the generate step and both dumps happen
     inside the one worker call per file.

### Performance: the ~15 s per CorelDRAW job was a bug, not CorelDRAW (measured and fixed)

Measured before changing anything (real CorelDRAW 27, dalmia master, `backend/` scratch runs - `signage_dataset/` untouched):
89 past conversions' own step timings summed to a median 13.9 s, but a live single-shop conversion took **25.2 s** end to end
while its CorelDRAW work was done at ~9 s. Every job ended with ~16 s of dead time. Root causes, each confirmed live:

1. **`CorelEngine.process` did `pythoncom.CoInitialize()` ... `CoUninitialize()` around every job.** The pooled CorelDRAW
   proxy lives in that apartment, so after every job it was disconnected ("Object is not connected to server") while the
   CorelDRAW process kept running. `quit_corel`'s `app.Quit()` then raised (swallowed by its `try/except`), CorelDRAW was
   never told to quit, and the pool waited out `QUIT_TIMEOUT_S` (15 s) and force-killed it - on EVERY conversion. Fix:
   `corel_util.ensure_com()` initialises COM once per thread and leaves it initialised for the thread's lifetime.
2. **CorelDRAW only exits after `Quit()` once the last COM reference to it is released** (held: still running after 30 s;
   released: exited 1.3 s after `Quit()`). `quit_corel` used to block its caller for up to 15 s while that caller still
   held a reference. Now the pool drops its own reference before quitting (`_quit_pooled`), and `quit_corel` waits only
   `QUIT_CALLER_GRACE_S` (0.5 s) before handing the rest to a background reaper (`_reap`), which force-kills after 15 s
   only if CorelDRAW really never exits. `wait_for_pending_quits()` runs at process exit so no pid stays tracked. This
   also removes the same ~15 s from every scene build and every Save-and-Generate export (both quit through `quit_corel`).
3. **`cleanup_orphaned_instances()` killed the pooled instance that was IN USE** (it is tracked and running, so it looked
   orphaned). That, together with (1), is why reusing an instance in a batch always failed with "Object is not connected
   to server" - which this file used to attribute to memory pressure (see "Content check" and "Remaining limitations").
   It also saved `tracked - running`, i.e. kept exactly the DEAD pids and dropped the live ones; a stale pid can be
   reused by Windows for a designer's own CorelDRAW, which a later cleanup would then kill. Now it spares the in-use pooled
   pid and keeps only that one tracked.
4. An instance that has done its last allowed job (`SIGNAGE_COREL_RECYCLE_N`) is quit right after it, instead of idling
   hidden in RAM until the next job.

**Batching** (`main._v2_convert_worker`): with reuse working, consecutive queued v2 shops (Convert All) are converted in ONE
corel_worker session, up to `CONVERT_BATCH_MAX` = 5, reusing one CorelDRAW; still strictly one CorelDRAW job at a time (one
task on the single-worker `_pool`), each shop's result stored the moment it finishes, and a shop that fails at index > 0 is
retried once alone in a fresh worker (the path a single shop always took). `/status` reads a batch's shared heartbeat only
for that shop's own index (`_batch_slots`). **Not done, deliberately**: N parallel CorelDRAW instances - COM `Dispatch`
attaches to an already registered CorelDRAW, the documented RAM floor (1.5 GB; free RAM here 1.8-4 GB) leaves room for one,
and the "one CorelDRAW job at a time" rule is load-bearing for the watchdog/orphan logic. Deferring PDF to download time was
also not done (median 0.6-1.7 s per shop; it would make every download slow instead).

| Measured live (real CorelDRAW) | before | after |
|---|---|---|
| one shop, end to end | 25.2 s | **10.1 s** |
| 3 shops in one worker batch | 34.4 s, shop 2 failed ("not connected") | **20.2 s**, all done (shops 2-3: launch 0.0 s) |
| 4 shops through the API (Convert All) | ~100 s (4 x 25 s) | **30.5 s** (DB completion times 11.2 / 20.3 / 25.4 / 30.5 s) |
| force-kills, leftover CorelDRW.exe, stale tracked pids | every job | none |

**SaveAs options benchmark (2026-09-29): no gain, nothing changed.** `saveas` is the largest step (median 6.45 s over 60 conversions),
so `backend/tests/benchmark_saveas.py` (not a pytest test; live CorelDRAW 27, scratch copies only) timed the first save of a freshly
opened, dirtied copy under each `StructSaveAsOptions` setting - `IncludeCMXData`, `ThumbnailSize` (none / 1K mono), `EmbedICCProfile`,
`EmbedVBAProject`, `KeepAppearance` on/off - 3 interleaved runs each, on a 9 MB dalmia board and a 124 MB bitmap board. Every variant was
within +/-1 % of baseline (1.03 s / 5.88 s), wrote the same size, was still v21 (`CDRM`/2100), kept `previews/page1.png` and rendered
pixel-identical. Fresh options already default to `IncludeCMXData=False`, `EmbedICCProfile=False`. Save time follows file size
(embedded bitmap data): of the last 60 conversions, boards < 30 MB saved in a median 1.10 s, the heavy ones (median 179 MB) in 9.95 s.
`app.DisplayAlerts` and `AutoBackupEnabled` do not exist in the typelib; `Optimization`/`EventsEnabled` were already set.

**Stuck "Converting 1 of N" (per-shop time limit + restart recovery).** Found in the database of a stuck session: a batch's
job 4 last beat step `png`, then the server was restarted - the worker died with it, but its rows stayed `queued`/`converting`
(one for 5 days), so the page polled them forever; earlier, four shops had waited the full 600 s no-progress limit. Fixes:
(1) `main._recover_interrupted_work` (startup event, `db.fail_interrupted_work`) fails every queued/converting shop and
queued/running export with "Interrupted: the server was restarted... convert it again" and clears `data/convert_runs`.
(2) `corel_supervisor.run_batch(job_timeout_s=...)`: a hard limit per JOB, clocked from the job's first heartbeat past
`starting`/`launch` (CorelDRAW start-up has its own 45 s Dispatch timeout), enforced even while the job keeps beating; over it
the worker tree and its CorelDRAW are killed, the job gets "timed out after 45s at step 'png' - CorelDRAW was stopped...", later
jobs "not started". Conversions pass `main._convert_limits()`: `SIGNAGE_SHOP_TIMEOUT_S` (default **45**, as requested; 0 = off)
and a 120 s no-progress limit instead of 600 s; scene builds, exports and `validate_all.py` keep the old limits. Recovery = the
existing retry-alone path: a later batch member that timed out, and every one it blocked, is retried in a fresh worker with a fresh
CorelDRAW; a timed-out FIRST shop fails (it already had a fresh instance). **Risk**: 45 s is tight - one shop normally takes 10 s on
CorelDRAW 27, but 31 s on 2019, 75 s once under memory pressure, and Agarpathi's 100-350 MB masters were never timed; raise the env
var for those. (3) Dialog suppression: the requested `app.DisplayAlerts` does not exist in CorelDRAW's `IVGApplication` (checked
in the typelib); `_suppress_prompts` already sets every related member (`Optimization`, `EventsEnabled=False`, `PanoseMatching`,
ColorManager `WarnOn*`), and the watchdog dismisses any dialog after 20 s - within the 45 s. Why job 4 stalled at `png` is unknown
(the evidence went with the restart). Tests: `test_corel_supervisor.py` (+4, fake worker `beat`/`_test_launch_s` modes),
`test_convert_batching.py` (+3).

**Oversampled bitmaps: SaveAs was 62% of a shop (2026-09-28).** A real 7-shop batch (10x4 in boards from a 125x48 in,
199 MB master) took 217.6 s; per shop: open 3.5 s, layout 0.5-0.8 s, **saveas 15-24 s**, pdf 2-6 s, png 2 s. Save options
make no difference (probed live on CorelDRAW 2019: default, no thumbnail, no KeepAppearance, no VBA - all 12.4-13.1 s,
208.6 MB; CMX data is already off by default). The cause is the master's five photos (100-300 dpi at 125x48 in, ~530 MB
raw): shrunk 12.5x they sit at 1,250-3,750 dpi and every output rewrites them. New step `bitmaps`
(`corel_util.cap_bitmap_resolution`, between `tile_resize` and `saveas`): each bitmap above `SIGNAGE_MAX_BITMAP_DPI`
(default **300**, 0 = off) at its PLACED size is `Bitmap.Resample`d down - never up, rotated ones skipped. Verified live:
Resample keeps the image's own dpi, i.e. it SHRINKS and moves the shape, so the box is recorded and restored (every box back
within 0.001 mm). Same 7 shops through the real pipeline: **217.6 s -> 104.9 s** (the 7th shop done at ~97 s); outputs
199 MB -> 9-11 MB; saveas 0.7-0.9 s, pdf 0.6-0.8 s, the resample 2.7-3.1 s; previews vs the old ones: mean diff 0.58/255,
0.07% of pixels off by >40. Full-size boards (photos already at their designed dpi) are untouched and gain nothing. Remaining
per shop: open 3.2-4.4 s + close ~1.5 s (the master reopened per shop) and the resample; the batch split at
`CONVERT_BATCH_MAX` 5 costs ~9 s (a second worker + CorelDRAW). A 15-20 s target for 7 shops is below what CorelDRAW needs
here (~5 s of real work per shop even with one open); keeping the master open across a batch (undo a command group per shop)
would save ~4.5 s/shop but is not done - it needs output-equivalence checks against the reopen path. `report["bitmap_cap"]`
records {max_dpi, checked, resampled, skipped, pixels_before/after}. Tests: `test_bitmap_cap.py` (10, fake COM with the live
Resample behaviour).
**List thumbnails + fly-in (2026-09-28, second pass).** Recently generated drew each row's 56 px thumbnail from the shop's
full CorelDRAW preview PNG: measured **17.9 MB for 16 rows** (~1.1-1.3 MB each), all downloaded and decoded. New
`GET /api/v2/shops/{id}/thumb` (`main.v2_shop_thumb`): Pillow, bilinear with a reducing gap, 240 px long side, WebP (PNG if the
Pillow build lacks it), cached under `jobs_v2/<job>/thumbs/` - NOT the shop's `out/` folder, which the ZIP export reads -
and rebuilt when the preview is newer. Same 16 rows: **87 KB (205x smaller)**, ~25 ms to build each once. An empty/corrupt
preview (seen live: a 0-byte PNG) is a 404 and the row shows the empty placeholder instead of broken-image alt text. Splash
fly-in: `FLIGHT_S` 2.4 -> 0.95 s, hand-over at `ARRIVE_AT` 0.8, and the already-faded splash exits instantly (it used to cover
the workspace, invisible, for 0.3 s more): Start Automation -> usable workspace **~3.2 s -> ~1.1 s**; the WebGL canvas is
unmounted with the splash (0 canvases afterwards). **Measured and left alone**: gzip (scene JSON 40 KB / 20 ms, recent list
9 KB - nothing to win locally), optimistic IndexedDB saving (the save round trip is 24 ms and the server's replay check is the
safety net), inspector debounce (fields commit on blur/Enter; typing is local state), streaming ZIPs (the WeTransfer upload
reads the built file). Parallel conversion and a master "parse cache": see "Batching" above.
**QA audit 2026-09-28 (`docs/qa-audit-2026-09-28.md`).** Profiled with 1,001 finished shops: `/api/v2/step-estimates`
262 -> 13 ms (SQLite `json_extract` of `timings_s` instead of parsing every whole report in Python); `/api/v2/shop-statuses`
no longer carries `report` (50 ids: 755 -> 22 KB); `/status` no longer sends `report_json` next to the parsed `report`;
`/api/v2/jobs/{id}` leaves out `report_json` (9.2 MB -> 394 KB). The startup recovery is a `lifespan` handler (the deprecated
`on_event` produced 220 of the suite's 222 warnings). Backend 757 passed, frontend 201.

**Frontend.** Editor-tab load was dominated by the loader, not by data: the workspace had painted at 0.4-2.0 s, but the loader
(eased at a fixed 45 %/s) left at 3.0-3.4 s. It now also closes 14/s of the remaining gap and finishes at 97 %: an in-page
trace on the production build shows the loader gone at **0.69 s warm / 1.0 s cold** (dalmia). The editor route is lazy
(`App.jsx`, `utils/prefetchEditor.js`, prefetched on hover/focus of an "Open in editor" button): main bundle 306 -> **221 kB**
(gzip 99.5 -> 71.3 kB), editor chunk 100 kB; `EditorLoader` is a static import again (no three.js since the 2D rewrite, and
a lazy chunk added a round trip before anything painted). Scene images are served under a build-versioned URL
(`asset_base` = `.../asset/v/<scene.json mtime>/`, `Cache-Control: immutable`), so re-opening a board no longer
re-validates its 100-350 images. The Automation page polls all converting/queued shops with ONE request per tick
(`GET /api/v2/shop-statuses?ids=`) instead of one request per shop every 800 ms (6 shops in flight: 5 requests instead of 30,
verified in Edge). Server-Sent Events were not added: one batched poll gets the same saving with no connection management.
**Not applicable here** (the brief assumed another stack): Fabric.js batching (`renderOnAdd`) - the canvas is SVG of
CorelDRAW-rendered images; a Redis/AST template cache - the .cdr is opened by CorelDRAW itself and a reused instance already
reopens the master in 0.4 s instead of 1.4 s; Web Workers for font measurement - measuring is a few ms per edited text.

Tests: `test_corel_quit.py` (9, a fake CorelDRAW that exits only when its last reference is released),
`test_convert_batching.py` (7, fake supervisor: one batch, cap, retry-alone, RAM refusal, per-index heartbeat, the batched
status endpoint), `test_editor_api.py` (versioned immutable assets). Backend 678 passed, frontend 170.

**Master kept open across a batch + `.env` (2026-10-01).** Measured on the user's 183-shop DARSHAN batch: ~21 s per shop -
saveas 10.6 s (median), pdf 3.8, open 2.7. The master carries 711 MB of raw bitmaps (`content/data/Bitmaps.dat`, 208 MB
packed) that every output rewrites; at 6-12 ft boards the photos are under 300 dpi, so the cap rarely fires.
- **Persistent master session** (`engines._MasterSession`, `SIGNAGE_KEEP_MASTER_OPEN`, default 1): within one worker the
  master stays open while the next shop uses the same file (path + mtime + size) on the same CorelDRAW pid. Each shop's
  edits run in ONE undo group (`BeginCommandGroup`/`EndCommandGroup`; verified live: page size, moves, text, duplicates
  and `Bitmap.Resample` are all undone by `Undo(1)`); after the PNG the group is undone and `doc_fingerprint` (page size;
  per shape, recursively into groups/PowerClips: type, box, text+font+size, bitmap pixels, top-level fills) must match
  the snapshot taken at open within 0.05 mm (`same_fingerprint`; CorelDRAW re-measures text after an undo - a width came
  back 1370.06 vs 1370.07 mm), else the doc is closed and the next shop reopens it. A doc is closed before an instance
  the pool is about to quit (`corel_util.will_quit_on_release`) and at exit. `report.master_session` = {reused, kept_open};
  `timings_s.restore` = undo + check. **Live** (4 boards, same master): 3 of 4 reused, every PNG pixel-identical to the
  reopen path (mean 0.000/255), CDR sizes equal - but only **86.9 s vs 90.8 s (~4%)**: the restore check costs ~1.2 s per
  shop and the snapshot ~1.5-4 s per open, against ~2.7 s open + close saved.
- **ZIP**: PDFs are STORED now (deflating a real 30 MB PDF took 2.17 s to save 1 %), ~2 s per shop in "Generate ZIP".
- **`.env`**: `app/__init__.py` loads `backend/.env` (python-dotenv, environment wins) for the server and the worker;
  `backend/.env.example` documents `SIGNAGE_MAX_BITMAP_DPI` (300; 150 measured on a 10x4 ft board: 31.3 -> 24.2 s, CDR
  209 -> 168 MB, PDF 43 -> 30 MB) and `SIGNAGE_KEEP_MASTER_OPEN`.
- Already in place, not redone: `Optimization`/`EventsEnabled` (`DisplayAlerts` does not exist), the background queue
  with progress, and a ZIP that never calls CorelDRAW. Tests: `test_master_session.py` (4).

### CorelDRAW version: discovered from the registry, never hardcoded

The code has always dispatched the version-independent ProgID `CorelDRAW.Application` (never a numbered one), which Windows
resolves through `HKCR\CorelDRAW.Application\CurVer` to the newest registered install - on this machine
`CorelDRAW.Application.27` (build 27.0.0.121), next to a still-installed `.21` (2019, 21.2.0.706). (The `21` elsewhere in this
file is the SAVE format target for the designers - `save_cdr`, `Version = 21` - not the connection.) What the generic ProgID
cannot survive is a stale registration, e.g. `CurVer` still naming an uninstalled version. `corel_util.progid_candidates()` now
reads every `CorelDRAW.Application[.N]` from the registry, keeps those whose `LocalServer32` executable exists on disk, and orders
them generic first, then numbered newest first (a numbered ProgID with the generic one's CLSID is skipped - same server).
`dispatch_corel` tries the next candidate immediately when one fails with a "no such server" HRESULT (class not registered,
invalid class string, app not found, server exec failure) instead of the 6 s busy-instance retry, and raises "no usable
CorelDRAW installation" once all are exhausted. `SIGNAGE_COREL_PROGID` pins one (e.g. `CorelDRAW.Application.21`). The connected
ProgID and `Application.Version` are logged and written to every conversion report as `report["corel"]`.

Deliberately NOT done (both were in the request): probing each ProgID with `Dispatch` in a loop - every successful Dispatch
launches a hidden CorelDRAW that the probe would leak; and attaching to a running instance with `GetActiveObject` by default -
that would drive a designer's own open CorelDRAW (the codebase keeps that dev-only, `SIGNAGE_REUSE_COREL=1`). No hardcoded
version list either: the one proposed stopped at `.25` and would have missed this machine's `.27`.

Verified live: default conversion -> `{"progid": "CorelDRAW.Application", "version": "Version 27.0.0.121"}`, 10.1 s; a
non-existent `.99` first in line -> fell through to the next candidate and launched in 2.7 s; pinned to `.21` -> a full
conversion on CorelDRAW 2019 (21.2.0.706) succeeded, CDR written as v21 (`CDRM`/2100), no warnings - but slower: 31.3 s
(launch 5.5 s, resize 5.7 s, PDF 12.0 s vs 1.9 / 1.9 / 0.6 s on 27). Nothing left running or tracked after any of them.
Tests: `test_corel_progid.py` (8, fake registry + fake COM).

### Input files from any CorelDRAW version; the watchdog was version-specific (fixed)

**Input: verified, no code change needed.** `OpenDocument` reads every format tested. The dalmia master was saved down to 17
formats - CorelDRAW 27 can write back only to X5 (v15; for v11-v14 its `SaveAs` returned WITHOUT error and wrote no file), the
installed CorelDRAW 2019 wrote v11-v14 (v11-v13 are pre-X4 RIFF files, `CDRB`/`CDRC`/`CDRD`, not zips) - and each was converted
through the real pipeline on CorelDRAW 27 in one batch (105 s for 17): all 17 done, the same 112 objects, no warnings, output
saved as v21. Renders are pixel-identical for v17-v27 inputs; v11-v16 inputs differ by ~1/255 mean, only in the bottom text bar
(text a few px off - older formats store text spacing differently). `_suppress_prompts` now also sets `EventsEnabled = False`
(no document/GMS macro event handlers during automation). (`app.SDK...` from the request is not part of the object model.)

**Output version: unchanged on purpose.** Saved .cdr files still target v21 (`save_cdr`, see "CDR file version") because the
designers' CorelDRAW 2019 cannot open the newer native format. `SIGNAGE_CDR_VERSION=0` already gives "whatever the running
CorelDRAW writes" for anyone who wants native output.

**Two watchdog bugs found doing this, both fixed (`corel_watchdog.py`):**
1. The main-frame check was the literal class `"CorelDRAW21"` (2019). On CorelDRAW 27 launched hidden, the only visible
   top-level window is an untitled `Internet Explorer_Hidden` helper, so on every conversion longer than 20 s the watchdog
   "dismissed" it with WM_CLOSE - 23 real reports carry `dialog '' open >=20s, dismissed via WM_CLOSE`. Now `is_dialog()`:
   never a `CorelDRAW<N>` main frame (`MAIN_FRAME_RE`) or a known helper class, and only a window with a title or child
   controls.
2. Production screenshotted EVERY new window with `ImageGrab.grab()` - the whole screen, all monitors - into the shop's output
   folder: `data/jobs_v2` held 123 whole-desktop screenshots (23 MB): 100 `dialog_new_*.png` plus 23 `dialog_stuck_*.png` from
   the bogus WM_CLOSE "dismissals" above. Now only dev-diagnosis mode (no `dismiss_after_s`) screenshots new windows, and any
   screenshot is of the dialog's own rectangle only. The 100 `dialog_new_*.png` were deleted at the user's request; the 23
   `dialog_stuck_*.png` are still there.
Tests: `test_corel_watchdog.py` (6).

### CorelEngine COM notes (verified against real CorelDRAW 2019, v21)

These were wrong or untested before verification against real master
`.cdr` files and are easy to regress if touched again:

- **`OpenDocument` needs an absolute path.** A relative path fails with a
  generic `Failed to open document` COM error. Always resolve the path
  first (`Path(master_path).resolve()`). **The same is true for every
  output path passed to `SaveAs`/`PublishToPDF`/`ExportBitmap`** - see "The
  Save Drawing hang" above; it silently shows an interactive dialog instead
  of raising, which is worse.
- **`cdrExportRange.cdrCurrentPage` is `1`, not `2`.** `2` is
  `cdrSelection` — using it silently tries to export the (empty) selection
  and throws. This enum is easy to get wrong from memory/docs; the correct
  values live in the generated typelib under
  `%TEMP%\gen_py\3.10\<guid>x0x21x0.py` (search `from enum cdrExportRange`).
- **Optional COM params typed `VT_DISPATCH` (type code 9) must get
  `None`, never their Python default of `0`.** This bites `Document.SaveAs`
  (`Options` param) and `Document.ExportBitmap` (`PaletteOptions`,
  `ExportArea`). Passing the integer default raises `TypeError: The Python
  instance can not be converted to a COM object` — not a COM error, a
  pywin32-generated-wrapper error, so it's easy to misdiagnose as "the
  whole call is wrong" rather than "one trailing arg needs None instead
  of 0".
- **`Document.Export` returns nothing usable.** The plain `Export` method
  is declared `void` in the typelib; `ExportEx`/`ExportBitmap` are the
  ones that return the `ExportFilter` object whose `.Finish()` must be
  called. The original code called `doc.Export(...).Finish()`, which
  would have raised `AttributeError` on `None`.
- **Default PNG export resolution is the document's native resolution**,
  which for large-format signage (multi-metre signs) produces PNGs tens
  of thousands of pixels on a side (one test case: 28800×10800, ~300MB
  decompressed) — too large to be a UI "preview". `CorelEngine` now uses
  `ExportBitmap` with an explicit DPI computed to cap the longest side at
  `PREVIEW_MAX_PX` (1600), clamped to a sane 36–150 DPI range.
- **`CorelDRAW.Application` instances aren't quit automatically.** COM
  `Dispatch` will happily spawn a new hidden (`Visible = False`)
  `CorelDRW.exe` per job and leak it. `Quit()` is asynchronous — the
  process takes a few seconds to actually exit; calling `Dispatch` again
  immediately (e.g. in a tight batch loop) can hit a transient
  `"Property ... can not be set"` on `.Visible` while the previous instance
  is still tearing down. `dispatch_corel()` retries (5×, 4s apart) before
  giving up rather than failing the whole job on this. See "Production
  hardening" above for the full launch/track/recycle/quit lifecycle.
- **`Shape.Text.Story` properties use different names than you'd guess**:
  it's `.Font` (not `CharacterFontName`) and `.Size` (not `CharacterSize`).
  Setting an unrecognized font name doesn't raise — it silently no-ops
  (see "Shop name replacement" above). `.Text` is directly settable
  (`shape.Text.Story.Text = "new string"` works and was verified against a
  real Tamil-content shape).
- **`Shape.Duplicate(OffsetX=0.0, OffsetY=0.0)`** clones a shape in place
  (default zero offset) and returns the new `Shape`; used to realize
  tiling — see "Tiling" above.
- Shape-type and unit enum values that were already correct and verified:
  `cdrMillimeter=3`, `cdrTextShape=6`, `cdrBitmapShape=5`, `cdrGroupShape=7`,
  `cdrPNG=802`, `cdrRGBColorImage=4`.

If another COM call starts throwing `TypeError: The Python instance can
not be converted to a COM object`, check the generated wrapper in
`%TEMP%\gen_py\3.10\<guid>x0x21x0.py` for that method's `InvokeTypes` type
tuple — any `(9, 49)` (optional `VT_DISPATCH`) argument needs an explicit
`None`, not its Python-declared default.

## Tools (`backend/tools/`)

- **`dump_objects.py <path.cdr> [out.json]`** — read-only COM dump of every
  shape (name, type, layer, group nesting, geometry in mm, text content and
  font for text shapes) plus page size. Never writes to the source file.
  This is what the designer-dataset analysis above was built from. Uses the
  same `corel_util` launch/timeout/prompt-suppression plumbing as
  `CorelEngine`, so `SIGNAGE_REUSE_COREL`/`SIGNAGE_COREL_VISIBLE`/
  `SIGNAGE_COREL_TIMEOUT_S` all apply here too.
- **`analyze_designs.py <master_dump.json> <variant_dump.json> [--shop-name
  ... --master-shop-name ...]`** — offline (works from JSON dumps, no
  CorelDRAW needed) single-file tiling-rule check: runs
  `compute_layout(tile=True)` on the master's dumped objects at the
  variant's real page size and compares object counts, tile axis/count, and
  non-background content bounding box against the variant's own dump.
  Doesn't match individual shapes - superseded for real pass/fail
  validation by `validate_all.py` below, kept for quick one-off checks.
- **`validate_all.py <brand> [--limit N] [--only <substr>] [--resume]
  [--timeout S]`** — the real end-to-end validator (see "Validation: all 13
  dalmia files" above): builds one job per real shop and runs the whole
  batch through `corel_supervisor.run_batch` (see "Process isolation"
  above) - generate + both dumps happen inside the worker subprocess, with
  the real file's dump skipped if already cached. Writes
  `dataset_analysis/validation/<brand>/validation_report.{json,md}`
  incrementally as each job's result arrives (a long unattended COM batch
  can still fail partway on individual jobs, even though the *process*
  itself no longer hangs - `--resume` retries only the errored jobs, not
  successful ones). Always seeds from the existing report first (see
  "Wide-board panel sequence" above for why - `--only` used to silently
  discard every other board's entry), so a filtered `--only` run only ever
  replaces the board(s) it actually reran. Never touches `signage_dataset/`;
  all output goes under `backend/dataset_analysis/`.
- **`derive_brand_rules.py`** — clusters a master's `logo` shapes by
  bounding-box proximity and matches cluster sizes against real wide files
  to derive `backend/app/brand_rules/dalmia.json` (see "Per-brand tiling
  rule attempt" above for the honest result: this didn't improve
  validation numbers). Offline, reads `dataset_analysis/dumps/*.json`.
- **`add_visual_metrics.py <brand> [--force]`** — second pass over an
  existing `validate_all.py` report: renders each board's real file to PNG
  (`render_real_preview.py`, read-only COM) and fills in its `ssim` field
  (`image_compare.py` - windowed SSIM on downscaled greyscale, via
  numpy/scipy). Separate from the main batch on purpose: a 4th COM session
  per board pushed this machine into sustained low-memory territory during
  testing (see git history) - decoupling it gives each pass its own full
  retry budget instead of compounding failures.
- **`build_comparison.py <brand>`** — offline (PIL only, no COM): builds
  `dataset_analysis/compare/index.html`, a side-by-side ours-vs-real visual
  sheet from a `validate_all.py` report's `ours_png`/`real_png` paths and
  diff/SSIM numbers. Run after `validate_all.py` (and optionally
  `add_visual_metrics.py`, for the SSIM score to show).
- **`failure_analysis.py <validation_report.json>`** — groups a
  `validate_all.py` report's failing boards into named causes (tile-count
  mismatch, shop-name position, logo/text drift, ...) and counts them; see
  its docstring for exactly how a board is classified.
- **`diagnose_save.py`**, **`test_saveas_variants.py`** — one-off diagnostic
  scripts from investigating the "Save Drawing" hang (see "Production
  hardening" above and `docs/corel-save-hang-diagnosis.md`); not part of
  normal operation, kept for reference if a similar hang needs diagnosing
  again. Both use `app/corel_watchdog.Watchdog` to catch a stuck dialog
  without clicking it.
- **`metrics.py`** — the Phase 1 metric suite (see "Metrics suite" above);
  pure Python, no COM, importable from other tools/tests.
- **`cache_ours_dumps.py <brand> [--force]`** / **`cache_real_renders.py
  <brand> [--force]`** — batch helpers (via `corel_supervisor`, same hang
  protection as everything else) that backfill, respectively, shape dumps
  of already-generated "ours" `.cdr` files and PNG renders of real designer
  files, when a report needs them but `validate_all.py`'s own
  `worker_results.json` no longer has them (see "Metrics suite" above for
  why that happens). Both add a `dump_only`/`render_only` job kind to
  `corel_worker.py` alongside its normal generate+dump job.
- **`build_metrics_report.py <brand>`** — builds the Phase 1 HTML
  side-by-side report (see "Metrics suite" above). Run after
  `validate_all.py` + `cache_ours_dumps.py` + `cache_real_renders.py`.
- **`sensitivity_test.py`** — checks metrics.py's visual score actually
  reacts to known-wrong output (shifts, a missing graphic, a wrong shop's
  board); see "Metrics suite" above for the results table. No CorelDRAW -
  works from one already-generated "ours" PNG plus PIL-synthesized
  perturbations of it.
- **`build_examples.py <brand> [--exclude <substr>] [--out <path>]`** —
  builds `backend/brand_data/<brand>/examples.json` from cached master +
  real dumps (see "Example-based layout engine" above). `--exclude`
  skips a matching source filename (leave-one-out); `--out` writes
  elsewhere instead of the brand's real `examples.json` for that. Offline,
  no CorelDRAW.
- **`leave_one_out.py <brand>`** — the Phase 2 honest-accuracy harness (see
  "Example-based layout engine" above); writes
  `dataset_analysis/leave_one_out/<brand>/report.{md,json}`. Offline.

## Product slots (`backend/app/product_engine.py`, `frontend/src/editor/product_engine.js`, `POST /api/editor/{job}/{shop}/product-assets`, editor `ProductPanel`)

A registry mapping a scene's shapes (the editor's model - see "Phase C: editor
v1") to semantic *product slots* for a future product-based automation flow:
`product_image` (a bitmap, top-level or nested inside a PowerClip),
`brand_title`/`product_title`/`address` (text, tag-only - like `shopname`
before it, there is no reliable untagged signal for these), and `contact`
(text carrying a "Phone No."/"GST NO." label - the same regex
`layout.find_contact_ids` already uses, so an existing untagged master's
contact block is found for free). A shape's slot is decided by a
name-prefix tag in CorelDRAW's Object Manager (`product_image_1`,
`brand_title`, ...), falling back to a heuristic only where one is safe: an
untagged bitmap not covering ≥90% of the page (`BG_AREA_RATIO`, the same
"is this the background" reasoning `_place_panel_sequence`'s panel/badge
split and `metrics.py`'s full-bleed exemption already use elsewhere) is a
product-image slot; an untagged text shape matching the contact regex is
the contact slot. Tagging a group or PowerClip claims its *largest* bitmap
(with a warning if it holds more than one) - any other bitmaps inside it
still get their own heuristic slots, which is a real, accepted consequence
of "tag claims one shape" rather than "tag claims a whole subtree", not a
bug to fix here.

Two new ops (`scene_ops.py` / mirrored in `ops.js`, same drift-guard
pattern as every other op — shared golden cases in
`tests/fixtures/ops_golden.json`'s `product_base`/`product_cases`/
`product_errors`, generated once from the reference Python implementation
rather than hand-computed like the older cases, because centred
aspect-fit scaling isn't hand-arithmetic-friendly — the point is
cross-language parity, not independent re-derivation of the formula):

- **`swap_image`** `{id, asset:{name,w,h,path?}, fit?, frame?, padding?}` -
  fits a new image (given by its *natural pixel size*, for the aspect-fit
  math, plus an optional `path` - see the upload endpoint below) into a
  *frame*, then sets the shape's box to the fitted result and marks it
  (and any PowerClip ancestor) `stale`. The frame is: the PowerClip's own
  box, when the image sits inside one (an explicit `frame` that disagrees
  is refused, not silently ignored - a stale client could otherwise fit
  into the wrong box); otherwise an explicit `frame`, else the frame
  *remembered* from an earlier swap of this same shape (`node.slot_frame`),
  else the shape's own current box. Remembering the frame is what stops a
  second swap from fitting into the *shrunk* result of the first one -
  covered by a golden case (`10,10,50,30` remains the frame across two
  chained swaps even though the first swap's own box ends up `10,12.5,50,25`).
  `fit: "contain"` (default) shows the whole image; `"cover"` fills the
  frame and lets the PowerClip clip the overflow, so it's refused outside
  one. `padding` insets the frame on every side first.
- **`update_product_slot`** `{id, kind, asset?/text?/font?/size_pt?}` - the
  slot-aware wrapper: `product_engine.map_slots(scene)` finds the slot for
  a node id (or `slot_id` via `update_slot_op`), and this op takes `asset`
  for an image slot or `text`/`font`/`size_pt` (at least one) for the other
  four (rejecting the wrong kind of field for a given `kind`, and any
  unknown `kind`) - a `product_image` slot delegates straight to
  `swap_image`'s geometry; a text slot updates whichever of content/font/
  size are given exactly like the existing `text` op (font-size changes for
  a slot were added alongside the COM replay work below, since replaying
  them is just `Replayer._apply_text` - already built for `text` - there
  was no reason to leave them editor-only).

**Editor sidebar: "Product slots" replaced by "Shop details" (`editor/ShopDetailsPanel.jsx`, helpers in `editor/shopDetails.js`).** The
`ProductPanel` UI below was removed from the editor (file deleted); the product-slot ops, `product_engine`, the upload endpoint and the COM
replay all remain and are still tested. The new panel shows the shop record's name for reference and edits the board's **shop-name text** and
its **Phone/GST text** (found by label, `contactIds` = product_engine's contact slot). Edits are ordinary `text` ops (undoable, replayed by Save
and Generate; Enter applies, Shift+Enter adds a line, CorelDRAW's `
` line breaks are kept). Finding the shop-name text: a `shopname`-tagged
text, else a text whose content matches the shop's name, else the designer picks it from a dropdown of the board's text objects once
(remembered per shop in localStorage). Picking is usually needed: a v2 conversion keeps the MASTER's own shop-name text, so e.g. shop "Shop 3"
reads "ஸ்ரீ கவி ஸ்டீல்ஸ்" on the board (checked on 10 cached scenes: no board text equalled its shop's name, none tagged). A "Use <record name>"
button writes the record's name into the board text. The panel does NOT rename the shop record. 10 unit tests (`shopDetails.test.mjs`,
165 frontend total). Checked in Edge on a real converted dalmia board with the ops autosave intercepted (the shop's saved edits untouched):
pick -> canvas selection, edit -> `text` op, Ctrl+Z reverts, no console errors.

**Live text on the canvas (`editor/LiveText.jsx`).** Typing in the Shop details fields (and the Properties Text/Font fields) now
updates the board as you type, and the change STAYS visible after it is committed: every text object whose content/font an op changed
(`stale`), plus the one being typed (`textPreview`, uncommitted, never an op), is drawn as an SVG `<text>` instead of CorelDRAW's image
(which is hidden, not covered). It sits in the object's own box as a nested `<svg>` registered in `imgRefs`, so drags move/resize it like
an image. Size: the glyphs are measured (`getBBox`, re-measured after web fonts load) and scaled so one line has the height one line of the
ORIGINAL text had (line counts from the pristine scene, so an edit that drops a line does not double the size); the width follows, so a
longer name runs wider, as it would in CorelDRAW. Colour: alpha-weighted average of the opaque pixels of CorelDRAW's own render of that
text (a 96 px, alpha >= 200 first version failed on thin glyphs and fell back to near-black - invisible on dalmia's blue). The old preview
drew only for a SELECTED text, so text inside a group (the dalmia shop name) never previewed; this one does not depend on selection.
Still an approximation until Save and Generate: browser font metrics, kerning and alignment (always centred here) differ from
CorelDRAW's, and text inside a non-live PowerClip still shows the clip's old flat render under the live text. Checked in Edge on a real
dalmia board (autosave intercepted): the image is replaced while typing, the white live text stays after Enter, Ctrl+Z restores the
original render, no console errors.

**Board fonts for the live text (`utils/fontLoader.js`, `GET /api/fonts/file`).** Fonts only matter for text the browser draws itself
(LiveText); everything else is CorelDRAW's render. When the editor loads, `sceneFonts(scene)` lists the families the board's text uses (read
from the scene export's `text.font` - there is no separate .cdr parser; CorelDRAW itself reports the fonts) and `ensureFont` makes each
renderable: already renderable -> "local"; else the server's installed copy (`/api/fonts/file?family=`, loaded with `FontFace`, only
tried when `/api/fonts` lists the family) -> "server"; else Google Fonts css2 (fetched first; a family Google doesn't serve answers 400
and no stylesheet is injected) -> "google"; else "missing". Detection measures canvas text widths against monospace/serif/sans-serif
(Latin + Tamil probe) because `document.fonts.check()` returns true for any family the FontFaceSet doesn't track, installed or not.
LiveText re-fits on every `document.fonts` `loadingdone`. The editor footer shows "Fonts: all N available" / "Font missing: X" (warning
colour) with per-font sources in its tooltip. `fonts.font_file` resolves a family through the registry's font list (exact name, then
"<family> Regular"; never a bold/italic face for a plain family) and only serves .ttf/.otf/.ttc files inside the Windows fonts folders
(tests: `test_fonts.py`, +6). Verified in Edge against real boards: dalmia (Arial, Yu Gothic Medium) -> all local, no requests; the
Agarbathi board -> "Copperplate Gothic Bold" reported missing (not on this server, not on Google); a dalmia board with the shop name
switched to Noto Sans Tamil -> loaded from Google and the live text rendered real Tamil glyphs (CorelDRAW's own render of that text shows
tofu boxes here); the server path loads Arial and the .ttc collections Nirmala UI and Yu Gothic Medium. Not done: the Local Font Access
API (`queryLocalFonts`, needs a permission prompt) - measuring needs no permission. Limits: only regular weight is fetched from the
server; Google's lookup is by exact family name (no fuzzy "AvantGarde-Demi" -> "TeX Gyre Adventor"); a Google miss leaves one
unavoidable "Failed to load resource" line in the console; the exported file is unaffected by any of this (CorelDRAW uses the fonts
installed on the machine that runs it).

**"Missing Font Detected" (`editor/FontSubstituteModal.jsx`, `editor/fontSubs.js`, `POST/DELETE /api/fonts/substitute`,
`GET /api/fonts/substitutions`, table `font_substitutions`).** When the editor has loaded (after the loading screen - a popup that opened
earlier sat behind it) and a board font's status is "missing" (not in this browser, not installed on the server, not on Google Fonts -
`utils/fontLoader.js`), the modal asks for a replacement from the SERVER's installed fonts (CorelDRAW on the server writes the files and
ignores a font it lacks; `/api/fonts` lists 285 here), with a live sample of the board's own text in the choice. **Temporary (This Session
Only)**: kept in this tab (sessionStorage, per shop), nothing stored. **Permanent (Save to Shop Config)**: `POST /api/fonts/substitute`
{shop_id, original_font, substitute_font, is_permanent} validates the replacement is installed (422 otherwise; stored under the installed
family's own spelling) and saves it per shop; choosing Temporary later removes a saved one. Either way the canvas draws every text node in
that font LIVE in the replacement (`Canvas` liveTexts + `substituteFor`) instead of CorelDRAW's render - the editor has no Fabric canvas;
its text is CorelDRAW images, and LiveText is the existing way to redraw text (browser typesetting, approximate). **Exports**: the shop's
permanent map rides in the export spec (`font_subs`) and `export_replay.apply_font_substitutions` - after the replay is verified, before
any file is written - sets `Story.Font` on every matching text object (groups and PowerClips too, case-insensitive), reads it back, and
reports `report.font_substitutions` {original: {to, changed, not_applied}} plus a warning when CorelDRAW ignored it; a text mixing fonts
in one run is skipped (counted). "Cancel / Use System Default" keeps CorelDRAW's own render and is not asked again in that tab; the footer's
"Font missing: X -> Y (saved)" reopens the modal, which then also offers "Remove substitution". NOT applied to `batch_runner.py`: that is a
separate legacy tool (products.json + config.json + generate_layout.CorelDrawEngine) with no shops, so a per-shop map has nothing to
attach to. Not verified live in CorelDRAW (fake COM tests only). Found checking: "Copperplate Gothic Bold" IS installed on this machine now
(the earlier note that it is missing is outdated); "AvantGarde-Demi" is missing. Tests: `test_font_substitution.py` (5), `fontSubs.test.mjs`
(3); Edge run on a mock board with the scene's font rewritten: auto popup, Temporary -> live Arial, footer -> Permanent -> saved, reopened
editor keeps it without a popup, Remove, Cancel not re-asked after reload.

**Font search before the substitute popup (`utils/fontLoader.js ensureFont`).** Order: renderable in this browser -> the server's
installed copy -> Google Fonts by the exact name -> Google Fonts by the name's variants -> Fontsource by the exact name and its variants
-> "missing" (only then the "Missing Font Detected" popup). Variants (`fontCandidates`, tested): the base family with the weight/style its
trailing words mean, camel case split ("AvantGarde-Demi" -> "AvantGarde" / "Avant Garde" 600, "Roboto Semi Bold Italic" -> Roboto 600
italic). Google variants: `googleFaceUrl` for that one weight, the css2 response parsed (`parseGoogleCss`, latin / latin-ext / tamil blocks)
and each file registered with the FontFace API under the board's ORIGINAL name (LiveText asks for that name). Fontsource: metadata from
`https://api.fontsource.org/v1/fonts/<id>` (`fontsourceId` = lower case, hyphens; format checked against the live API 2026-09-28:
variants -> weight -> style -> subset -> url -> woff2 on cdn.jsdelivr.net, OFL-licensed), nearest weight, default subset plus Tamil
(`pickFontsourceFiles`). A family found on the web ("google" / "fontsource", `isWebOnly`) fixes the EDITOR PREVIEW only - CorelDRAW on the
server, which writes the exports, does not have it; the footer says "preview only: X" (warning colour) and opens the substitute dialog
reworded as "Font Not On The Server", so an export replacement can still be saved; it does not pop up by itself. (This was already true
of Google-sourced fonts and was not said before.) No backend change: the server was not asked to download or install fonts. Checked in
Edge with every web request answered inside the page (Google 400, Fontsource only "avant-garde", the CDN file a local font): AvantGarde-Demi
found via "avant-garde" weight 600 with no popup; an unknown family searched everywhere and then the popup. Real Fontsource/jsDelivr CORS
from the browser was not exercised live.

### Replacement-image upload: `POST /api/editor/{job}/{shop}/product-assets`

Saves an uploaded image under `<job>/out/<shop>/product_assets/` (never
`signage_dataset/`, never the master's own directory) and reports its real
pixel size via PIL - `{"name", "w", "h", "path"}`, where `path` is a
generated filename (never the client's own, and never a full path - a
directory-traversal attempt like `../x` is rejected the same way
`GET .../asset/{filename}` already guards the scene's own image directory).
The frontend feeds that straight into `swapImageOp`/`updateSlotOp` as the
asset's `path`; `GET .../product-asset/{filename}` serves it back for a
preview. Both require the shop to already be converted (`_editor_shop`),
same as every other editor route. 5 new tests in `test_editor_api.py`.

### COM replay (`export_replay.py`) - `swap_image`/`update_product_slot` now export for real

`Replayer._op_swap_image` imports the asset file onto the target's own
layer with `Layer.Import(path, 0, None)` - verified live: the typelib
declares it `void`, so the new shape is read back via `doc.ActiveShape`,
not a return value, and its `Options` argument is `VT_DISPATCH` and needs
an explicit `None` (the same optional-`VT_DISPATCH` bug class as every
other one documented in "CorelEngine COM notes" - passing the Python
default `0` raises `TypeError: The Python instance can not be converted to
a COM object`). The new shape is fitted to the box `scene_ops.py` already
computed on the shadow scene and takes over the old bitmap's node id, so
later ops in the same list keep resolving it correctly.

A target nested inside a PowerClip - `_clip_ancestor_id` walks the whole
ancestor chain, mirroring `product_engine.clip_ancestor`, because the
direct parent isn't always the PowerClip itself (see below) - is moved in
with `Shape.AddToPowerClip(container, 0)` before the old bitmap is pulled
out with `Shape.RemoveFromContainer()` and deleted, both verified live
against a real generated board's PowerClip (job 16bfc025ca11). A target
inside an ordinary group is put back into that same group via
`_merge_into_group`, the existing ungroup+regroup dance `_op_reorder`/
`_op_group` already rely on.

**A real bug found live, exactly the kind this project's rule of engagement
exists to catch**: the first version only checked the target's *direct*
parent for `kind == "powerclip"`, so a bitmap nested in a plain group that
was itself inside a PowerClip (the real dalmia master's own structure - a
group of 3 sits inside the board's single PowerClip) was never added to
the clip at all - it silently landed as a loose shape on the layer, and
`verify()` correctly caught the resulting object-count mismatch rather than
reporting a false success. Fixed by `_clip_ancestor_id` walking the full
chain like `product_engine.clip_ancestor` already does.

**One case is left approximate, found live and documented rather than
silently gotten wrong**: for a bitmap inside a group that is itself inside
a PowerClip, `AddToPowerClip`/`RemoveFromContainer` were found to operate
on the shape's overall clip membership, not on which sub-group it
happened to sit in - re-inserting the replacement into that exact
sub-group was not attempted (mixing an ungrouped-and-regrouped set of
PowerClip-nested shapes with a brand new one is exactly the kind of COM
interaction this codebase only relies on once verified live, and it
wasn't). The replacement lands as a direct PowerClip child instead, a
sibling of the sub-group; `_settle` reports the resulting z-order/
structure difference as a warning and a real `verify()` mismatch rather
than claiming a false match. Verified live on the real board (job
16bfc025ca11): a **direct** PowerClip child swap (no intermediate group)
and a **top-level** swap both come back `verify() == {"ok": True}` across
all 371 compared objects; the **group-inside-PowerClip** case correctly
reports the one expected mismatch. 9 new fake-COM tests in
`test_export_replay.py` cover all three shapes plus the error paths (asset
never uploaded, asset file missing, no assets directory, invalid shape id,
path-traversal in `asset.path`).

### Editor UI: `ProductPanel` (`frontend/src/editor/ProductPanel.jsx`)

A sidebar panel (between Properties and Layers) listing every slot
`product_engine.js`'s `mapSlots` finds: clicking a row selects (and so
highlights, via the canvas's existing selection outline - no separate
highlight overlay was built) the shape on the canvas, resolving to its
top-level ancestor the same way a canvas click would (`model.js`'s
`ancestry`) since a tagged shape is not always top-level itself. An image
slot gets a file picker that uploads through the endpoint above and
dispatches `update_product_slot`; the other four get a plain text field
that dispatches it with `text` on blur/Enter. Both go through the editor's
normal `commit`, so they land on the undo/redo stack and the live SVG
updates the same way any other edit does - `ProductPanel` never touches
the canvas directly. Not built (out of this task's UI scope, though the
op schema now supports it): a font/size picker per slot, and a persistent
highlight for every slot at once rather than just the selected one.

**A real, unrelated bug found while wiring this**: `frontend/package.json`'s
`test` script never actually ran `product_engine.test.mjs` - it only listed
`ops.test.mjs`/`model.test.mjs`, so every product-slot unit test added in
this and the previous task had been passing in isolation (`node --test
product_engine.test.mjs` directly) but silently never executed by `npm
test`/CI. Fixed by adding it to the script; `npm test` now reports 121
(was quietly only counting 90).

## Orientation adaptation (`backend/app/orientation_adapter.py`, `POST /api/scene/convert-orientation`, editor `OrientationControl`)

Built on top of "Product slots" above: re-lays an editor scene out for a very
different target aspect/orientation (the motivating case is portrait ->
landscape) by moving each SLOT KIND into a purpose-built zone of the new
page, rather than the plain uniform-scale-and-centre `layout.py` falls back
to for an untagged master. Zones: `header` (brand_title, top of a
right-hand text column), `product` (product_image - the PowerClip
CONTAINER's id when the image is nested, per "Product slots" above, never
the inner shape's, so the whole clipped result moves as one), `main_text`
(product_title, middle of the text column), `footer` (address + contact,
one horizontal banner across the full width at the bottom - reusing the
same "the shop name belongs in the bottom bar" placement already
established in "Wide-board panel sequence" rather than inventing a new
convention), `background` (any top-level shape covering
`product_engine.BG_AREA_RATIO` of the page - stretched to exactly fill the
new page, the `bg` role from `layout.py`), and `other` (everything else
unslotted - scaled by the page's own uniform fit factor with its centre
kept at the same proportional position, `layout.py`'s `text`/`logo`
fallback). **Background is decided FIRST, from each top-level shape's own
geometry, before any slot is considered** - found live wiring this up
against a real generated board (job 16bfc025ca11): a real, untagged
master's whole board is often one page-sized PowerClip (real masters are
untagged - see "Designer dataset analysis") that also happens to contain a
modest bitmap `product_engine`'s heuristic alone would call a
`product_image` slot; deciding background second squeezed the ENTIRE
board's artwork into the small product column because that one nested
bitmap looked like a plausible product photo in isolation. Fixed by
classifying page-covering top-level shapes as background up front; a slot
whose top id is already claimed that way is dropped, not reassigned - its
content is handled wholesale by the background stretch. Covered by
`test_classify_zones_treats_a_page_covering_container_as_background_even_with_a_heuristic_slot_nested_inside`
and reverified against the real board (the whole-board PowerClip stretched
to exactly fill the new page, independent of the unrelated small bitmap
nested inside it, which got its own zone).

### Three templates for the area above the footer, picked by aspect ratio (`calculate_zone_rects`)

A single "wide vs. tall" split (the original two-template design) looks
wrong once ANY positive `target_w`/`target_h` is accepted rather than just
a couple of sizes a template happened to be tuned against - a thin,
full-height product column reads fine on a very wide board but is absurd
on a near-square one. `calculate_zone_rects(target_w, target_h)` now picks
from R = target_w / target_h:

| R range | template | product / header / main_text placement |
|---|---|---|
| R >= 2.0 (`WIDE_RATIO`) | `_wide_zones` | product: a left column (width grows with target_w); header stacked above main_text in a column to its right |
| 1.0 <= R < 2.0 (`GRID_RATIO`) | `_grid_zones` | a full-width header banner across the top of the upper area; product and main_text as an equal-width pair of cells side by side below it |
| R < 1.0 | `_stack_zones` | header, product and main_text stacked full-width, top to bottom |

The footer is always a horizontal banner across the full width at the
bottom, in every template - only the area above it changes shape. Every
fraction each template uses (margin/gap/footer as fractions of
`min(target_w, target_h)`; each zone's own width/height as a fraction of
the "upper area" or "available" height that's itself always a fixed
fraction of `target_h`) is relative, never an absolute mm figure - this is
what makes the geometry provably non-degenerate for ANY positive
`target_w`/`target_h`, not just sizes it's been tried on; each `_*_zones`
helper's own docstring in `orientation_adapter.py` carries the specific
argument for why its own rectangles can't collapse. `zone_frames` (the old
name) was renamed to `calculate_zone_rects` as part of this - there is no
compatibility alias, since nothing outside this module and its own tests
called it.

Verified: 17 new tests including the grid template's defining shape (an
equal-width, equal-height pair of cells) at the exact R=1.0 boundary,
disjointness/in-bounds across a much wider range of sizes than before
(extreme aspect ratios in both directions, sub-millimetre targets, and the
task's own arbitrary examples - 90x40, 120x36, 48x96, 60x60in), and a
full `convert_orientation` + `apply_ops` integration check at five
different target sizes (one per template, plus a second grid-range size)
confirming 0 overlaps, strict boundary containment, and that a
multi-shape zone (the footer's address+contact pair) is still realized as
ONE atomic `resize` op, not two independent ones - re-verified live
against the same real board as above (job 16bfc025ca11) at all four of the
task's own target sizes (90x40, 120x36, 48x96, 60x60in) through the
running API: 0 errors, 0 overlaps, 0 out-of-bounds shapes at every size.

**No new op type.** `convert_orientation(scene, target_w_mm, target_h_mm)`
returns a `page` op plus one `resize` op per non-empty named zone, each
covering every shape assigned to that zone in one op (`ids: [...]`) - the
same shape scene_ops.py's existing `resize` already supports for the
editor's own multi-selection drag-resize, so it already does everything
asked for here for free: it repositions AND rescales the group together
(preserving whatever relative layout/non-overlap they already had) and
scales `text.size_pt` proportionally to height (`_scale`) - "adjusted font
sizes" falls out of reusing `resize`, it needed no separate mechanism.
A locked shape (or one on a locked layer) is left out of its zone's op
entirely rather than making the whole list fail to apply -
`classify_zones(scene)` (a separate query function, mirroring
`product_engine.map_slots`) reports zone membership plus a warning for
each shape this happened to, alongside `map_slots`'s own warnings.

**Honest limits, matching this codebase's usual caveats elsewhere**:
grouping the footer's address/contact text and resizing it as one rigid
unit is a scale+reposition, not real text reflow/line-wrapping (there is
no typesetting here, same limit as `CorelEngine`'s own text handling - see
"Text-fit"); the `other` zone's per-shape fallback is only checked to stay
on the page, not to avoid the four named zones, so a master with a lot of
untagged decoration could still end up with `other` content overlapping
`header`/`product`/etc. - a real, documented gap, not silently assumed
away. Not wired into the CorelDRAW replay path specifically (plain
`resize`/`page` ops already replay through COM - `export_replay.py` -
exactly like a manual editor resize, so nothing new was needed there), and
not into the OLD `App.jsx`/`/api/jobs` flow at all - only the new-UI
editor.

`backend/tests/test_orientation_adapter.py` (43 tests): pairwise-disjoint,
in-bounds zone rectangles across a wide range of aspect ratios (including
all three templates and the task-specified arbitrary sizes - see "Three
templates" below); slot->zone classification on a
synthetic portrait scene with a PowerClip-nested product image, a hidden
shape, an untagged "other" shape, a locked slot shape, and the
page-covering-container-wins-background case above; and full
`convert_orientation` + `apply_ops` integration - no overlap or
out-of-bounds positioning among the four named zones, the background
stretched to exactly fill the new page, font sizes changed (not zeroed),
the PowerClip child staying fully inside its container after the
container's resize, a locked shape left untouched instead of raising, and
a scene with no slots at all still applying cleanly.

### API: `POST /api/scene/convert-orientation`

Stateless - takes `{scene, target_w, target_h}` (mm, like the rest of the
scene model) and returns `{scene, ops}`; not tied to a job/shop id or the
`editor_ops` table, so the caller sends whatever scene it already has
(including unsaved edits) and merges the returned `ops` into its own
undo/op timeline itself, exactly like a locally-generated `resize`/`page`
op - nothing here writes to disk. `orientation_adapter.OpError`s (bad
target size) and malformed-scene `KeyError`/`TypeError`/`ValueError`s both
come back as 422. `backend/tests/test_orientation_api.py` (9 tests): the
happy path (including that reapplying the returned `ops` to the ORIGINAL
posted scene reproduces the returned `scene` exactly - the endpoint isn't
just plausible-looking, it's internally consistent), the PowerClip
container-not-inner-shape case, non-positive targets, a malformed scene, a
locked shape, and a scene with no recognizable slots.

### Editor UI: `OrientationControl` (`frontend/src/editor/OrientationControl.jsx`)

A toolbar dropdown next to the page-size control: "Landscape (90 × 30
in)", "Portrait (30 × 90 in)", or "Custom dimensions…" (two inputs in the
editor's current unit). Picking one POSTs the editor's current scene to
the endpoint above and merges the returned ops into the undo timeline via
a new `commitMany` in `EditorPage.jsx` - deliberately NOT
`opsList.forEach(commit)`: `commit()`'s `setOps` updater slices on the
CURRENT `cursor`, which is still the stale, pre-batch value for every call
made inside the same render pass, so a second `commit()` call in the same
tick would slice off the first call's op instead of extending it (the
first op would silently disappear). `commitMany` validates and appends
the whole list in one pair of state updates instead, so the entire
conversion is one undo step (Ctrl+Z reverts it in one go). Selection is
cleared and the view re-fits to the new page afterward, since the old
zoom/pan was framed for a very different aspect ratio. The canvas needed
no changes at all for this: it already re-derives `scene` (and so every
clip path, image box and selection outline) from `base`/`ops`/`cursor` on
every render, so appending the orientation ops updates the live SVG the
same way any other edit does.

**Verified live against a real generated board** (job 16bfc025ca11, the
AL MADEENA PowerClip board from "Fix scene export dropping vector shapes
inside PowerClips") via curl against the running API (no Playwright, so
the React control itself was not click-tested in a browser this session -
only unit/integration-tested and confirmed to build): converting its
2286×762mm scene to a 6096×2032mm (240×80in) target correctly stretched
the whole-board PowerClip to fill the new page exactly and moved every
other top-level shape into its zone, with 0 errors - this is the same run
that caught the background-precedence bug above, i.e. this feature was
wired against real data, not only synthetic fixtures, before being called
done.

**A process note, not a code issue**: verifying this needed the backend
restarted several times while iterating, and `uvicorn --reload` on
Windows kept leaving ORPHANED worker processes still bound to port 8000
after their parent was killed (`--reload`'s Windows implementation spawns
the real server as a `multiprocessing` child that inherits a duplicated
socket handle; killing the parent doesn't release that handle if the
child is still alive) - three generations of stale workers ended up
simultaneously answering on :8000, so requests kept hitting old code
despite the file on disk and a freshly-started process both being correct.
Fixed for this session by killing every leftover `python.exe` and
restarting `uvicorn` WITHOUT `--reload` for manual verification; not a
code change, just worth knowing if a future session sees new code
"not sticking" after a restart.

### Fixing blank space and background distortion on a real (untagged) master

A task asking to "prevent zone duplication" against the AL MADEENA board
found, on inspection, that **no shape duplication exists anywhere in this
module** - there is no `Duplicate()`/`paste` call, every op is a single
`resize`, so an id can never appear twice (verified: `len(all_ids) ==
len(set(all_ids))` across every `resize` op, for all three of the task's
target sizes). What LOOKED like duplication in a rendered preview was two
separate, real problems, found by actually rendering the converted AL
MADEENA board rather than reasoning about the code alone:

1. **The background was visibly squished.** `classify_zones`'s
   background-precedence rule (see above) can make an ENTIRE real master's
   composed PowerClip - logos and packaging included, not just a plain
   texture - the "background" bucket when it happens to cover most of the
   page (true for AL MADEENA: its one PowerClip holds nearly everything).
   The background zone's exact non-uniform stretch (`layout.py`'s own `bg`
   role, correct for a plain texture) then visibly squished every shape
   nested inside it on an extreme aspect change. **Fixed**: background now
   uses a "cover" fit (`product_engine.aspect_fit(..., fit="cover")` -
   uniform scale, centred, no distortion) instead of an exact stretch -
   still covers every mm of the new page (it only ever grows past the page
   edges, never falls short - the same 100% coverage an exact stretch
   gives), at the honest cost of cropping whatever overflows.
2. **Most of a real, untagged master's actual content was never getting
   near the template's own zone rectangles at all.** A real master (see
   "Designer dataset analysis") has no `brand_title`/`product_title`/
   `address`/`contact` tags, so `header`/`main_text`/`footer` came back
   completely EMPTY for AL MADEENA - the master's real logos and shop-name
   text sat entirely in the weak, position-preserving `other` fallback
   instead, which left over half a tall target's height as bare template
   while scattering the real content into tiny, oddly-placed fragments (a
   converted 36x96in board showed this live: a shop-name text and a logo
   badge that sat far apart on the original page ended up overlapping).
   **Fixed**: `convert_orientation` now buckets `other` shapes by their
   ORIGINAL proportional vertical position into however many of
   `header`/`main_text`/`footer` are completely empty, top-to-bottom
   (matching each empty zone's own top-to-bottom position on the new page
   in every template - `product` is deliberately excluded, since in the
   wide template it's a full-height side column, not comparable to the
   others by vertical position); each bucket is fit as its own rigid unit
   into its own zone, not merged into one - a single merged blob was tried
   first and found live to cram unrelated shapes together for exactly the
   reason above. This only fires when a zone is COMPLETELY empty, so a
   properly slot-tagged master (this tool's own future masters) is
   unaffected - its header/main_text/footer are never empty to begin with.

**Re-verified on all three of the task's target sizes against the real AL
MADEENA board** (job 16bfc025ca11), both visually (rendered composites) and
programmatically: 0 duplicated ids, the background covers 100% of the new
page at every size, 0 overlaps among the final top-level shapes, and every
non-background shape stays within the page. `product_image` slots are
unaffected by any of this and keep the aspect-preserving `contain` fit
they already had - the "no stretching, no awkward clipping" requirement
was already satisfied for named zones before this task; the actual
distortion was specific to the background-zone case above. 2 new tests
(`test_convert_orientation_absorbs_other_into_empty_named_zones_by_
vertical_band`, `test_convert_orientation_leaves_other_alone_when_no_
named_zone_is_empty`) plus 3 existing background tests updated from
exact-stretch to cover-fit assertions (`assert_covers_page_without_
distortion`).

### Full-canvas spatial reflow: stretch-to-fill zones instead of aspect-preserving `contain`

A follow-up task asked for the endpoint to never serve a cached result (it
already didn't - see below) and for the tall/ultra-wide templates to
actually fill their assigned zone bounds rather than leaving whitespace.

**Caching check - no bug found, defensive header added anyway.**
`POST /api/scene/convert-orientation` (`backend/app/main.py`, not
`api.py` - that file doesn't exist) was already fully stateless: `body.scene`
is read fresh from the request on every call, nothing is written to or read
from disk/`editor_ops`, and there is no `lru_cache` or module-level cache
anywhere in the call path - two calls with different `target_w`/`target_h`
always recompute from scratch. Rather than inventing a fix for a bug that
wasn't there, the endpoint now sets `Cache-Control: no-store` on its
response (defensive - rules out a browser/proxy layer ever reusing a POST
response, though browsers don't cache those by default) and its docstring
records the investigation.

**The real gap: `_place`-style `contain` fit wastes zone space by design.**
Every named zone's content was placed with `pe.aspect_fit(..., fit="contain",
padding=pad)` - centred, aspect-preserving, so a content cluster whose own
aspect ratio didn't match its zone's left one axis full of unused padding.
Measured live on the AL MADEENA board (job 16bfc025ca11) converted to
36x96in: only ~43% of the page height was actually occupied by content,
with every zone's shapes clustered near its own centre - exactly the
"clustering in the center" the task called out.

Fixed with `_fill_frame(frame)`: since zone content here is logo/text
clusters (not a photographic asset that would look wrong distorted) and
`scene_ops._scale` already supports independent x/y scale factors (nothing
new needed - see "Why only existing ops" above), each named zone's `to` box
is now the padded frame itself, stretched to fill exactly, instead of an
aspect-fit sub-rect centred inside it. Applies to both the per-zone loop and
the "other absorbed into an empty named zone" path in `convert_orientation`
- background keeps its `cover` fit unchanged (that one is deliberately
non-distorting, per the section above).

**Padding needed a second fix to actually clear 85%.** The first version of
`_fill_frame` computed one `padding` value from `min(frame_w, frame_h)` and
subtracted it from BOTH axes - for a zone much taller than wide (the stack
template's header/product), that set the inset from the SMALL width and
then wasted the same absolute amount on the LARGE height too, capping
vertical utilization at 84.0% (zone-level) even with stretch-to-fill.
Fixed by computing `pad_x`/`pad_y` independently as `ZONE_PADDING_FRAC` of
EACH axis's own size; `ZONE_PADDING_FRAC` was also brought down from 0.06 to
0.03 (still comfortably > 0, so every zone's `frame_dim * (1 - 2*0.03)`
stays positive for any positive frame - same non-degeneracy guarantee as
every other constant in this module) once the per-axis fix alone (84.0%)
still fell short of the task's >85% bar - 5 stacked zones (header, product,
main_text, footer, plus the small inter-zone gaps) each compounding their
own padding adds up fast at 0.06.

**`_wide_zones` restructured so the header actually centres over the full
canvas**, per the task's explicit ask for the 240x36in case. The header was
previously a right-hand column stacked above main_text, sized to the text
column's own width - correct per the module's original 3-role design, but
not "centred over the canvas width" as asked. Now the header is a
full-width banner (margin to margin) across the top of the upper area,
with product (left column) and main_text (right of it) sharing the
remaining height below - `header_w = target_w - 2*m` is trivially positive
for any target_w, and `remaining_h`/`product_w`/`text_w` keep the exact
same positivity proofs the original layout already had (see the function's
own docstring).

**Live re-verification against the real AL MADEENA board (job
16bfc025ca11)**, both target sizes from the task, after a clean backend
restart (no `--reload`, to avoid the orphaned-worker issue documented
above):

| Target | Zone-level vertical utilization | Zone-level horizontal utilization | Duplicate ids | Side-by-side duplicate blocks | Header centred? |
|---|---|---|---|---|---|
| 36x96in (tall) | **89.8%** (> 85% required) | 88.4% | none | none | n/a (stack template) |
| 240x36in (ultra-wide) | 84.6% | 96.1% | none | none | **yes** - header spans x=[27.4, 6068.6]mm on a 6096mm-wide page, centre at exactly 3048mm |

Verified via `curl` against the running API with the real cached scene at
`backend/data/jobs_v2/16bfc025ca11/out/91a4cdb56ffe/scene/scene.json`, then
checking the returned ops/scene programmatically (merged-interval coverage
of each zone's own `to` box against the target page, and an all-pairs
same-size/position check for duplicated blocks) - not just by re-running
the existing synthetic unit tests. `backend/tests/test_orientation_adapter.py`
and `test_orientation_api.py` (54 tests) and the full backend suite (416
tests) all still pass unmodified - none of the existing tests hardcoded the
old padding fraction or the old wide-template column position, only
disjointness/in-bounds/no-degeneration, which the new geometry still
satisfies. `npm test` (121 tests) also passes - `orientation_adapter.py`
has no JS mirror (only its API/UI wiring is JS), so it was unaffected by
this change, confirmed rather than assumed.

### High-fidelity scaling: bitmaps scale uniformly, vector/text still fill the zone

The stretch-to-fill fix above (`_fill_frame`) is correct for vector/text
clusters - CorelDRAW re-renders crisp geometry from an updated bounding
box, so a non-uniform scale never blurs anything - but applying it
unconditionally would visibly stretch/distort a real photographic bitmap
(a product photo, a table/pedestal surface texture) or a PowerClip that
clips one, since `scene_ops._op_resize` maps one `from`/`to` box onto
every id in a `resize` op with a single sx/sy pair.

Fixed with `_contains_bitmap(node)` (recurses into `children`, using the
existing `product_engine.is_bitmap` check) and `_zone_fit(idx, ids, frm,
frame)`: if ANY id assigned to a zone contains a bitmap anywhere in its
subtree - a bare product-image shape, or a group/PowerClip that has one
nested inside it (the "product image grouped with a table/pedestal
surface" case from the task) - the WHOLE group scales via
`pe.aspect_fit(..., fit="contain")` (one uniform scale factor on both
axes, centred in the frame - never overflows/truncates, which is what
"scale the composite object uniformly ... so edges do not truncate
mid-canvas" asks for); otherwise it keeps `_fill_frame`'s non-uniform
stretch. A group is checked as a whole, not per-shape, because one `resize`
op cannot give two different ids two different scale factors - the safer
(non-distorting) behaviour wins for the group. Background keeps its
pre-existing `cover` fit (already uniform, unaffected).

**Verified live against the real AL MADEENA board (job 16bfc025ca11)** at
both of the task's target sizes (30x40in and 36x96in): every bitmap leaf's
own width/height ratio (`s45`, `s47`, `s48`, `s49` - nested inside the
page-covering background PowerClip `s44`, unaffected since that zone
already used `cover` - and `s6`, the heuristic top-level `product_image`)
matches its ORIGINAL ratio to within floating-point rounding (< 0.0001%
difference) at every size, confirmed by comparing each bitmap's `w/h` in
the converted scene against the source scene, not just by eye. The
vector/text zones (`s22`+`s7`, `s28`+`s4`, `s5` - none contain a bitmap)
still stretch non-uniformly to fill their zone, unchanged from the
previous fix (e.g. `s22`+`s7` goes from a 1884.5x193.0mm source box to a
673.3x284.8mm box at 30x40in - width and height scaled by different
factors, exactly as intended for vector/text content).

**A second, real bug found writing the test for the grouped case**: tagging
a plain `group` (not a PowerClip) as `product_image_1` around a product
bitmap AND a table/pedestal-surface bitmap did not move the two together -
`_slot_top_id` resolved to `slot.container_id or slot.node_id`, and
`container_id` is only ever set to the nearest **PowerClip** ancestor
(`product_engine.clip_ancestor`), never a plain group's id. So the zone
ended up containing just the single targeted bitmap (the larger of the
two, per `map_slots`' "using the largest" rule), leaving its table surface
behind at the OLD page's coordinates entirely - a literal case of the
composite being torn apart, exactly what "Table Surface & Assembly
Anchoring" was written to prevent. **Fixed** by replacing `_slot_top_id`
with `_topmost_top_level_ancestor(idx, node_id)`, which walks all the way
up to whichever ancestor is a direct child of the layer (a true top-level
shape) - a bare bitmap resolves to itself, one in a PowerClip resolves to
the PowerClip (unchanged from before), and one in a plain group now also
resolves to that group's own id, so the whole composite moves and scales
as one rigid unit regardless of which container type holds it.

New tests in `test_orientation_adapter.py` (48 total, up from 43): the
real AL MADEENA product zone's PowerClip (`pc`, holding the nested
`photo` bitmap) keeps its own aspect ratio after conversion while the
header zone's pure-text content (`brand`, no bitmap) still stretches
non-uniformly to fill its zone as before; a synthetic product-image
-bitmap-plus-table-surface-bitmap GROUP (no PowerClip) scales as one
uniform, non-truncated unit inside its zone frame, with each bitmap
child keeping its own individual aspect ratio too. All 419 backend tests
(416 + 3 new) and 121 frontend tests pass - `scene_ops.py`'s generic
`_op_resize`/`_scale` were deliberately left untouched (they're also what
the editor's own freeform drag-resize uses, where a user may intentionally
want non-uniform scaling on anything, bitmap included), so this fix is
scoped to `orientation_adapter.py`'s own zone-fitting and slot-to-zone
membership decisions, not the shared resize primitive.

**Live re-verification against the real AL MADEENA board (job
16bfc025ca11)** at the task's own 30x40in and 36x96in targets, after the
`_topmost_top_level_ancestor` fix (which doesn't change this board's own
results - it has no plain-group product composite, only the PowerClip
case that already worked): every bitmap leaf (`s45`, `s47`, `s48`, `s49`
inside the background PowerClip; `s6`, the heuristic top-level
product-image) keeps its exact original aspect ratio (max drift
2.4e-5% across both targets), while the vector/text zones (`s22`+`s7`,
`s28`+`s4`, `s5`) still stretch non-uniformly to fill their zone bounds,
unchanged from the previous task.

### Fixing an "other absorption" bug found on a second real board (DARSHAN AGARBATHI)

A follow-up task asked for strict uniform bitmap scaling (already built - see
above, re-verified below) plus specific, board-described fixes ("scale the
brand logo up ~20%", exact Y=28-40in/8-28in/0-24in zone boundaries) for job
`8a41177716c4`, shop `fe047cace239` - a different brand/board than every
previous verification in this file. **Those specific percentages/Y-ranges
were not hardcoded** - per this project's own established rule (see "Wide
-board panel sequence": "apply a rule only if it holds for more than one
sample" - one board's own numbers are not a generalizable rule, they're
overfitting) - so the actual scene was read and diagnosed first, the same
way every other fix in this file was found.

**What the real scene actually looks like** (`GET .../scene` cached at
`backend/data/jobs_v2/8a41177716c4/out/fe047cace239/scene/scene.json`):
page is already exactly 762x1016mm (30x40in - the task's own target), and,
like AL MADEENA, it's an untagged master whose entire illustrated
background - including the brand logo, the "3d Object" wooden-table
-and-incense-box composite (`s46`, itself a group of 3 bitmaps), and a
dot-pattern decoration - is ONE page-covering PowerClip (`s44`), so
`classify_zones`'s background-precedence rule (see above) correctly
absorbs all of it wholesale; it's already fully covering the page 1:1
(target size = its own current size), so there is nothing to reposition
there. The only things classify_zones finds OUTSIDE that background are 6
small untagged top-level shapes: `s38`/`s23` (small logo fragments near
the top), `s22`/`s21` (two text lines near the bottom), `s5` (a
sizeable secondary badge/text group - the real "BLACK STONE" assembly the
task's own "secondary accents" bullet describes), and `s4` (a standalone
bitmap - "the red box" the task's verification step names).

**Item 1 (strict uniform bitmap scaling) needed no new code** - `_zone_fit`
(built in the previous task) already applies to `s4` here exactly as
designed: verified live through the running API that `s4`'s aspect ratio
(0.4479) and every background-nested bitmap's own aspect ratio (`s45`
0.8779, `s47` 0.8692, `s48` 0.6969, `s49` 0.0799) are bit-for-bit unchanged
after conversion (0% drift on 4 of 5, 2.9e-6% on `s4` from floating-point
rounding) - confirming the fix generalizes to a second, independently
-verified real board, not just the one it was built against.

**Item 2 surfaced a real, second bug, found the same way as every other
fix here - by running the real data, not by guessing**: `s5` (a
303x562mm badge group, comparable in size to the page's own product
composite) landed ALONE in the stack template's `main_text` zone - a
deliberately small 0.06-of-`avail` sliver sized for a short text label
(see "Full-canvas spatial reflow" above) - and was crushed to 40.7mm tall,
a 13.8x shrink. This is exactly the "position secondary accents ...
without leaving wide gaps" the task describes, just discovered from the
actual number rather than assumed: the OLD bug (from two tasks ago) was
that absorbed content got no room at all; THIS bug is that the room it
gets is assigned by `_split_evenly`'s equal HEAD COUNT (5 shapes into 3
zones = roughly 2/2/1), with no regard for how large any one of those 5
shapes actually is.

**Fixed with `_split_by_capacity`** (replaces `_split_evenly` for this one
call site): a small dynamic program over cut points that partitions the
leftover shapes (still top-to-bottom, order-preserving - undoing that
would re-cause the "unrelated shapes crammed together" bug two tasks
back) into the empty zones so as to minimize the WORST per-zone overflow
ratio (`bucket weight / that zone's own capacity`), rather than the worst
being decided by count alone. A first, simpler attempt (assign each item
to whichever zone's cumulative-capacity range contains its own
cumulative-weight midpoint) was tried and found NOT to fully fix this -
documented in the function's own docstring rather than silently replaced:
a single large item can still overflow whichever bucket its position
happens to fall into, since a group can't be split across two zones; only
an actual optimization over where to CUT reliably finds the best
placement. On the real numbers (33.2/61.4/170.7/15.5/11.8mm across
303.0/43.3/203.2mm capacities), it correctly groups `s5` with the two
small shapes above it into the much larger header capacity (265.3/303.0 =
0.876x, no overflow at all) and leaves `main_text` empty rather than
crush anything.

**A second, smaller bug surfaced by fixing the first**: leaving
`main_text` empty (the DP's correct decision - giving it anything would
make some OTHER zone's ratio worse) meant that zone's own 43.3mm of page
height sat completely unused - reintroducing a smaller version of the
exact "beige empty space" this task complains about, just relocated.
Fixed with `_reclaim_empty_absorbing_frames`: an empty zone's frame is
folded into the next zone below it that DOES have content, extending
that zone's own frame to also cover the freed slot - guarded to only
merge when the two frames share the same `x`/`w` (true for every zone in
the stack template, since `_stack_zones` gives header/main_text/footer
identical `x`/`w` by construction), so a grid/wide-template's narrower,
offset main_text column is left alone rather than risk stretching a
merged rectangle sideways into a third zone's space.

**Live result, verified through the running API** (not just direct
function calls) at the task's own 30x40in target: **86.95% zone-level
vertical utilization** (was 81.5% with the capacity-aware split alone,
before the empty-zone reclaim; the task's own bar is >85%), zero overlap
among the 6 real non-background shapes, all within page bounds, `s5`'s
own shrink factor down to 1.52x (was 13.8x), and every bitmap's aspect
ratio exactly preserved as above. 6 new tests in
`test_orientation_adapter.py` (54 total, up from 48): `_split_by_capacity`
on the real board's own numbers (locks in the exact expected bucket
assignment, not just "doesn't crash"), its fallback to `_split_evenly`
when there's nothing to weigh by, `_reclaim_empty_absorbing_frames`
extending a neighbour into a freed slot AND refusing to do so across a
genuine x/w mismatch, and a full synthetic end-to-end reproduction of the
DARSHAN scenario (small header/footer shapes plus one disproportionately
large secondary group) confirming the shrink factor stays under 3x and
nothing overlaps. All 425 backend tests and 121 frontend tests pass.

**What was deliberately NOT done**: the task's own literal asks - "scale
the brand logo up by ~20%", "Y = 28in to 40in" style absolute zone
boundaries, moving the wooden-table composite specifically - describe ONE
board's own ideal layout, not a rule that generalizes (the same "manual
creative redesign" limitation documented since "Designer dataset
analysis" and revisited at every wide-board tiling fix in this file).
Since that composite is nested inside the page-covering background
PowerClip (not a separately addressable zone at all under the current
slot/zone model - see "Fixing blank space and background distortion"
above), pulling it out into its own independently-scaled zone would be a
materially new feature (partial content extraction from a background
PowerClip), not a bug fix, and was out of scope here; the diagnosis is
recorded so a future task that actually wants that feature starts from a
verified understanding of why it doesn't already work, not a guess.

### Structural wireframe sub-placement: top-left/top-right logos, main_object + subobjects, English/Tamil footer

A follow-up task asked for a portrait/landscape "wireframe": top-left/
top-right logo objects, a centred `main_object` with `subobjects` flowing
into flanking columns, and a footer banner with English/Tamil text either
stacked (portrait) or side by side (landscape). It described this as a
2-way "R < 1.0 / R >= 1.0" split - **deliberately not implemented as a
replacement for `calculate_zone_rects`'s existing 3-template design**
(wide/grid/stack, picked by `WIDE_RATIO`/`GRID_RATIO` - see "Three
templates" above): collapsing back to 2 templates would reintroduce
exactly the "a template tuned for a very wide board looks wrong on a
near-square one" problem the grid template was built to fix, with no new
evidence that it's actually wrong for the sizes in between. Instead, the
wireframe is layered ON TOP of the existing header/product/main_text/
footer zone geometry (unchanged) as a SUB-PLACEMENT: how a zone's own
already-computed frame divides its assigned ids into named sub-roles.
Top Region = header, Center Region = product/main_text (each independently
- "main_object" is decided per zone, not merged across product+main_text's
differently-shaped frames), Bottom Region = footer (already a full-width
banner in every template, so no template change was needed there at all).

**New helpers** (`orientation_adapter.py`): `_split_left_right_by_x` (2+
ids split around their own group mean centre-x, falling back to an
index-based midpoint split if every id ties); `_place_two_up` (places one
group in the left/top half of a frame and another in the right/bottom
half, `axis="x"`/`"y"`); `_place_top_region` (splits a header zone's ids
into left/right logo bounds via the above); `_place_main_and_subobjects`
(the largest-by-area id becomes `main_object`, centred in
`MAIN_OBJECT_FRAC` (0.5) of the frame's width, with the rest flowing into
whichever side of `main_object`'s own centre-x they originally sat on);
`_place_footer_banner` (splits by detected script - `_is_tamil_text`,
matching the Tamil Unicode block U+0B80-U+0BFF against a text shape's
actual content, the same "read the real text, never trust a declared
font/language tag" principle CLAUDE.md's "Shop name replacement" section
already established - and places English/Tamil stacked for a portrait
target, side by side for landscape). **Every one of these reduces exactly
to the pre-existing single-group `_zone_fit` behaviour when a zone holds 0
or 1 id** - the common case for every currently-verified real board's
product/main_text zone before absorption adds extras - so this is
additive: nothing changes unless a zone actually has 2+ genuinely distinct
pieces of content to arrange. Each sub-placement still routes every group
through `_zone_fit`, so item 2's bitmap-vs-vector protection (uniform
scale for anything containing a raster, non-uniform fill otherwise) is
unconditionally preserved regardless of which sub-role a shape lands in.

**A real ordering bug found and fixed while wiring this in**: the first
version applied the new sub-placement to BOTH the real tagged-zone loop
AND the "absorb untagged other content into an empty zone" fallback (see
"Fixing an 'other absorption' bug" above). This broke a passing test
(`test_convert_orientation_absorbs_other_into_empty_named_zones_by_
vertical_band`): an absorbed bucket assigned to `header` by
`_split_by_capacity` purely because it had spare CAPACITY (not because its
contents are semantically a "logo") can include a shape that originally
sat much lower on the page than the others; splitting that bucket by
x-position alone (as `_place_top_region` does) then placed that lower
shape side-by-side with a genuinely top shape instead of keeping it below,
scrambling the "top stays top, bottom stays bottom" ordering the
absorption logic exists to guarantee. **Fixed by scoping the wireframe
sub-placement to the REAL tagged-zone loop only** - absorbed "other"
content keeps its original plain single-group `_zone_fit` (unchanged from
before this task), since it has no genuine semantic role to sub-place by;
the wireframe assumes its ids actually belong to the zone's own kind (a
real `brand_title`/`product_image`/etc. slot tag, or a heuristic match -
see `classify_zones`), which absorbed filler content does not.

**Live implication, stated honestly**: both real boards used for
verification (AL MADEENA, DARSHAN AGARBATHI) are untagged masters (see
"Designer dataset analysis"), so on their OWN, unmodified files, this new
logic is a no-op for header/footer - that content still arrives via
absorption, which is deliberately excluded above. It DOES engage for their
`product`/`main_text` zones' single heuristic `product_image` bitmap (`s6`
on AL MADEENA, `s4` on DARSHAN) - reducing to the unchanged single-item
path, confirmed to produce byte-for-byte the same aspect ratios as before.
To verify the actual NEW code paths (top-left/right split, footer En/Ta
split) against real geometry rather than only synthetic fixtures, each
board's own text was tagged on an IN-MEMORY COPY (never written back to
disk, never touching `signage_dataset` or the cached job files) using
content that was already genuinely bilingual:

- AL MADEENA's `s4`/`s5` are the real board's own Tamil/English shop-name
  pair ("அல் மதீனா பூஜை ஸ்டோர்" / "AL MADEENA POOJA STORE") - tagged
  `address`/`contact` to route them into `ZONE_FOOTER`.
- DARSHAN's `s21`/`s22` are its own real Tamil/English shop-name pair
  ("ஸ்ரீ கன்னியம்மன் நாட்டு மருந்து கடை" / "SRI KANNIYAMMAN NATTU
  MARUNTHU KADAI") - same tagging.
- AL MADEENA's `s4`/`s5` (both independent top-level text shapes, unlike
  DARSHAN's own text which is nested several groups deep and collapses to
  one shared top-level id when tagged - see `_topmost_top_level_ancestor`)
  were also tagged `brand_title_1`/`brand_title_2` in a SEPARATE run to
  test the top-region left/right logo split specifically.

**Verified live through the running API** (not just direct function
calls) at both of the task's target sizes, for both boards:

| Board | Target | Footer layout | Result |
|---|---|---|---|
| AL MADEENA | 30x40in (portrait) | English above Tamil | PASS (en y=127.5 > ta y=25.9) |
| AL MADEENA | 240x36in (landscape) | English left of Tamil | PASS (en x=118.0 < ta x=3138.6) |
| DARSHAN | 30x40in (portrait) | English above Tamil | PASS (en y=127.5 > ta y=25.9) |
| DARSHAN | 240x36in (landscape) | English left of Tamil | PASS (en x=118.0 < ta x=3138.6) |

The top-region test (AL MADEENA, `s4`/`s5` tagged as two `brand_title`s)
confirmed a genuine left/right split with zero overlap between the two
reserved halves, at both target sizes. Every bitmap's aspect ratio (`s45`/
`s47`/`s48`/`s49`/`s6` on AL MADEENA, `s4` on DARSHAN) stayed within
2.5e-5% of its original value across all 4 live tagged runs, confirming
item 2's uniform-scaling guarantee holds through the new placement paths
too, not just the old ones.

11 new tests in `test_orientation_adapter.py` (65 total, up from 54):
`_split_left_right_by_x`'s mean-based split and its tie-breaking fallback;
`_place_top_region` with 1 vs. 2 ids; `_place_main_and_subobjects` with 1
id (unchanged) vs. 3 ids (largest becomes main_object, flanked without
overlap, the PowerClip-nested bitmap inside it keeps its exact aspect
ratio); `_place_footer_banner` with no Tamil present (unchanged) vs. a
genuine English/Tamil pair, both for portrait (stacked) and landscape
(side by side); and a full `convert_orientation` end-to-end test on a
scene tagged with all three wireframe roles at once (2 logos, a product
-image plus 2 flanking subobjects, and a real Tamil/English address pair),
confirming zero overlap and exact bitmap-aspect preservation at both a
landscape and a portrait target. All 436 backend tests and 121 frontend
tests pass.

### Portrait footer height + product-zone absorption fix (a real bug, found via a UI report)

A UI test reported that converting a wide master (125x48in, R~2.6) to a
30x40in portrait target (R=0.75) left "huge top/bottom dead space and
cramped horizontal content". Reproduced live with a synthetic wide scene
(logo badge, product bitmap, brand text, secondary bitmap, footer text) -
**the stack template itself was not the problem** (it already does
vertical stacking for R < GRID_RATIO and was already measured at ~89.8%
vertical zone-fill on a real board - see "Three templates"/`_fill_frame`
above). The real cause: an untagged VECTOR shape (a curve/group logo or
badge, not text and not a bitmap) has **no slot at all** in
`product_engine.py` (`SLOT_KINDS` covers only `product_image`/bitmap and
four text kinds - see "Product slots") - it can only reach a named zone
through `convert_orientation`'s "absorb leftover `other` content into an
empty zone" fallback, and that fallback deliberately excluded
`ZONE_PRODUCT` (correct for the WIDE template, where product is a
full-height side column, not comparable to header/main_text/footer by
vertical position - but that exclusion doesn't hold for the STACK
template, where product occupies the same top-to-bottom band as the
others). Confirmed live: a vector-only repro (no bitmaps, so product was
ALSO empty) crushed 3 distinct shapes into the same absorbed zone.

Two targeted fixes, both gated so the wide/grid templates are provably
unaffected (verified: all 438 existing backend tests still pass unmodified):

- **`FOOTER_FRAC_PORTRAIT = 0.15`** (was the shared `FOOTER_FRAC = 0.20`)
  for the stack template only (`R < GRID_RATIO`) - matches this task's own
  explicit "bottom 15% height, full width" footer spec; wide/grid keep 0.20.
- **`ZONE_PRODUCT` joins the absorbing set when the target is portrait**
  (`target_w < target_h`) and product is genuinely empty - gives an
  untagged logo/badge/secondary-graphic content a real, properly
  -sized zone instead of falling through to the weak `other` fallback
  (`fit_scale = min(target_w/page_w, target_h/page_h)`, a single global
  uniform scale applied per-shape at its own unchanged proportional
  position - literally "shrinks into the canvas centre" for a target this
  much narrower than the source). Confirmed via the actual
  `/api/scene/convert-orientation` endpoint on the 125x48in->30x40in
  reproduction: with product bitmaps present (so product zone was already
  non-empty and unaffected by this change), header/product/main_text/
  footer all populate distinctly and stay on-page; a second, vector-only
  repro (product genuinely empty) went from 3 shapes crushed into one
  zone to 3 shapes in 3 distinct vertical positions.

2 new tests in `test_orientation_adapter.py` (67 total, up from 65): the
portrait footer fraction (0.15 for stack, 0.20 unchanged for wide/grid),
and the product-absorption fix (confirms product joins absorption only
when portrait AND genuinely empty, and that a landscape target on the
same scene is provably unaffected - the `other`-fallback resize op for
that id set never appears in the wide-target op list). All 438 backend
tests pass (up from 436).

**Honest remaining gap, not closed by this fix**: there is still no
untagged HEURISTIC for a vector logo/badge shape the way bitmaps get one
(`product_image`) and contact text gets one (regex match) - see "Product
slots"/"Designer dataset analysis" (real logos are often raw ungrouped
curves). This fix gives such content a *chance* at a properly-sized zone
via absorption when one is empty, but doesn't give it a genuine `header`
-role identity the way a real `brand_title`-tagged text shape gets -
absorption still assigns purely by vertical-position capacity, not by
role. Per this codebase's established rule of engagement (CLAUDE.md
"Example-based layout engine" - don't add a heuristic without real data
to validate it against), no new slot kind was added here; that remains a
larger, separately-scoped piece of future work, not attempted in this
change.

### Portrait stack template rewritten as ratio-driven normalized bands (supersedes the 0.15 footer above)

The previous subsection's `FOOTER_FRAC_PORTRAIT = 0.15` and the old 0.42/0.52/0.06 header/product/
main_text fractions are **superseded**. The stack template (`R < GRID_RATIO`, every portrait aspect) is
now `_stack_bands`: fixed normalized bands of the canvas height (0 = bottom, 1 = top), identical for
every ratio - footer 0-0.20 (`FOOTER_FRAC_PORTRAIT = 0.20`), product 0.20-0.55, branding/`main_text`
0.55-0.72, header 0.72-0.98 - each inset only by the usual margin/gap. Order is header, brand,
product, footer: the brand logo is a "roof" strictly above the products, whose base sits right above
the footer. Non-degeneracy holds for any `target_w < target_h` (proof in `_stack_bands`). Verified
identical band edges at 0.75 (30x40), 0.667 (24x36), 0.707 (A-series), 0.375, 0.25 and 0.975.

For portrait targets, untagged (`other`) shapes are no longer split by capacity: each joins the zone
whose band contains its own normalized centre-y on the SOURCE page (same boundaries), so header badges
reach the existing left/right-by-x corner split, the brand logo lands in the branding band and the
table/products fill the product band. Found by running a vector-only wide master through the first
version of the new bands: capacity absorption pulled the brand logo into the header and stretched a
thin object across the brand band. Landscape/grid targets keep the old capacity absorption unchanged.
Honest limit: a band with no source shape in it stays empty (not padded with unrelated content), and
a shape is judged only by its centre, so a tall shape straddling two source bands goes to one of them.
Tests: 447 backend tests pass (new: band edges across 6 portrait sizes, and an end-to-end distribution
test at 0.75/0.667/0.707 checking bands, left/right badges, brand-above-products and no overlaps),
also checked through `POST /api/scene/convert-orientation`.

#### Follow-up: top-right badge promotion and portrait main-object boost

**Promotion.** A secondary badge on the source's upper right (a "BLACK STONE" header badge, source
centre-y ~0.65) routed by centre-y alone landed in the branding band and left the top-right corner
empty. For portrait targets, untagged shapes with source centre-y > 0.50, lying wholly in the right half
(left edge >= 0.50) and badge-sized (height <= 40% of the source page) are promoted into the header,
where the existing left/right-by-x split puts them top-right beside the left badge (constants
`PROMOTE_*`). Two deviations from the literal ask, both found by tests: "centre-x > 0.50" promoted a
central logo at x = 0.525 (broke `..._absorbs_other_into_empty_named_zones_by_vertical_band`), so the
test is on the shape's LEFT edge; and the height cap keeps a tall right-side product composite out.

**Boost.** A plain 1.25-1.35x scale cannot be applied to a zone's content: vector content already
fills its band, and a bitmap's uniform contain-fit already fills one axis, so scaling further overflows
into neighbouring bands or columns. Measured: with bitmap products the table was only ~11% of canvas
height, width-limited inside a narrow middle column. The boost is therefore realized by widening the
main object's column (`PORTRAIT_FILL_BOOST` = 1.3 x the default 0.5 share, capped at 0.85), which scales
the main object ~1.3x (table 335 -> 445 mm at 30x40, aspect drift 0.0000%) while the flanking sticks/box
columns narrow - no overlap. Portrait only; a zone with a single object is unchanged (it already fills
its frame).

**Not changed / limits.** The branding logo was already vertically centred in its band (centre 0.635 vs
band centre 0.635) so no change was made there. Bitmap groups are centred vertically in the product
band, not bottom-anchored, so the table's base sits above 0.20 (0.30 in the 30x40 check) rather than on
it (**superseded: see "Product zone stands on the baseline" below**). Tests: 455 backend tests pass (new: promotion at 3 ratios, the two must-not-promote cases, boost at
3 ratios, portrait-only).

### Page-covering PowerClips are unwrapped: their foreground children are extracted and routed

`classify_zones` used to treat a page-covering PowerClip as an opaque background, so on the real AL
MADEENA and DARSHAN masters (one clip holds nearly everything) no cross-orientation routing could ever
reach the content inside it. Now, for a page-covering shape of `kind == "powerclip"` that is not locked,
the container keeps the `background` role and its direct children that are **separable foreground** are
extracted and routed (`_is_separable_foreground`, `_child_zone`):
- a slot tag on the child wins (image tag only if it contains a bitmap, text tag only on text); contact
  text (`pe.CONTACT_RE`) goes to the footer; otherwise the child's normalized centre-y on the SOURCE
  page picks the zone: footer < 0.20, product 0.20-0.55, branding (`main_text`) 0.55-0.72, header >= 0.72.
- plain page-covering shapes, groups and gradients, locked containers, and clips whose children are all
  backdrop/clipped art behave exactly as before.

**A rule the spec did not have, found by reading the real scenes**: not every child is foreground. Each
real clip holds (1) a full-bleed backdrop bitmap (coverage 1.07-1.22), (2) a small group of bitmaps (`s46`,
the table composite) and (3) a huge art group (`s50`, 159-318 leaves) whose centre is BELOW the page
(cy -0.06 / -0.10). Routing (3) by centroid would squash a mostly-hidden bounding box into the footer
band, so a child counts as foreground only if it is not backdrop-sized (< 90% of the page) and lies >= 90%
inside the page (`SEPARABLE_MIN_INSIDE`). On both real boards exactly `s46` is extracted; `s45`/`s50` stay
with the container.

**Op ordering changed** (needed for correctness): `convert_orientation` now emits the background cover-fit
ops right after the `page` op and computes every zone op from the scene AFTER them. `_scale` carries a
container's children with it, so a child op computed from its original box and applied after the container
op would be transformed twice. Op order in the list therefore differs from before (background first);
final positions do not, and no existing test depended on the order.

**Verified through `POST /api/scene/convert-orientation` on the real cached scenes** (30x40in portrait and
240x36in wide): the container still covers the page exactly; `s46` lands inside the product frame (portrait:
y 0.26-0.49 / 0.22-0.53 of the canvas against a 0.21-0.54 product frame); its bitmaps' aspect drift is
< 0.0003%; the clipped art group scales by exactly the container's factor. One existing test
(`..._page_covering_container_as_background_even_with_a_heuristic_slot_nested_inside`) asserted the old
behaviour (nested bitmap dropped) and was updated: the container is still the background and the bitmap is
now routed to `product`. 9 new tests (92 in `test_orientation_adapter.py`): routing into all four zones,
backdrop/clipped art excluded, tag beats band, contact regex beats band, non-PowerClip and locked containers
untouched, and end-to-end placement inside the zone frames at two portrait and one landscape target with no
double transform.

**Limits.** Only direct children of the clip are considered (a group is one unit; nothing inside a child group
is split up). A child is judged by its centre, so a shape straddling two bands goes to one of them. The
top-right badge promotion is NOT applied to extracted children. Moving PowerClip children uses the editor's
existing move/resize-inside-a-clip support; the CorelDRAW replay of those ops was not re-verified live in this
change. `other` still receives the real boards' loose top-level shapes as before.

### Product zone stands on the baseline (bottom-anchored) + bounded boost

Bitmap groups were centred vertically in their zone, so a table/pedestal shorter than the product band
hovered above the footer (real AL MADEENA table: base at 0.26 of the canvas height instead of ~0.20).
`_zone_fit` now takes `anchor_bottom` and `boost`; for the **portrait product zone only**
(`_place_zone_content`: `zone == ZONE_PRODUCT and portrait`) every object - a lone one, the main object and
its flanking columns - has its bottom edge on the product frame's bottom edge (the baseline just above the
footer, 0.207 of the canvas height at every portrait ratio) and stays horizontally centred in its frame or
column. Vector/text content anchors the same way. Branding (`main_text`), header, footer, and the wide/grid
templates keep their standard centred fit (tests lock this in).

**Boost, honestly measured.** A contain fit already fills one axis of its frame, so a lone object can only grow
by the padding that was reserved. `boost` (`PORTRAIT_FILL_BOOST` = 1.3) is bounded by the UNPADDED frame on both
axes - the top can never pass the frame top (below the branding band) nor the width the frame width - and the
factor actually applied is min(1.3, that headroom): **measured 1.03x (3:1 table) to 1.06x (bottle, box, 2:1
table)**, not 1.25-1.35x. A real ~1.3x exists only for a main object with flanking objects, via the widened main
column from the previous round (table 335 -> 445 mm at 30x40); that path is not boosted again, so the total
stays ~1.3x. Reaching 1.3x for a lone object would need it to leave its frame.

**Verified** on the real cached boards at 30x40in through `POST /api/scene/convert-orientation`: AL MADEENA
`s46` (table composite) and `s6`, DARSHAN `s46` and `s4` all have their bottom exactly on the product baseline
(0.207; offset 0.000 mm) and their tops at 0.44-0.52, below the branding frame; the container still covers the
page. 19 new tests (111 in `test_orientation_adapter.py`, 482 backend total): lone product at 3 portrait
ratios x bitmap/vector, main + flanks on one baseline, product never reaching the branding zone or footer,
boost bounded/aspect-kept/never shrinking, anchoring limited to the portrait product zone, and the two real
boards (skipped if the cached scenes are absent from a checkout).

### Portrait -> wide: an explicit direction, a wide "stage" template, horizontal unstacking

A portrait master converted to a wide target used to hit the old 3-bucket capacity split (or, for a tall
master, the per-shape proportional fallback), so a vertical arrangement shrank into a dense central block.
`convert_orientation` now derives an explicit `Direction(page_w, page_h, target_w, target_h)` = (source,
target) orientation, replacing the single `portrait = target_w < target_h` flag (which said nothing about the
source). `direction.to_portrait` keeps the old meaning for the helpers that still take that flag;
`direction.portrait_to_wide` selects the new path.

**Scope decision (deviation from "all target_w > target_h")**: the new geometry applies only when the SOURCE
is portrait. `calculate_zone_rects(..., portrait_source=True)` returns `_wide_stage_bands`; a landscape
source keeps `_wide_zones`/`_grid_zones` exactly as before, because those carry the documented and tested
real-board results (header centred over 240x36in, etc.) and a portrait-only report gave no reason to move them.
Any wide target (R > 1, so also 1 < R < 2) from a portrait source uses the stage.

**Stage bands** (fractions of canvas height, identical for every wide ratio, all full width): footer 0-0.12,
product stage 0.12-0.65, branding roof 0.65-0.83, header 0.83-1.0, each inset by gap/2 (m at the page edges);
non-degeneracy proof in `_wide_stage_bands` (min(W,H) = H for a wide target).

**Routing**: untagged shapes of a portrait source are routed by the same source-y bands as a portrait target
(footer < 0.20 <= product < 0.55 <= branding < 0.72 <= header), plus promotion of small upper-quadrant badges
(centre-y > 0.5, wholly in the left OR right half, <= 20% of page height and <= 50% of width - stricter than the
portrait-target rule because a portrait page is tall, so a product must not pass for a badge) into the header
(`P2L_BADGE_*`). Extracted PowerClip children (see above) flow through unchanged.

**Placement** (`_place_p2l`): header badges go to the far top-left / top-right slots by source side (uniform
scale, aligned to the corner and the row top, each slot `CORNER_SLOT_FRAC` = 25% of the width); band-routed header
shapes near the horizontal middle (centre-x in the middle third, not promoted) join the branding roof, which is one
centred, uniformly scaled slot (`BRAND_SLOT_FRAC` = 50% width). Uniform, not the fill-to-frame used elsewhere: a
square badge stretched into a 120in x 5in strip would be unusable. Products go through `_unstack_products`:
`_unstack_order` groups items whose x-ranges overlap >= 50% of the narrower into a column (a vertical stack),
reads each column top to bottom, and columns left to right; ONE common uniform scale (largest that fits the
stage width less the minimum clearance, and its height) keeps every product's proportions and the relative sizes
between products; all bottoms stand on the stage's bottom edge (the shared baseline, y ~ 0.13); leftover width is
spread as equal gaps between products and at both ends, and a tight row keeps exactly the minimum clearance
(`UNSTACK_MIN_CLEARANCE_FRAC` = 2% of the stage width). Footer keeps the English-left / Tamil-right banner.

**Verified**: a synthetic 30x40in master (two badges, brand roof, three vertically stacked products, footer) at
2:1, 4:1 and 6:1: the stack unfolds A, B, C left to right, all on the baseline, proportions kept, clearance kept,
lineup spans 0.5+ of the stage, badges in the far corners, brand centred, footer slim, zero overlaps. The REAL
DARSHAN board (a genuine 762x1016mm portrait master) through `POST /api/scene/convert-orientation` at the same
ratios: container still covers the page, `s38`/`s23` in the far top corners, `s5` centred in the roof, the
English/Tamil footer lines side by side, `s46`/`s4` on the baseline.

**Honest limits.** On DARSHAN the products do NOT fill the stage: the tall `s46` table composite scales to the
stage height (so it is only ~6-12% of the width) and the small `s4` bitmap keeps its small relative size (under
2% of the width at 2:1 and less at wider ratios) - faithful to the source's relative sizes, but a mostly empty
stage; a designer would enlarge the small product. Each product is one whole id, so products that intentionally
overlap on the source (sticks standing in front of a table) are separated. The source-band routing assumes the
portrait master follows the usual header / brand / product / footer stacking. **Tests changed on purpose**: four
existing tests converted the 400x1000 portrait fixture to a wide target and asserted behaviour this path
replaces - the two "untagged `other` stays at its proportional position" tests now use a transposed (landscape)
copy of the fixture, the header non-uniform-stretch test uses a portrait target, and the assembly-group test uses
the portrait-source frames. 19 new tests (130 in `test_orientation_adapter.py`, 501 backend total).

### Extreme aspect-ratio matrix and live CorelDRAW replay (final verification round)

**1. Matrix** (`backend/tests/test_orientation_matrix.py`, 45 cells, all passing): five fixtures (the REAL AL
MADEENA landscape master, the REAL DARSHAN portrait master, and three synthetic masters) x nine targets - 1:4, 1:3,
1:2 (tall), 1:1, 4:3 (square/near-square), 2:1, 4:1, 6:1, 8:1 (wide). Asserted in every cell: every op applies;
every foreground shape is inside the canvas (no NaN); no NEW bounding-box collision (overlaps already present on the
source are kept, AL MADEENA has 2 and the synthetic tall master 2); bitmap aspect drift <= 6.2e-6 (measured; the test
allows 1e-4); conversion time 0.1-4 ms (the test allows 1 s). Where a baseline is defined it holds: portrait
targets put the lowest product on the product frame's bottom edge, 0.2025 (1:4) - 0.2098 (0.975) of the height and
0.207 at 30x40 (the "0.207" of the spec is aspect-dependent by construction, so the test asserts the frame bottom and
the 0.20-0.21 band); a portrait master unfolded to a wide target sits on exactly 0.13. Two matrix cells first "failed"
because MY expectation was wrong, not the engine: a vertically stacked pair of products moves as one flank group, so
only its LOWEST member is on the baseline - the invariant is the lowest product bottom.

**Defined gap**: a LANDSCAPE master converted to a wide/grid target (AL MADEENA at 2:1..8:1, 1:1, 4:3) has no product
baseline - it uses the original side-column template (see the portrait->wide section for why); square targets (1:1)
use the grid template for every source. The "Y ~ 0.13 for wide" requirement is therefore met for portrait sources
only.

**Ratio limits (measured, not asserted).** Bitmaps never distort, but VECTOR/TEXT groups fill their zone
non-uniformly, and at extreme ratios that is a large stretch: worst single-shape stretch (width factor / height
factor) - AL MADEENA 12x at 1:4, 9x at 1:3, 6x at 1:2, 5.7x at 1:1, 4.2x at 4:3, 2.7x at 2:1, 3.9x/6x/8x at
4:1/6:1/8:1; DARSHAN 12x, 9x, 6x, 8.2x, 3.5x, 2.3x, 1.3x, 1.9x, 2.6x; synthetic masters up to ~20x. It is visible
in the live renders: at 1:2 the "Sugandha Swarna" Tamil badge glyphs are tall and narrow, at 1:4 that badge and the
footer lines are badly distorted. Shrink is the other extreme: DARSHAN's small `s4` bitmap is scaled to 0.067x at 8:1
(relative sizes are preserved). **Practical limit at the time of this matrix: portrait targets down to about 1:2 and wide targets up to
about 4:1 looked right; beyond that vector logos/text distorted.** (Superseded: the stretch cap below was added
right after and removes this limit at the cost of coverage.)

**2. Live CorelDRAW replay** (real `corel_worker` export_replay job, CorelDRAW build **27.0.0.121** - the earlier
sections verified 2019/v21, so this is also new version-compatibility evidence; ops from `convert_orientation`
written to a scratch folder, the shops' saved edit history in the database untouched). All five exports: `.cdr`,
`.pdf`, `.jpeg`; CorelDRAW's own verification re-walks the replayed document and compares it with the expected scene
(structure, z-order, positions, visibility, text) - **0 mismatches and 0 warnings on all five**:

| Board -> target | objects compared | wall time | launch / open / replay / verify / cdr / pdf / jpeg (s) |
|---|---|---|---|
| DARSHAN -> 120x30in (4:1) | 208 | 39.4 s | 1.5 / 4.7 / 1.1 / 1.4 / 10.0 / 2.7 / 0.7 |
| DARSHAN -> 20x40in (1:2) | 208 | 40.5 s | 1.5 / 4.8 / 1.1 / 1.5 / 10.0 / 3.4 / 0.9 |
| DARSHAN -> 10x40in (1:4) | 208 | 40.3 s | 1.5 / 4.7 / 1.0 / 1.2 / 10.0 / 3.2 / 0.8 |
| AL MADEENA -> 30x40in | 371 | 35.3 s | 1.5 / 3.3 / 1.8 / 2.5 / 5.9 / 2.2 / 0.5 |
| AL MADEENA -> 240x30in (8:1) | 371 | 35.3 s | 1.4 / 3.1 / 1.9 / 2.6 / 5.9 / 2.3 / 0.5 |

**The "<1 minute per board" goal is met: 35-41 s wall-clock for launch + replay + verify + all three formats;**
converting itself is milliseconds. The .cdr save (6-10 s, the 125-209 MB embedded backdrop) dominates. Exported .cdr
sizes equal the source's (208.8 MB source vs 208.76 MB), so the file is not inflated by the conversion.

**A real bug found by looking at the JPEG, fixed**: `export_raster` called `ExportBitmap(cdrCurrentPage,
ExportArea=None)`, which was verified live to render the whole DRAWING extent, not the page, once content lies off the
page. An orientation conversion's cover-fit background deliberately overflows it (5.3x the page height for
DARSHAN -> 4:1), so every foreground shape in the JPEG was squeezed to ~1/5 of its height around the centre - while
the .cdr, the verification and the PDF (MediaBox 120x30in, checked) were all correct. `_page_export_area` now builds
the page rectangle with `Application.CreateRect` and passes it as `ExportArea` (falls back to None if the document
cannot provide one); the earlier phases never met this because their boards had nothing off the page. 2 new tests.
Same function serves PNG; PNG was covered by the unit test but not exported live in this round (JPEG was).

**What the rendered files show** (viewed, not just compared): the extracted PowerClip child `s46` renders as the
table-with-box composite standing on the baseline; the unstacked wide products, the promoted corner badges
("Sugandha Swarna" top-left, "BLACK STONE" top-right) and the centred brand roof render without corruption or missing
layers, and every text line is present; AL MADEENA at 30x40 and DARSHAN at 1:2 are clean, complete boards.

**Known visual limits found in the renders** (documented, not fixed): (a) at wide targets the cover-fit crops the
background's maroon footer band away, so the white shop-name text sits on cream and is low-contrast (DARSHAN 4:1,
AL MADEENA 8:1); (b) the vector stretch above; (c) at AL MADEENA 30x40 the footer text block is slightly taller than
the maroon band and crosses its scalloped edge; (d) DARSHAN's small product stays small on the wide stage, leaving it
mostly empty.

**Final stats**: backend `pytest` 548 passed (was 501 before this round: +45 matrix cells, +2 export tests);
frontend `npm test` unchanged. Not exercised: the editor UI in a browser, a `swap_image`/product-slot replay, PNG/PDF
option variants, CorelDRAW versions other than 27.0. **Readiness verdict**: geometry, replay fidelity and speed are
production-grade and verified live; the two visible-quality limits (stretch at extreme ratios, cropped footer band on
wide targets) are known and unfixed, so deploy for designer-tweak use with tall targets limited to ~1:2 and wide to
~4:1, or land the stretch cap first.

### Stretch cap: vector/text groups fall back to a uniform contain fit (`MAX_STRETCH_RATIO`)

Vector and text groups are resized as one rigid unit into a zone frame and used to be stretched non-uniformly to FILL it
(`_fill_frame`) - at extreme ratios that warped Tamil glyphs and logos (12x on both real boards at 1:4, see the matrix
section). `_zone_fit` - which every non-bitmap placement goes through (`_place_main_and_subobjects`, the footer banner,
the header split, the absorption path) - now computes the group's aspect stretch `max(sx/sy, sy/sx)` (sx = fill width /
source width, sy likewise) and, above `MAX_STRETCH_RATIO` (**2.0**), abandons the fill: it applies the SMALLER factor to
both axes (a uniform contain fit) and centres the group in its frame, or stands it on the frame's bottom edge on the
product stage (`anchor_bottom`). Within the cap behaviour is unchanged (still fills the frame). Configurable per call:
`convert_orientation(scene, w, h, max_stretch=...)` (1.0 = never distort; a huge value restores the old fill-to-frame
behaviour) via a context variable, so the override cannot leak out of the call (tested). The API endpoint does not
expose it.

**Exemptions** (`_stretch_exempt`): a plain `rectangle` (a solid colour panel) and, for a top-level shape, a bare
childless non-text shape spanning >= 95% (`FULL_BLEED_FRAC`) of the SOURCE page in either dimension (a full-bleed border
bar) keep stretching edge to edge; a rigid group with any text/children/glyph-carrying member is not exempt. The
page-covering background container never reaches `_zone_fit` (it is cover-fit, uniformly, and still covers the page -
tested). Bitmaps keep their existing uniform logic. Nested shapes are not page-tested for full-bleed (their coordinates
have been cover-scaled). The source page size reaches `_zone_fit` through `_Idx`, a `dict` subclass with a `.page`
attribute (nothing iterates `idx`).

**Measured on the real boards (all nine ratios):** worst vector/text stretch is now **<= 1.97x everywhere** (was 12x at
1:4, 9x at 1:3, 6x at 1:2, up to 8x wide); the matrix test asserts it in all 45 cells. **The cost is coverage**
(foreground area as a share of the canvas): AL MADEENA 1:4 56.6% -> 26.8%, 1:3 58.1% -> 31.1%, 1:2 61.0% -> 42.2%,
8:1 52.1% -> 24.3%; DARSHAN 1:4 70.0% -> 39.4%, 1:2 61.7% -> 41.4%, 4:3 22.0% -> 17.7%; DARSHAN at 4:1 and 6:1 is unchanged
(its stretch was already within the cap there). Groups that used to fill a zone are now smaller and centred, so extreme
targets show more empty background.

**Live check** (CorelDRAW 27, real replay + verification, DARSHAN -> 10x40in, 1:4): 208 objects compared, 0 mismatches,
0 warnings, 40.4 s. The rendered JPEG has an undistorted "Sugandha Swarna" Tamil badge, brand roof and footer lines - the
warped glyphs of the uncapped 1:4 render are gone. Visible consequences: the header badges are small and centred in their
half-frames, leaving an empty band at the top, and the two footer lines are small and sit apart in the tall footer band.

**Tests:** 13 new in `test_orientation_adapter.py` (143 in the file) - fills while within the cap, uniform + centred
beyond it (scale_x == scale_y), bottom-anchored on the product stage, per-call configuration, exemptions (solid panel,
full-bleed bar, mixed group, non-bar), <= cap for vector/text at 1:4 and 8:1 on three masters, end-to-end uniform
footer with the container still covering the page - plus the stretch invariant added to all 45 matrix cells. One
existing test asserted the old 3.9x stretch (`..._stretches_a_text_only_zone_non_uniformly`) and now checks a moderate
stretch that stays within the cap. **Suite: backend 561 passed (548 before), frontend 121 of 121, zero regressions.**

**Limits.** 2.0 is a judgement call between distortion and coverage (the spec suggested 1.8-2.0); it is one constant.
The cap is per rigid group, so a group whose members differ a lot in shape is judged as a whole. Uniform fallback shrinks
wide footer text lines to the frame height, so at wide targets they end up centred and narrower than the frame. The
earlier statements in this file that vector/text zones "fill the zone bounds" (the 85-90% utilization figures) describe
the pre-cap behaviour and hold only within the cap.

### Real-board round: shattered Dalmia logos, snapped corner badges, footer sizing, table width

**1. The Dalmia "giant white rectangles" - real bug, different cause than reported.** Reproduced on the real
Dalmia 120x48 master (scene exported from a scratch COPY of the dataset file; `signage_dataset/` untouched) and on a
cached Dalmia board: a live 30x40 conversion rendered a white house shape cut off mid-word ("FOUNDATI"), a white
rectangle holding a cropped Tamil fragment and a stranded icon. The cause is NOT a solid rectangle wrapped inside a
text/logo group: the Dalmia masters are 138 LOOSE top-level curves (the documented ungrouped-logo structure), and band
routing sent each fragment to a zone by ITS OWN centre - a white card and its content (and the card and its shadow)
landed in different zones, the largest fragment (the card) became a zone's "main object" as a giant slab, and its
content was stranded as tiny pieces. Stripping the rectangles was NOT done: those white cards are the design (they
carry dark-blue text; without them the text is invisible on the blue board).

Fix: `_group_fragments` emits `group` ops at the start of `convert_orientation` (after the `page` op), so every later
stage sees ONE object per logo. Loose vector shapes cluster by bounding-box proximity (`CLUSTER_GAP_FRAC` = 0.009 of the
page's long side, ~20 mm on the 90x30in Dalmia board; 0.0066 left "EXPERT" split) - result on the real Dalmia
masters: **24 / 50 / 58 members = the documented badge / Tamil card / roof graphic**. Only top-level, visible,
unlocked, untagged, non-text, non-bitmap shapes/groups are candidates; a cluster is grouped only with >= 2 members and
>= 1 LOOSE shape (so AL MADEENA and DARSHAN, whose logos are already groups, get no group ops - tested on the real
scenes). Second defect found in the first live re-render: each card's soft drop shadow is a GROUP HOLDING A BITMAP,
which the product-image heuristic claimed and dragged away (a stray dark rectangle, the card shadowless). A
bitmap-bearing, text-free shape whose box overlaps a cluster's box by >= `SHADOW_IOU` (0.6) now joins that cluster (a
product photo next to a logo does not), and `classify_zones` ignores heuristic slots inside a synthesized "Logo
cluster" group so the logo is not mistaken for a product. The extracted-PowerClip-children path and the cluster path
share the same downstream code.

**2. Corner badges snap to canvas points** (`_place_snapped_badges`, called from the header branch of
`_place_zone_content`, portrait targets only - the spec named `_place_main_and_subobjects`, which handles the
product/branding zones, not badges): left group centred on X = 0.18 of the width, right group on X = 0.82, top edge
on Y = 0.92 of the height (`BADGE_*`), each group one rigid unit scaled UNIFORMLY (contain) in a slot that reaches
from the margin to the mirrored point. A LONE header badge snaps to the side it sat on in the source (left/right
third; middle third keeps the centred fit) - the Dalmia master's single top-right badge was being centred and
stretched. Verified on real DARSHAN: X 0.180 / 0.820, top 0.920 exactly. Consequence: DARSHAN's Sugandha Swarna oval is
now small (0.30W x 0.04H) - its true source proportion; the old stretch had inflated it. The brand roof is centred at
X = 0.500 and Y = 0.635 (the band centre; the spec said ~0.65 - 0.015 off, band positions were locked earlier).

**Table width 85-92%: not achievable for DARSHAN, measured.** Its `s46` composite is tall (source 338 x 728 mm = 0.44W
x 0.72H, aspect 0.464) and its "table" bitmap `s47` is itself nearly square (0.87). 85-92% of the canvas width
would need 1.37-1.49x the canvas height = 4.1-4.4x the product frame (0.335H), i.e. overflowing the canvas or
stretching a photo 4x - neither done. What exists: a lone bottom-anchored bitmap composite fills its zone but is now
capped at exactly 92% of the canvas width (`PRODUCT_MAX_WIDTH_OF_FRAME`); a wide table (3:1) lands at 85-92% at
every portrait ratio (tested). To make DARSHAN's composite bigger the product BAND would have to grow (the composite is
0.31H tall in a 0.335H zone, source 0.72H) - a design decision, not made here.

**3. Footer text**: shop name/contact occupy `FOOTER_CONTENT_FRAC` = 0.65 of the footer band's height (centred in it) on
portrait targets and the portrait->wide stage (`_place_footer_banner(content_frac=...)`). Measured: a single block that
can fill it ~61%; the stacked English/Tamil pair on the real boards 53-62% of the band - i.e. 60-70% is NOT guaranteed
for very wide lines, because the stretch cap stops them being stretched to full height. 0.73 was tried to push it up
and reverted: at 24x36 a 10:1 footer line's stretch crossed the 2.0 cap and the uniform fallback HALVED its height
(0.68 -> 0.34 of the band) - the cap is discontinuous at its threshold, worth knowing. Contrast (white text on cream when
a wide target crops away the maroon band) is unchanged and unaddressed.

**4. Validation.** Suite: backend `pytest` **595 passed** (561 before; +34 tests: clustering, shadows, classification guard,
snapping incl. the lone badge and real DARSHAN, footer share, wide-table width, and the real 138-fragment Dalmia board
added to the ratio matrix). Two existing tests were updated on purpose (badge top edge 0.95 -> the 0.92 snap; Dalmia
cluster sizes). `POST /api/scene/convert-orientation` at 30x40 on the real Dalmia 120x48 master (12 ops incl. 3 `group`
ops -> 10 after the shadow fix) and DARSHAN: HTTP 200, nothing outside the canvas. Live CorelDRAW replay (build 27.0),
CorelDRAW's own verification: Dalmia master -> 30x40 145 objects, 0 mismatches, 0 warnings, 24 s; DARSHAN 208 objects,
0 mismatches, 41 s. Rendered JPEGs viewed: DARSHAN is a clean board (badges at the snapped points, brand roof centred,
products on the baseline, large legible English/Tamil footer inside the maroon band); the Dalmia master is coherent
(badge top-right, "ROOF COLUMN FOUNDATION EXPERT" complete, the Tamil card with its icon, wordmark and shadow intact).

**Honest limits.** "Matches human designer standards" cannot be asserted from here - the numbers above are met or
explained, but a designer's eye was not in the loop. Remaining visible weaknesses: the Dalmia board has a large empty
band and its Tamil card is very large (65% of the width) because clusters are routed as single objects; DARSHAN's
composite is narrow and there is empty space between the brand roof and the products; the Tamil footer lines on Dalmia
render as boxes (the missing-font limitation documented earlier); grouping touching loose vector shapes is a
heuristic - two genuinely separate objects that touch WILL be merged (seen once in a synthetic fixture). The op list now
begins with `group` ops for fragmented masters; they replay through CorelDRAW (verified) and appear in the editor as
groups named "Logo cluster".

### Pedestal stage expansion and central brand-roof boost (portrait targets)

**Pedestal.** `_has_bottom_support(node)`: a group with >= 2 children where one child is bottom aligned to the group
(within `SUPPORT_BOTTOM_TOL` = 3% of its height) and spans >= `SUPPORT_MIN_WIDTH_SHARE` (50%) of its width - the table of a
table-with-products composite. `_place_main_and_subobjects(..., pedestal=True)` (portrait PRODUCT zone only, bottom-anchored)
uses it when all flanking products sit on ONE side: the main column gets `PEDESTAL_TARGET_W_FRAC` = 80% of the CANVAS width
(+ the padding `_zone_fit` removes), the flanks share the remaining column on their own side (floor
`PEDESTAL_MIN_FLANK_FRAC` = 9% of the frame; 10% held the table at 78%). Applied only if the widened composite still fits the
stage height (top <= the 0.55 ceiling), otherwise the old symmetric split is kept. Anchored contain fits now pad only the top,
so the bottom stays on the baseline (Y = 0.207). Real landscape DARSHAN (120x40in -> 30x40): table `s46` **0.584W -> 0.800W**,
Y 0.207-0.531; the bottle `s4` shrinks to 0.081W (~55% of its former size) to make room. At 24x36 the table reaches 79.5%
(the flank floor binds) - just under the 80% asked; 21x29.7 likewise ~79.8%. Not widened: a tall composite (the portrait
DARSHAN board's, aspect 0.46) - 80% of the width would need ~4x the zone height; unchanged, tested.

**Brand roof.** `_boost_brand_roof`: a LONE group in the branding zone is scaled uniformly, centred on X = 0.5, as large as fits
between the header badges above and the products below (`BRAND_CLEARANCE_FRAC` clearance, ceiling default = the badge line 0.92),
up to `BRAND_MAX_WIDTH_FRAC` = 72% of the zone width; never smaller than the standard fit. Real DARSHAN: 0.290W -> 0.336W
(+16%), Y 0.557-0.726. **72% is geometrically unreachable here**: the snapped badges leave a 0.34W gap between them and the
right badge's bottom is at Y 0.738, so a wider logo would touch it; the bound is the badges, not the cap. The snapped badges
stay at X 0.18 / 0.82, top 0.92 (verified).

**Validation.** Backend `pytest` **607 passed** (+12: `_has_bottom_support`, pedestal width/ceiling/baseline/no-overlap at 3
portrait sizes, tall-composite exception, pedestal-flag scope, brand boost at 3 sizes, no-room fallback, real DARSHAN scene).
`POST /api/scene/convert-orientation` (Cache-Control no-store) on the cached landscape DARSHAN board -> 30x40: HTTP 200 with
the numbers above. NOTE: no 125x48 DARSHAN scene is cached; the 120x40 landscape board was used. Live CorelDRAW replay: 9 ops,
208 objects, 0 mismatches, no warnings, 40 s; the rendered JPEG shows the wooden table spanning the lower stage and the
logo centred under the badges. Limits: the bottle is small, and the empty band beside the roof remains.

### Dual-master templates: landscape + portrait uploads, same-orientation routing

**Why.** Cross-orientation conversion (a portrait master unfolded to a wide target and vice versa) is where the
layout artefacts documented above come from. With one master per orientation a job can convert
landscape -> landscape and portrait -> portrait only.

**Model.** Each upload is still its own `jobs` row; `POST /api/v2/upload` takes an optional `orientation` form field
(`landscape` default, `portrait`) stored in the new `jobs.orientation` column. `POST /api/v2/jobs/{id}/shops` accepts
optional `landscape_master_id` / `portrait_master_id` (new `shops` columns): they must exist (404), belong to the job's
brand and have been uploaded as the orientation of their slot (400). `db._MIGRATIONS` adds the three columns to an
existing database. Shops attach to the first master that exists (landscape preferred), so the editor, scene and export
paths (`<job>/out/<shop>`) are untouched. Both masters are optional; a shop with neither id converts from its job's own
master exactly as before.

**Routing.** `orientation_adapter.select_master(target_w, target_h, landscape_id, portrait_id)` (rule
`target_orientation`: ONLY width > height is landscape; square and portrait targets use the portrait master - changed from the original
"width >= height", so a square target used to be landscape. Width within `SQUARE_TOL_MM` = 0.01 mm of the height counts as square, because
sizes are compared in mm after unit conversion and 48 in = 1219.1999999999998 mm while 4 ft = 1219.2 mm: a plain `>` sent that mixed-unit
square board to the landscape master; tested in `test_dual_master.py`, incl. through the API) returns (orientation, id,
fallback). `main._select_shop_master` uses it in `_v2_convert_worker`; if only the OTHER orientation was uploaded it is
used for every size and the choice is recorded as `fallback: true`. The choice is written to the shop's
`report.master_used` ({job_id, orientation, reason, fallback}). Editor "Re-convert at this size" passes the shop's
master ids on to the new shop, so it re-routes by the NEW size's orientation.

**Same-orientation fit.** `convert_orientation(scene, w, h, same_orientation_fit=True)` (also `same_orientation_fit` on
`POST /api/scene/convert-orientation`): when `Direction.same_orientation`, `_same_orientation_ops` emits a `page` op, a
cover fit for the page-covering background, and ONE resize of every other unlocked top-level shape as a rigid unit -
uniform scale by min(target_w/page_w, target_h/page_h), centred, so an aspect mismatch becomes padding; no re-zoning, no
unstacking, no stretch. A cross-orientation call ignores the flag. **Default is off**: the zone/band machinery above is
unchanged for every existing caller and test, and the editor's OrientationControl does not send the flag yet.

**Which path actually runs a conversion.** The Automation-page conversion goes through `CorelEngine`/`layout.py`
(`compute_layout`), not `orientation_adapter` - so what the dual masters change there is WHICH MASTER FILE is opened;
`layout.py` already scales uniformly and keeps proportions. `orientation_adapter` is the editor's re-layout path, where
the new flag applies.

**UI.** `pages/Automation.jsx` (the spec named `components/Automation.jsx`, which does not exist) shows two
`UploadDropzone`s - "Landscape Master (.cdr)" and "Portrait Master (.cdr) - optional" (both optional; the dropzone now
takes `orientation`/`label` props and sends the orientation) - each with its own preview, and sends both ids when adding
a shop. Verified only by `vite build` and code review: no browser click-through this session (no Playwright).

**Validation.** Backend `pytest` 626 passed (607 + 19 in `tests/test_dual_master.py`: the routing rule, fallback, upload
orientation, 120x48in shop converting from the landscape master and 30x40in from the portrait one - checked via the
report and via the master file path chosen -, ids validation, no-dual-master shops unchanged, and same-orientation fit
uniform/padded/relative-layout-preserving at four size pairs, ignored across orientations, off by default, and through
the endpoint); frontend `npm test` 121 passed. Limits: MockEngine ignores the master file, so end-to-end "the right
`.cdr` was opened" is verified by the chosen path, not by a live CorelDRAW conversion; a masters' brand is checked but
their content (are they really the same design?) is not.

**Follow-up: portrait master ignored (stale shop ids).** Reproduced as a logic bug, not a payload-name bug: a shop row
keeps the master ids it was CREATED with, so a portrait master uploaded after the shop was added left
`portrait_master_id = NULL` and the landscape master won by fallback (the add-shop payload names were already
correct). `POST /api/v2/shops/{id}/convert` now accepts `{landscape_master_id, portrait_master_id}` (same validation,
`_validated_master_ids`), stores them on the shop and converts with them; `Automation.jsx` sends the current ids with
every convert. The worker logs `Shop <id> (WxH mm, <orientation> target): Selected master file path -> <path> (<reason>)`
(logger `signage.convert`). A portrait target with a portrait master opens that master directly: `select_master` runs
before the engine, and the orientation adapter is not on this path at all. Tests: 3 in `test_dual_master_convert.py`
(629 backend total). NOT done: a live Shop 2 conversion checking the rendered layers against the portrait CDR - the
user's masters are not available here, so the "roof badge at bottom / Tamil card upper-mid" check is still to do by eye.

### Shop-name binding, 1.25 master routing, square/vertical boards (2026-09-30)

Found on the real DARSHAN dual masters (125x48 landscape, 36x48 portrait): (1) the v2 conversion never told the engine
what the master's own shop name is, so on these untagged masters the shop name was NEVER replaced (every board printed
"SRI KANNIYAMMAN NATTU MARUNTHU KADAI"); (2) the imported names were whole designer file names ("73 - 60 X 75 Inch -
Nonlit - SHOP.cdr"); (3) square/vertical boards (6x6 ft, 60x75 in) were TILED - `_tile_plan` tiled whenever a board was
enlarged > 1.4x on one axis, even with the same shape, so two copies of every logo sat on one stretched background over
the product box. Fixes:
- **Master shop name**: `jobs.master_shop_name` / `master_shop_name_local` (migration). Upload fills the English one from a
  designer-style file name (`batch_import.shop_name_from_filename`) or the optional form fields;
  `PATCH /api/v2/jobs/{id}/master-shop-name` corrects either. `_convert_job` passes the CHOSEN master's name (falling
  back to its file name for jobs uploaded before this) as `master_shop_name[_local]`. A `shopname`-tagged shape is used
  regardless. Report warning when the master's name is unknown.
- **Tamil line**: the master's Tamil spelling is not known, so `layout.find_local_partner_ids` takes the Tamil text
  stacked under/over the matched English line or beside it on the same row (portrait DARSHAN: stacked; landscape: same
  row). It is rewritten only when the shop HAS a local name; otherwise it is left as-is and the report warns (the English
  name is never printed in the Tamil line's place).
- **Nested texts**: `CorelEngine._replace_nested_shopnames` rewrites shop-name texts inside groups/PowerClips in place
  (tag or content match, same partner rule), fitted to their original width.
- **Import**: `shopImport.js` reads a local-name column (Shop Name (Local)/(Tamil), Local Name, Tamil Name, ...) into
  `shop_name_local` (shown under the name in the Shops table; carried by drafts and the convert payload; new
  `shops.shop_name_local` column, PATCH-able). `cleanShopName` / backend `clean_shop_name` reduce a designer file name
  to its shop name (applied at convert time too, so existing rows print correctly; the output files are then named after
  the shop name).
- **Routing**: `orientation_adapter.target_orientation` = landscape when width / height >= 1.25
  (`LANDSCAPE_MASTER_MIN_RATIO`), else portrait (supersedes "only width > height"): 11x6 ft landscape; 5x5, 6x6, 7x6 ft
  and 60x75 in portrait. The SQUARE_TOL_MM float guard is gone (a ratio has no such edge).
- **Tiling**: `_tile_plan` additionally needs the target stretched along the axis > `TILE_MIN_ASPECT_GAIN` (1.15)
  relative to the other and the target itself >= `TILE_MIN_TARGET_RATIO` (1.25) wide/tall. All four tiled dalmia boards
  and the 2x6 ft stacked case keep their plan (tested); 60x75/5x5/6x6 from the portrait master and 11x6/7x5 ft from the
  landscape one no longer tile (11x6 ft used to get y,2).
- **Background PowerClip**: the `bg` shape is stretched to the new page and CorelDRAW stretches its contents too;
  `CorelEngine._undistort_clip_contents` re-sizes each foreground child (< 90 % of the page, >= 90 % on it - the
  table/product composite) uniformly by min(sx, sy), same centre-x and bottom edge. Backdrop children keep the stretch.
  Recorded as a warning on the bg object.
- **Not done**: the spec's literal "fit central assets within 0.25H-0.75H" band - with tiling fixed and the composite kept
  in proportion, the four live boards had no overlap, so no band clamp was added.
- **Verified live** (CorelDRAW 27, the user's DARSHAN masters, outputs in a scratch dir, DB untouched): 11x6 ft
  (landscape master), 5x5 ft, 6x6 ft, 60x75 in (portrait master) - 4 boards in 62.7 s, every render viewed: one set of
  logos, box on an unstretched table, no overlaps, each board's own English + Tamil name. Tests: `test_shop_name_binding.py`
  (22), `test_dual_master.py` routing cases updated, frontend importer/payload tests (+3). Backend 817, frontend 212.

### Excel / CSV shop import (Automation page, section 3)

"Import Excel (.xlsx / .csv)" under the Shops header (the sample-template button was removed on request). Parsing is
client-side with SheetJS (`xlsx` 0.20.3 from the vendor tarball `cdn.sheetjs.com` - the npm copy is 0.18.5 with known
advisories; lazy-loaded, ~500 KB chunk). `src/utils/shopImport.js` reads ONLY shop name, width and height from ANY layout
(phone/GST/address columns are ignored): sheets are read as arrays of rows; the header row is the first of the top 10
rows with a recognisable header (titles above it are skipped) and a sheet with no header at all is treated as data.
- **Name**: headers matching shop|store|name|client|outlet|particulars|dealer, preferring shop/store > client/outlet/
  dealer/particulars > bare "name"; never phone/GST/address/S.No or size/width/height headers ("Store Size"). Fallback:
  the first mostly-text column that is not a size or phone column.
- **Size**: a combined column (header size|dimension|board|measurement|recce whose values parse) split by a regex that,
  unlike the one in the request, also allows a unit between the number and the separator - the plain
  `(\d+)\s*[*xX-]\s*(\d+)` cannot match "10ft x 4ft" or "12' * 4'" - so "120 * 48", "10 x 4", "30X40", "10.5 x 4",
  "10 by 4", "10ft x 4ft", "12' * 4'" all work. Otherwise separate columns (width|^w$|breadth, height|^h$|length). Failing
  both, the column whose values look like sizes is used.
- **Unit**: ft / feet / ' (in the cell or the header, e.g. "Size (ft)") -> ft; otherwise in. cm/mm/in words are also
  honoured; a unit on one side of "10 x 4 ft" applies to both. Width and height keep separate units (the UI has one each).
Good rows are POSTed to `POST /api/v2/jobs/{id}/shops/batch` (job-scoped; per-row validation shared with single add;
<= 500 rows; bad batch-level master ids reject the whole request) and appear as ordinary shops; the page says
"Successfully imported X shops from <file>" and lists skipped rows with their spreadsheet row numbers. Not built:
editing a row before adding (the table was never editable). Tests: `test_shops_batch.py` (5) and `shopImport.test.mjs`
(13; 134 frontend total) including real .xlsx/.csv files for a single-column "10*4" size, separate Width/Height and custom
client headers; backend 634 passed; `vite build` OK. No browser click-through (no Playwright).

**Inline-editable Shops table.** Every row of section 3 (imported or added by hand) is now a set of live inputs -
name, width, width unit, height, height unit, phone, GST, address (units offer in/ft/cm/mm, a superset of the in/ft asked
for, because an import can yield cm/mm) - plus a Remove button; inputs are disabled while a shop is queued/converting or
done. Field names stay snake_case (`width_unit`), matching the API. Text fields save on blur and units on change via the
new `PATCH /api/v2/shops/{id}` (validated like an add, only the named fields change, blank phone/GST/address clear the
field, 409 while converting); `DELETE /api/v2/shops/{id}` removes the row and its editor/export rows (files on disk are left).
Convert (single row, or the new "Convert all (N)" button for every new/failed row) sends the row's CURRENT values in the
`POST /api/v2/shops/{id}/convert` body (`shopPayload` in `utils/shopPayload.js`), which the server saves before queuing, so
an edit whose blur-save had not landed yet is still what gets converted; an invalid edit (e.g. emptied width) is a 400 shown
under the table and nothing starts. Tests: `test_shops_edit.py` (6; 640 backend total) - including import "10x4", convert
carrying width 12 -> converted at 12 - and `shopPayload` tests (136 frontend total); `vite build` OK. The actual typing in the
browser (retyping 10 to 12 in the input) was NOT driven - no Playwright/jsdom here - so that step is covered by the payload
helper and the API test, not by clicking.

**Import is browser-only (draft rows).** "Import failed: Not Found" came from the import calling
`POST /api/v2/jobs/{id}/shops/batch` - a route that a backend started before it was added does not have (restart the
server after pulling). Import no longer makes ANY request: `importFile` parses with SheetJS and appends local draft rows
(`toDraftRow`, ids `draft-...`, status `new`) to the `shops` state; they render as the same editable inputs as saved rows
(name, width + unit, height + unit, phone, GST, address, Remove). Editing or removing a draft is local; a draft is saved
(`POST /api/v2/jobs/{id}/shops` with its CURRENT values and the master ids) when it is converted - single Convert or "Convert
all", which runs sequentially so seq numbers do not race - and then converted under its real id. The batch endpoint and its
tests remain in the backend, unused by the UI. Row numbers are now the table position. Tests: draft-row and "importFile makes
no network request" guard in `shopPayload.test.mjs` (138 frontend total); backend 640; `vite build` OK. Not driven in a
browser (no Playwright).

**Simplified Shops table.** Columns are now S.no | Shop name | Width | Height | Unit | Convert | Editor | trash icon (inline SVG, no
icon library in the project; grey, red on hover, disabled while converting). Phone/GST/Address are gone from the UI - table, manual
add row, import and payloads; the backend still accepts them (engine feature), so an API client can send them. Each row has ONE
`unit` (`in`/`ft`) for both dimensions; the UI sends only `{name, width, height, unit}` (`shopPayload`), and the server
(`_parse_shop_payload`, `_apply_shop_edits`) expands `unit` onto `width_unit`/`height_unit` (explicit per-dimension units still
win, DB columns unchanged). Import (`resolveUnit` in `shopImport.js`): no unit -> in; a unit on one side applies to both (so
"12'" next to a bare "48" gives 12 x 48 ft - ambiguous input, the ft rule wins); ft or in on both -> that unit; ft next to in, cm
or mm -> converted to inches (rounded to 0.01) so the numbers stay true - a deliberate refinement of "ft present means ft", which
would have turned "10ft x 48in" into 10 x 48 ft. Tests: 1 new backend (641 total), parser/payload tests updated and one added
(139 frontend); `vite build` clean. Not viewed in a browser.

**Full-bleed dashboard shell (App.jsx / Automation.jsx / styles.css).** The app is a fixed `100vh x 100vw` flex shell
(`.app-shell`, no page scroll): a pinned sidebar (256 px, 64 px collapsed, 300 ms width transition, never scrolls, a
ChevronLeft/Right toggle at the bottom, `data-tip` tooltips on the collapsed rail, state remembered in localStorage) and a
`.app-main` that alone scrolls. Automation is a brand bar (dropdown, "+ New Brand", "N Shops Loaded" / "Dual-Master Ready"
badges) over a 12-column grid `calc(100vh - 140px)` tall: Master Templates (span 5 - landscape and portrait slots stacked, each
with an UploadCloud dropzone + thumbnail + Required/Optional/Uploaded tag) and Shops Queue (span 7 - gradient "Import Excel",
"Add Shop" toggle for the manual row, "Convert All (N)", and the table in its own scroll region with a sticky header).
lucide-react icons: UploadCloud, FileSpreadsheet, Trash2, Play, ExternalLink, Plus, CheckCircle2, chevrons, Workflow, History.
**Deviation:** the spec's classes are Tailwind; the project has none and adding Tailwind's preflight would restyle the editor
and Recently-generated pages, so the same values (rounded-2xl cards, red-600 -> rose-600 gradient buttons, slate-50 canvas,
w-64/w-16) are written as plain CSS classes (`ws-*`, `app-*`, `btn-gradient`). The old `.shell`/`.sidebar`/`.content` CSS is
now unused. **Verified in a real browser (Edge via playwright-core, mock backend on a scratch data dir, 1440x800):** document
height = viewport (no page scroll); sidebar 256 -> 64 -> 256; main pane 1184 -> 1376 px and back; the table scrolls inside its
card (554 px visible of 724) and keeps its scroll position across the collapse; thumbnails still render after the toggle; the
collapsed tooltip shows; a 12-row import made 0 requests and rendered 12 editable rows; editing width 10 -> 12 in the input and
pressing Convert sent `POST /api/v2/jobs/../shops {name, width: 12, height: 4, unit: "in", landscape_master_id, portrait_master_id}`
then `/convert` with the same body; the trash icon removed a draft row; the Recently-generated page renders in the new shell.
One console 404 is the missing favicon (pre-existing). Not done: mobile/tablet layout beyond a single-column fallback under
1000 px.

**Data-grid table + batch loader.** The Shops table is a borderless grid (rounded, hairline-bordered container, slate header,
row hover, cells that are transparent inputs revealing a border on hover and a red ring on focus; the manual-add row keeps
visible borders). The Convert column shows status badges - Completed (emerald, check icon), Processing NN% (amber, pulsing;
the eased `useSteppedProgress` value), Queued (slate), Failed (red + Retry). Plain CSS again (no Tailwind), same values.
"Convert All (N)" (N = shops that are new/failed, not `shops.length`, so finished shops are not counted for re-conversion) now
sits in the card footer; while a batch runs it is replaced by a banner: SVG ring with the percentage, "Converting i of n -
Estimated time remaining: ~Ns" and a linear bar. State: `isBatchConverting`, `batch`, and derived `batchProgress` /
`estimatedTimeRemaining` / `currentShopIndex` from `utils/batchStats.js` (unit-tested): progress = (finished shops + each
in-flight shop's backend %) / total; remaining = average seconds per finished shop x shops left, or, before the first shop
finishes, extrapolated from the overall fraction (shows "calculating..." until > 5%). Drafts are saved and queued one by one,
the server converts them sequentially, the batch ends when every shop is done/failed and a one-line summary replaces the
banner. `convertShop` now returns the real shop id (null if it did not start). Browser check (Edge, mock backend; the
status endpoint was intercepted to make each conversion last 3 s, because the mock engine finishes instantly): the button
disappeared on click, the ring/bar went 0 -> 93% smoothly, text and estimate updated ("Converting 2 of 4 ~12s"), badges went
Queued -> Processing -> Completed, and the button/summary returned at the end. Input hover/focus colours verified via computed
styles (a hover-vs-focus specificity bug that hid the red focus ring was found and fixed). The estimate is only as good as the
backend's step percentages and the per-shop time; it is not a promise.

**Batch banner: monotonic % and a moving-average ETA (supersedes the ETA formula above).** Root cause of the backwards jumps was the
SERVER: in a batched CorelDRAW session the shops share one heartbeat, and `/status` only read it for the shop's own index - so the moment
the worker moved on to shop i+1 (before `on_progress` stored shop i as done), shop i fell back to its placeholder step "starting" = 5 %
after showing 97 %; a batch member that failed and was retried alone also restarted at 5 %. Now a converting shop whose heartbeat index
is behind the file's reports step "saving", 99 %, and `_progress_peak` makes a conversion's `progress_pct` never drop (cleared when the
shop leaves "converting"). Page (`utils/batchStats.js`, tested): weighted progress (done/failed = 100, converting = its %, queued = 0);
the banner shows `monotonicProgress(peak, raw)` - never below what it already showed in this batch, reset to 0 by Convert All; ETA =
moving average of the last `ETA_WINDOW` (5) completion intervals (`batch.finishTimes`, stamped by `recordFinishes` as shops settle) x
the work left INCLUDING in-flight % (the old elapsed/finished estimate grew every second a shop was busy, then dropped), extrapolated
from progress before the first finish, floored at 0; displayed through `smoothEta` (counts down on its own, moves 1/4 toward each new
estimate); text `fmtEta`: "~2m 15s remaining" / "~45s remaining" / "Finishing up..." / "Calculating...". No SSE/WebSocket was added:
the page already polls atomic per-shop rows (`/api/v2/shop-statuses`) and derives the batch state itself. Checked in Edge with the
status responses replayed for 7 shops of uneven length and one 97 -> 5 % blip: 125 samples, 0 backward steps, no negative ETA,
a steady countdown.

**Executive brand bar.** The top bar (`.ws-bar`) is a translucent, blurred, rounded card (sticky, hover border shift) with a
Building2 "BRAND" label, a native `<select>` restyled with `appearance: none` inside a `.ws-select` wrapper and a
ChevronDown overlay (`pointer-events: none`; red focus ring), a gradient "+ New Brand" button (lift on hover; it becomes an
input + Add/Cancel while adding) and, on the right, a "N Shops Loaded" pill (Store icon) and the master-status pill with a
pulsing emerald dot when both masters are uploaded (grey static dot otherwise). Plain CSS with the requested values (no
Tailwind). Checked in Edge: page still fits the viewport (grid bottom 769 of 800), chevron ignores pointer events, native
appearance is off, the dot animates, no console errors.

**Custom brand dropdown.** The brand `<select>` is replaced by `components/BrandSelect.jsx` (pill trigger with Building2 icon and a
ChevronDown that rotates when open; a floating blurred card with fade/zoom-in, items with red hover, the selected one bold with a
Check; a search box when there are 5+ brands). `hooks/useOnClickOutside.js` closes it on an outside press; Escape closes, Arrow
keys move, Enter picks. Verified in Edge with 6 brands: no native select in the bar, menu opens with the animation, search filters,
keyboard and mouse selection work, outside click and Escape close it, no console errors. The table's Unit dropdown (in/ft) is still
a native select - only the brand one was asked to change.

**Export dialog polish + "Download All (ZIP)".** `editor/ExportDialog.jsx` (the "Save and Generate" modal lives in the editor, not
Automation.jsx): header with an X icon button, a dark full-width "Download All (ZIP)" action, one row per generated file with a
Lucide type icon (FileCode CDR, FileText PDF, Image PNG/JPEG), the size/pixel metadata and a gradient Download button, a framed
preview, and a footer with "Export again" (RotateCw) and "Done" (CheckCircle2). **Deviation:** the zip is built by the server
(`GET /api/editor/{job}/{shop}/exports/{id}/zip`, members STORED, temp file removed after sending, named
`{ShopName}_Signage_Export.zip` with the shop name reduced to filename-safe characters, other scripts kept), not JSZip in the
browser - a CDR is often 100-300 MB and would have to sit in browser memory; "sequential downloads" would also trigger the
browser's multiple-download prompt. `handleDownloadAll()` just clicks a download link to that endpoint. Verified in Edge against the
mock engine: rows with icons, the icon close button, footer buttons, and Download All saved `Sri_Kumar_Stores_Signage_Export.zip`
containing the .cdr/.pdf/.png; 3 new backend tests (644 total). Not verified: the pixel-size metadata (the mock report has none;
real CorelDRAW exports do) and the requested "clean Tamil text on the preview banner" - the preview is CorelDRAW's own PNG, so
Tamil glyphs render as boxes when the font is missing on the machine, exactly the limitation documented earlier; the dialog cannot
fix that.

### CDR file version: every saved `.cdr` targets CorelDRAW 2019 (v21)

**Problem.** The server's CorelDRAW is now 27 (CorelDRAW 2025); a bare `doc.SaveAs(path, None)` writes the newest format, which
the designers' CorelDRAW 2019 (v21) refuses to open. **Fix** (`corel_util.save_cdr`): both places that write a `.cdr` -
`CorelEngine`'s `saveas` step and the editor export (`export_replay`, format `cdr`) - now save through
`app.CreateStructSaveAsOptions()` with `Version = 21` (`cdrFileVersion.cdrVersion21`) and `Overwrite = True`. The spec's
`CreateSaveOptions()` does not exist in the object model; the factory is `CreateStructSaveAsOptions` (confirmed in the generated
typelib, where `Version` is an int property of `StructSaveAsOptions`). `SIGNAGE_CDR_VERSION` overrides the target (`0` = whatever
the running CorelDRAW writes); a CorelDRAW older than the target saves as itself; a failure to build the options object raises
rather than falling back to a plain SaveAs (that would silently reintroduce the bug).

**How it was verified (live, CorelDRAW 27.0.121).** A fresh document saved both ways: default -> `content/root.dat` RIFF form type
`CDRU`, `META-INF/metadata.xml` `cdr:CoreVersion` 2700; `Version = 21` -> form type `CDRM`, `CoreVersion` 2100 (and a smaller styles
part). Those are exactly the markers of the designers' own files: all 77 `.cdr` in `signage_dataset/` (opened read-only as zips) are
`CDRM` with `CoreVersion` 2100 (one true CorelDRAW 2019.2 file: `AppVersion` 2120; the others 2700, i.e. saved as v21 from a newer
CorelDRAW). `AppVersion` records the writing application and stays 2700 - that is normal and is not the format. Then a real
"Shop 1" (120x48in) conversion through `CorelEngine` and a real editor export from a real dalmia board: both wrote
`CDRM` / `CoreVersion` 2100. Each save is now READ BACK (`corel_util.check_cdr_format` -> `report.cdr_format` =
`{form, version, requested}`) and a warning is added if the file's version is not the requested one (same verify-don't-trust pattern
as fonts). 7 new tests (`test_cdr_version.py`; fake COM document + zip fixtures); backend suite green.

**Not verified:** opening a produced file in an actual CorelDRAW 2019 - none is installed here. The evidence is that the produced
files carry the same format markers as files that do open there. Also note: an uploaded MASTER is never modified, and
PDF/PNG/JPEG are unaffected. Existing outputs generated earlier by CorelDRAW 27 are not converted retroactively - regenerate them.
Features newer than 2019 (if a master uses any) are dropped or flattened by CorelDRAW's own down-save; that is CorelDRAW's
behaviour, not something this code controls.

**Recently generated = asset console (`pages/RecentlyGenerated.jsx`).** Same full-viewport shell (the sidebar is pinned by the app shell, only
`.rg-scroll` scrolls). A blurred control bar holds a live search (`searchTerm`; `utils/recentFilter.js matchesSearch`: shop name, master
filename or any generated filename, case-insensitive), custom pill dropdowns for brand and status (new generic
`components/PillSelect.jsx`, same look/keyboard/outside-click behaviour as `BrandSelect`) and a Refresh button (icon spins while loading).
The grid: PREVIEW (56x36 thumb, click opens it, hover zoom) | BRAND | SHOP DETAILS (name + mono master filename, ellipsised) | DIMENSIONS
(pill, one unit shown when both match) | GENERATED | STATUS (the shared `.badge` styles) | DOWNLOAD ASSETS (segmented CDR/PDF/PNG-or-SVG/Report
group) | EDITOR (gradient button, done rows only), sticky header, an "N of M jobs" footer. The 3 s auto-refresh while something is queued or
converting is kept. Plain CSS (`rg-*`) with the requested values, not Tailwind. Verified in Edge (mock backend, 4 shops): 8 columns, no
page scroll, search by name / master filename, brand + status filters combine ("1 of 4 jobs"), reset restores all; no console errors. 3 new
frontend tests (148 total). With the mock engine the preview is an SVG and there is no PDF - real CorelDRAW rows show CDR/PDF/PNG/Report.

**3D launch splash (`components/SplashScreen.jsx`, `App.jsx`).** `three` 0.169 + `@react-three/fiber` 8 + `@react-three/drei` 9 +
`framer-motion` 11 (fiber 8 / drei 9 because the app is React 18; `@types/three` skipped - the project is plain JS). The screen shows a
slowly sweeping, floating signboard (drei `RoundedBox` metallic frame, dark bezel, emissive face painted on a 2D canvas so no font is
fetched, two posts), drei `Stars` + two `Sparkles` fields, key/rim spot lights on a fogged dark background, pointer-tracking camera
parallax (lerp), the gradient title "SIGNAGE AUTOMATION PLATFORM", the subtitle and the "Start Automation" button (shimmer bar on hover).
Click or Enter: `leaving` makes the camera dive at the board and the overlay fade, then `onStart` after 450 ms and the parent's
`AnimatePresence` runs the 0.5 s opacity/scale exit. `Shell` holds `showSplash`, initialised to `true` with NO storage check, so the
splash appears on every load (refresh, hard reload, deep link such as `/recent`); leftover flags from the earlier once-per-session version
(`signage.splashSeen`, `splashSeen`, `hasSeenSplash`) are removed at start-up. A "Welcome screen" sidebar button (`onOpenSplash`)
re-opens it without a refresh; the editor tab route has none. Verified in Edge: with stale flags seeded, first load, reload, cache-disabled
reload (Ctrl+Shift+R equivalent) and a `/recent` deep link all show the splash first, and Enter/Start then reveals the workspace. The chunk is `React.lazy`-loaded (~943 KB, 262 KB gzip - the main bundle stays ~300 KB) and Vite prints its
usual chunk-size warning for it. Fallbacks: no WebGL or a canvas error -> the same overlay on a CSS gradient; `prefers-reduced-motion`
-> no sweep/float/drift, fewer stars, no zoom delay. Verified in Edge (SwiftShader WebGL): canvas 1440x800 renders the board and
particles, hover scales/shines the button, click removes the splash and shows the dashboard, reload does not show it again, the sidebar
button and Enter re-trigger/dismiss it, no page errors (the only console noise: React Router v7 future-flag warnings and the pre-existing
favicon 404). Real GPU frame rate was not measured.

**Splash = Adinn identity + fly-through.** The board face now carries the real Adinn logo: `assets/logo.jpeg` (repo root) was copied to
`frontend/src/assets/logo.jpeg` and imported (`useTexture`, sRGB, `LinearFilter` without mipmaps, max anisotropy). The JPEG is a 1600x1440
square with wide white margins, so the UV window is cropped to the logo's own box (repeat 0.85 x 0.46, offset 0.075 / 0.30 -> ~2.05:1,
matching the 4.2 x 2.1 face plane) - unstretched; the face is emissive-white so the panel glows like a lit sign. Palette: background
`#0D0D0D`, `#E31E24` red accent bezel, dark anodized frame `#1A1A1E` (metalness 0.8, roughness 0.2), a red rim spot light BEHIND the
board plus a red point light for the halo, red/white particles, white title/subtitle with a red underline and glow, red gradient button
(the earlier amber is gone). Launch: a global `keydown` for Enter or Space (`preventDefault`, guarded so a key + click can't double-start),
the button, or a click sets `isZooming`; the board squares up to face the camera and the camera eases (exponential, frame-rate independent
equivalent of `lerp(z, target, 0.08)`) from z 7.2 to 0.35 at the board centre - not 0.1: that is behind the face plane (z 0.125) - and when
z < 1.4 the screen fades (250 ms) and `onStart` runs; a 1.6 s timer guarantees `onStart` if frames stall, and without WebGL it starts
immediately. Verified in Edge with software WebGL: the logo renders sharp and correctly proportioned; a mid-flight frame shows the camera
inside the logo fading into the dashboard; Enter and Space both reach the workspace (1.2-1.7 s with SwiftShader, so faster on a GPU). The
splash chunk is now ~946 KB (263 KB gzip) plus the 65 KB logo.

**Splash metals (frame + poles).** Root cause of the "invisible" frame: a metallic material with nothing to reflect renders near-black (no
environment map), so lights alone could not fix it. The scene now has a procedural drei `Environment` built from `Lightformer` strips (white
top/sides, an Adinn-red strip behind) - no HDR download - plus the requested lights: front-low point light, white key spot, and a red
directional rim from behind. Light intensities were scaled up (45 / 140) from the spec's 2.5 / 3.0 because three r155+ uses physical
units where those values are invisible; the directional rim keeps 2.0. Frame: dark chrome `#2A2A32` (metalness 0.9, roughness 0.15,
envMapIntensity 1.5) over a slightly larger light-silver slab `#E0E0E6` that forms the outer rim; poles: radius 0.12 (was ~0.06-0.08), length 2.2
(was 1.0), `#33333E` metalness 0.85 / roughness 0.2, top tucked into the frame, placed at x = +/-1.7 so they clear the button. The face
material got roughness 0.95 and almost no env reflection so the new lights do not wash out the logo. Verified in Edge (software WebGL): silver
rim and chrome highlights on the frame, both poles clearly visible with red/white edge highlights, no console errors. The poles pass behind the
title and subtitle (long poles were asked for); the logo's black reads as dark grey under the emissive/tone-mapped face.

**Automation empty states + default brand.** On load the brand list is fetched and, if a brand named Adinn exists (case-insensitive), it is
selected once (`defaultBrandApplied`; a later choice is never overridden). The spec's `useState('Adinn')` was not used literally: it could
select a brand that does not exist in the list. Master Templates with no master uploaded now shows a full-width dashed hero dropzone
(`UploadDropzone hero`: gradient UploadCloud, "Upload Master CDR Templates", drag/drop or browse, real upload progress) with a
Landscape/Portrait toggle - the dropped file's orientation cannot be inferred client-side, so the user picks it - and a dual-master routing
tip; once either master exists the two-slot view returns. Shops Queue with no shops shows the matching emerald "Import Shop Details Sheet" hero
(click, Enter/Space or drop a sheet) with "Import Excel File", "Load Sample Data" (4 demo shops via `utils/sampleShops.js`, local drafts exactly
like an import; covers both orientations) and a sheet-format tip; both are disabled until a master is uploaded (drafts need a job to be saved on
convert). The hero returns when the last shop is removed; the import report and errors show above it. Verified in Edge (mock backend): Adinn
preselected, two heroes and two tips instead of the old plain text, hover border, upload through the hero, sample data -> 4 editable rows,
deleting them brings the hero back; no errors. 1 new frontend test (149 total).

**Empty states, trimmed.** The two instructional tip banners (dual-master routing under Master Templates, sheet format under Shops Queue) were
removed, and the Shops Queue header's "Import Excel" / "Add Shop" buttons are shown only when `shops.length > 0`; with no shops the only way in is
the centred empty-state card ("Import Excel File" / "Load Sample Data" / drop a sheet). Consequence: with an empty queue there is no manual
"Add Shop" until at least one shop exists. The hidden file input stays mounted so the card's button still works. Verified in Edge: no
tip text on load, header actions absent until an import, present after, gone again once every shop is removed; the card's button opens the file chooser.

**"Enter Manually" replaces "Load Sample Data".** The Shops Queue empty-state card's secondary button is now "+ Enter Manually" (outline style, red plus).
There is no manual-entry MODAL in the app - manual entry is the inline add-row of the shops table - so the button opens that row
(`setShowAddRow(true)`, which swaps the card for the table with the add row), name field auto-focused, Enter in it adds; the row gained a Cancel button
(back to the card when the queue is empty). Like the import button it is disabled until a master is uploaded. The sample-data loader, its
util and test were deleted as dead code (148 frontend tests). Verified in Edge: disabled before a master, opens the row focused on the name, adding
a shop works and brings back the header buttons, Cancel returns to the card.

**Empty-state heroes are full-card now.** The inner dashed/rounded box is gone from both cards: `.empty-hero` has no border, radius or own background, fills
its `ws-card-body` (`flex: 1; width/height 100%; min-height 350px`, body padding and gap zeroed via `:has(.empty-hero)`) and centres its content; hover,
drag-over and keyboard focus are shown as a soft red tint (plus an inset focus ring) instead of an outline. `UploadDropzone` in hero mode wraps in
`.hero-root` so the dropzone can grow; the Landscape/Portrait toggle sits above it, so the Master hero is the card minus that toggle (555 of 607 px in
Edge) while the Shops hero is exactly the card body (651 x 607). Disabled heroes (no master yet) show no hover tint.

**Master Templates = two side-by-side upload cards (tab switch removed).** The Landscape/Portrait toggle and the single hero dropzone are gone. The
card body is always two cards (`.mc-grid`, `auto-fit minmax(190px, 1fr)` so they stack when narrow): "Landscape Master - Drag & drop .cdr file (Width >= Height)"
and "Portrait Master - ... (Width < Height)", each a bordered click-or-drop card (`UploadDropzone card={{title, help, browse, tone}}`, real upload progress kept).
A filled card turns into the master's thumbnail plus a file badge (name, human size via `utils/fileSize.js fmtBytes`, X to remove). The upload callback now receives the file
name/size (`onUploaded(body, name, size)`). Removing a master clears that slot (a confirm appears only when it is the last master AND shops are listed - it then clears the list,
converted shops stay under Recently generated). Deliberate related changes: replacing/adding a master no longer clears the shop queue (saved shops carry their own `job_id`,
drafts attach to whichever master exists at convert time), and "Open" in the editor now uses `shop.job_id`. Uploads are disabled until a brand is chosen. Removing a master
only clears it in the UI - the uploaded file stays on the server (no job-delete endpoint exists). Verified in Edge: no tab bar, both cards visible side by side, filling one leaves the
other empty, badges show `land.cdr 1.2 KB` / `port.cdr 1.1 KB`, the header badge goes Dual-Master Ready -> Portrait master only after a remove; 1 new test (149).

**A master change resets the Shops queue (supersedes "replacing/adding a master no longer clears the shop queue" above).** Uploading,
replacing (= remove + upload in this UI) or removing a master calls `resetShopsQueueStatus` (`pages/Automation.jsx`): every row that was
saved to the server - done, converting, queued or failed, all tied to the OLD master's job - becomes a fresh draft with the same name,
size and unit (`utils/shopPayload.js resetForNewMaster`, unit-tested), so its next Convert creates a NEW shop on the new master instead of
re-running the old one (a plain status flip would have re-converted against the old master). Untouched drafts stay. Status pollers are
cleared, batch tracking and the import/batch messages are reset, and `masterGen` stops an in-flight convert or Convert All at its next
step (no convert request after a reset; a draft already POSTed at that moment stays on the server as an unconverted shop). The old
shops are not deleted - their boards stay under Recently generated, and a conversion the server already started finishes there. A
dismissible notice says what happened ("Portrait master added - 4 shops reset to Convert..."). Verified in Edge against a mock-engine
backend on a scratch data dir: 4 x Completed/Open -> 4 x Convert and "Convert All (4)" after adding a master; a row pinned at
"Processing" reset on remove with 0 status polls afterwards; the next convert saved the shop on the new landscape master's job.

**Splash poles + text layout.** Poles are now 6.0 long (top tucked into the frame, centre y -4.2 in the board group) so they run off the bottom of the viewport
instead of stopping mid-air. Because a pole that reaches the bottom edge necessarily passes the text lines (they sit 238 px either side of centre, the title is
~1080 px wide), a dark gradient scrim (`.splash-scrim`, bottom 58%, 0.97 -> 0 opacity) sits between the canvas and the text: the poles sink into shadow and the
title, subtitle and button read cleanly; the text block is anchored in the lower third (padding-bottom 5vh, slightly tighter gaps). The board was NOT moved
up: at the current camera it already sits ~50 px from the top edge, so more height would clip its top. The optional `ContactShadows` at y -3.5 was skipped - the
poles now leave the screen, so a ground contact would be off-screen. The poles remain faintly visible behind the title (by design of the fade). Verified in Edge with
software WebGL; no console errors.

**3D editor loader (`components/EditorLoader.jsx`, used by `pages/EditorPage.jsx`).** The editor tab (opened from "Open in editor") shows an Adinn-themed WebGL loader
while it initialises: floating badge (chrome torus, `metalness 0.9 / roughness 0.1`, a counter-rotating glowing `#E31E24` ring, a wireframe red core with a bright heart, sine-wave bobbing
and X/Y rotation), red sparkles + stars on `#0D0D0D`, and a HUD - "ADINN AUTOMATION EDITOR" with a pulsing red dot, three status messages cycling every 1.2 s, and a glass progress bar with a
red -> rose -> amber fill. **Deviation from the spec's "setTimeout 2200 ms":** it is driven by the REAL load state, not a fixed timer - `ready` is the scene actually loading, `realProgress`/`step`
are the server's CorelDRAW scene-build progress (shown as "rendering objects n/m" when building), the bar shows max(real, an eased estimate creeping to 90%) and jumps to 100% when ready, and the
loader stays at least `MIN_SHOW_MS` = 1.8 s so a cached scene does not flash it, then fades (250 ms) and hands over. Errors (503 low RAM, 409 not converted) still use the old 2D card with Retry;
a retry shows the loader again. The chunk is lazy (`React.lazy`), sharing the three.js chunk with the launch splash; no WebGL -> HUD only. Bug found while testing: the first version's "finish"
timers lived in an effect keyed on the progress value, so the next progress tick cleared them and the loader hung at 100% - the finish is now a one-shot flag. Verified in Edge by clicking
"Open in editor" on the Recently generated page (a real new tab): canvas rendered, progress 19 -> 84 -> 100 %, messages cycled, editor appeared after ~2.2 s, no errors.

**Launch-screen readiness check (`GET /api/corel/health`, `utils/corelHealth.js`, `SplashScreen.jsx`).** The splash checks the
server before letting anyone in: "Checking CorelDRAW installation on this machine..." (button disabled), then either
"CorelDRAW 27.0 detected" (+ the fallback version, + a low-RAM warning when free RAM is under the 1.5 GB batch floor) and an
automatic fly-in after 1.4 s (Enter/click start at once), or an error with a "Retry Connection" button (Enter retries). There is
no long-running CorelDRAW to "connect" to - each job launches its own hidden instance - so the endpoint checks what a job would
use WITHOUT starting CorelDRAW (a test launch costs seconds and RAM and would race a running conversion): the engine
(`SIGNAGE_ENGINE`; mock is always ready), the installs from `corel_util.corel_installs()` (registry + each executable's version
resource, in the order a job tries them), free RAM. States: ok / error (no usable install) / offline (the server did not answer
within 8 s). The request uses the proxied relative URL, not a hardcoded host. A small "Continue without CorelDRAW" link on the
error states is a deliberate addition so a false negative cannot lock people out (Recently generated works without CorelDRAW).
Tests: `test_corel_health.py` (5, incl. that it never dispatches CorelDRAW), `corelHealth.test.mjs` (6). Verified in Edge on the
production build against a fresh backend: real check -> "CorelDRAW 27.0 detected / Fallback ... 21.2" -> auto-entered the app;
simulated "no install" -> error, Enter did not enter, the link did; server refusing connections -> offline, Retry -> ready.
**Update: the flow is now click-driven, nothing automatic.** On load the splash only shows "Connect to CorelDRAW" - no request
is made (verified: 0 health requests in 3 s). Clicking it runs the check ("Connecting to local CorelDRAW engine...", button
disabled); success shows "CorelDRAW 27.0 detected" + a "Start Automation" button and waits (no auto-start - verified still on
the splash after 4 s); failure shows the reason, "Retry Connection" and the "Continue without CorelDRAW" link. Enter/Space press
whichever button is showing. Starting (or continuing without CorelDRAW) navigates to "/" - the Automation view - even when the
splash was opened over another page (e.g. a /recent deep link, or the sidebar "Welcome screen" button on Recently generated).
**Update: 3D billboard + enterprise controls (current; supersedes the flat "enterprise redesign" that briefly replaced the 3D
scene, and the arcade-style 3D splash before it).** `SplashScreen.jsx` was REBUILT from this file's descriptions - the flat redesign had
overwritten it and it was never committed (untracked), so no copy existed in git, editor history or dist. The billboard: light-silver
outer rim + dark chrome frame (drei `RoundedBox`, metalness 0.9) over a matte, evenly self-lit logo face (UV crop repeat 0.85 x 0.46,
offset 0.075 / 0.30), two 6-unit poles, reflections from a neutral procedural `Environment` (white/grey `Lightformer`s), a white key spot,
a cool fill and a grey back light - the red rim/halo lights, red `Sparkles` and the fly-into-the-logo launch were dropped as "game-like";
a sparse, slow `Stars` field stays. Slow sway/float and pointer parallax (off with reduced motion). Layout: the canvas lives in a flex
"stage" above the panel (`.sp3-stage`), so a taller panel (error steps) shrinks the board instead of overlapping it, and the camera
distance follows the stage width (`fitDistance`) so the whole board fits on a phone. Controls (`sp3-*`): title in sentence case, small
uppercase subtitle, one glassy panel; tactile red buttons (bevel highlight, darker lip, 1 px press) "Connect to CorelDRAW" (power icon)
-> spinner line "Connecting to local CorelDRAW engine..." -> a raised badge "CorelDRAW v27.0 Connected / Fallback: v21.2 · Memory nominal"
+ "Start Automation"; failure: callout with remediation steps, "Retry Connection" and an underlined "Continue without CorelDRAW".
No "press Enter" copy (Enter still presses the primary button). Start: camera push-in + 0.4 s fade, then "/" and `onStart`. Splash chunk
back to ~1 MB (three.js), still lazy. Verified in Edge with software WebGL on the production build: 0 requests on load, every state,
no auto-start, Enter -> "/", error layout without overlap, 390 px phone fits, no page errors (only the pre-existing favicon 404).

**3D city backdrop (current; supersedes the single billboard + neutral studio lighting above - the title, the one Connect button
and the connection modal are unchanged).** `components/CityScene.jsx`: a procedural low-poly night street, no model files. Ground
`#0A0A0C`, buildings `#121218` as ONE instanced mesh with a generated window texture (a few red windows), a four-lane road (dashed
lanes, centre line, street lamps with warm pools), the main Adinn billboard (the old chrome frame, scale 1.8, on poles from the
pavement, with a red light line), two roadside hoardings and two building-mounted LED boxes with the logo, red neon borders and a red
glow, 12 cars both ways (headlight pools, red taillight trails; position = a function of the clock, wrapped at +/-75), and an
elevated metro (deck, rails, pillars, red edge line) with a 4-car train on each track every 22 s. The camera stands back far enough
for the main board to fit the canvas width (`baseDistance`, also on phones), drifts slowly and follows the pointer. **Start**
(button, Enter or Space) closes the modal, folds the title block away so the canvas fills the screen, and flies the camera along a
Catmull-Rom curve down over the traffic and into the board face (`FLIGHT_S` = 2.4 s); the scene calls `onArrive` at 92 %, then the
screen fades (0.32 s) and the workspace opens; a timer finishes anyway after 3.9 s. Reduced motion: nothing moves and Start enters at
once. Checked in Edge (software WebGL, 1440x900 and 390x844): all elements render, the fade begins 2.3-2.4 s after Space (triggered by
the scene, not the timer), reduced motion enters immediately, no page errors (the only console line is the old favicon 404). Frame
rate was 8-9 fps at 1440x900 and 28 fps at 390x844 in SOFTWARE rendering; a real GPU was not measured, so "no frame drops" is not
verified. Chunk ~1.02 MB (289 KB gzip), +12 KB. No bloom (no postprocessing package); the glow is additive halo planes.
**Update: fidelity pass (bloom, lit signs, real vehicles).** New dependency `@react-three/postprocessing` 2.19.1 (the last line for
React 18 / fiber 8; pulls `postprocessing` 6.39). `EffectComposer` (multisampling 4; the canvas' own antialias is now off) renders
WITHOUT tone mapping, `Bloom` (mipmap blur) takes everything above `BLOOM_THRESHOLD` = 1.15, `Vignette`, then `ToneMapping` ACES last -
so only HDR colours bloom: neon borders, head/tail lights, the train stripe (`NEON`/`HEAD`/`TAIL`, values 5-7). Sign faces (main board
included) are unlit basic materials at 1.08 - just UNDER the threshold: a first try at 1.3 plus the old lit face washed the logo's black
text to grey. Signs: `Sign` (roadside pole or wall lightbox with brackets) on 2 roadside poles and 4 building facades (`FACADES`,
buildings placed for them, no random building over them; the two under the metro sit low and towards their facade's outer edge because
the deck cut their tops). Cars: extruded side profiles (sedan / van) with bevels, wheel arches, a B-pillar, protruding dark glass,
merged tyres + chrome rims, HDR lamps, a headlight pool faded forwards AND sideways (`beam` texture - the first pools had hard
rectangular sides) and taillight trails; 6 meshes per car. Trains: extruded streamlined nose car (front and turned-round rear) + two
middle cars, windscreen, window band (emissive 0.72 - at 1.15 it bloomed into a flat strip), doors, roof unit, bogies with wheels, red
HDR stripe; merged per part. Environment: four light strips (white, warm, red, cool) reflect in the clear-coated paint, the train shell
and a glossier road; lamp pools are radial now. The metro deck moved to y 8.4 and the look point up to 5.6 so the train runs fully in
frame. Checked in Edge at 2x (crops of road and metro): shapes, glass, wheels, lamps and trails read as vehicles, the train shows
nose/tail, all 7 Adinn signs are visible, flight + fade still ~2.5 s, no 3D warnings. Chunk 1.11 MB (310 KB gzip, +85 KB). Frame rate in
software rendering dropped to ~4.6 fps at 1440x900 (was 8.6; phone size 14.5) - real-GPU smoothness is still unmeasured.
**Update: the Connect button is a glass HUD panel (`ConnectControl` in `SplashScreen.jsx`, `.sp3-hud*` in `styles.css`; plain CSS
with the spec's Tailwind values - the project has no Tailwind).** A translucent slate panel (75 % #020617, 24 px backdrop blur,
slate border, red outer glow layer, four glowing red corner brackets) holds a status bar (pinging red dot + "Engine node", a CPU
icon + the host the page was opened from - the spec's "LOCAL HOST : 8000" was not used: the page reaches the backend through the
dev proxy and does not know its port), the button (dark red glass gradient, neon red edge, mono uppercase "Initialize CorelDRAW
connection" / "Establishing handshake...", pulsing power icon, radio icon, shimmer sweep and bright red fill + glow + 1.02 scale on
hover) and a "Press [Enter] or click to connect" hint. Same behaviour as before: it opens the connection modal and is disabled while
the modal is open; it takes focus on load, so its focus ring was made a faint 1 px outer line (2 px doubled the neon edge). Under
420 px the button's gaps/tracking shrink so the label stays on one line. Reduced motion stops the ping/pulse/shimmer/scale. Checked in
Edge at 1440 and 390 px (idle, hover, Enter opens the modal, no horizontal scroll, no page errors).

**Connection HUD (supersedes the plug -> socket animation, which was removed on request - `Plug3DAnimation.jsx` deleted).** The top of
the splash panel is `StatusHud` (in `SplashScreen.jsx`), CSS 3D - no second WebGL context: two tilted halo rings (conic arc masked to an
annulus, `rotateX`/`rotateY` + spin) orbit a glass core - slow slate when idle, fast with a red accent arc while checking; on success three
emerald pulse waves radiate from a shield core and the details appear as small industrial chips (`describeHealth().badges`: CorelDRAW
27.0 / Fallback 21.2 / Memory Nominal - amber when low); on failure a red core and a high-contrast status pill (`.pill`: "CorelDRAW not
found" / "Server unreachable") above the remediation callout. Button copy: "Connect to CorelDRAW" -> "Establishing COM Bridge..."
(disabled, spinner; the user's chosen wording - the check itself only reads the registry, which the helper line under the HUD says:
"Scanning the server for installed CorelDRAW COM registrations...") -> "Start Automation →". The requested "Local Session Re-use Active"
line was not used (a single conversion gets a fresh CorelDRAW); the chips show real values instead. Reduced motion: rings/waves static.
Verified in Edge on the user's own dev server (:5173 + backend :8000): every state, Enter -> "/", error pill, 390 px phone, one canvas.

**Connection modal (current).** The splash page itself is now static: billboard, title and ONE button, "Connect to CorelDRAW" (`.sp3-cta`)
- no status on the page, so the 3D layout never changes size (verified: the title stays at the same y through every state). The button
opens a centred glass modal (`.sp3-backdrop` with a 12 px backdrop blur + `.sp3-modal`, framer-motion fade/scale) that runs the check
and holds every state, with the `StatusHud` at its top: checking - "Connecting to CorelDRAW / Establishing connection with local
CorelDRAW COM engine..."; success - "CorelDRAW Connected Successfully / Version 27.0 detected • Engine Active", the other chips
(Fallback, Memory) and "Start Automation ->"; failure - "Connection Failed", the pill, the remediation callout, "Retry Connection" and
"Continue without CorelDRAW". The X or Esc closes it and discards a check still in flight (the late result is ignored); focus returns to
the Connect button. Enter: opens + connects, then starts / retries. Verified in Edge on the user's dev server: page has one button and
no status elements, checking/ok/error modals, Esc (also mid-check), Enter -> "/", "Continue" -> "/", 390 px phone without h-scroll.

**Modal visual = `components/ConnectionVisual.jsx` (replaces `StatusHud`, removed).** checking - "radar handshake": dashed outer ring and a
red-arc inner ring counter-rotating, a 360 deg conic radar sweep, two scan rings pulsing outward, three signal nodes pinging on the orbit,
a breathing chip (`Cpu`) core, and a ticker cycling the check's REAL steps every 0.9 s ("Scanning COM registrations..." / "Locating
CorelDRAW executables..." / "Checking free memory..." - the requested "Scanning COM Ports" / "Authenticating PID" describe things the
check does not do); body text "Connecting to CorelDRAW / Establishing local COM bridge pipeline..." (user copy). ok - "lock-in": six
emerald nodes converge from the orbit (0.5 s), the core springs in (framer-motion spring), a checkmark draws itself (`pathLength`), an
emerald aura settles and two ripple rings keep expanding. error/offline - a red core with a short shake + the status pill. Reduced motion
(`useReducedMotion`): no loops or particles, final frames only. Verified in Edge on the user's dev server: scan frames over 2 s with the
ticker advancing, the success sequence (convergence -> spring + partial check -> full check with aura/ripples), the offline error.

**Update: industrial vector-engine loader.** Checking now shows a slowly rotating CAD precision ring (SVG, 60 ticks, every 5th longer),
two counter-rotating rings with METALLIC conic-gradient borders (silver/slate with a red and an amber glint) on a plane tilted 58 deg in 3D
with a red drop-shadow glow, a faint sweep, the three satellite pings, and the chip core over a breathing red glow. Success is an emerald
SHIELD (SVG) that flips in on a spring (`rotateY` 90 -> 0) after the node convergence; its outline, then a checkmark, draw themselves.
Chips (`describeHealth().badges`, now also `.version`): "CorelDRAW v27.0 Active", "COM Registered", "Fallback v21.2", "Memory Nominal".
Copy: "Initializing Corel Engine Bridge / Scanning local COM registrations & CorelDRAW installs...", "Engine Handshake Failed" (error text
in mono), "Continue without CorelDRAW Engine". Not used as requested, because untrue: "COM Bridge: Online" (the check verifies the COM
registration, no bridge is running), "Scanning local COM ports & active sessions", "Dual-Master Active".

**Editor loader v2: real-time stages + 3D build narrative.** Section 1 of the request (dashboard cleanup, dual master cards, Enter Manually, full-card dropzones, splash poles/text) was
already done in earlier rounds - verified, not redone. `EditorPage` now tracks REAL load progress (`utils/loadStages.js`, unit-tested): **BUILD 0-35** = the scene request (server
CorelDRAW build progress, "rendering objects 110/138"); **TRANSFORM 35-75** = applying saved edits and preloading EVERY object image for real (per-image progress, failures count, 20 s cap);
**PAINT 75-100** = the workspace mounts underneath the loader and, two animation frames later (first paint), reports 100. Deviation: there is no shader compile or texture upload in the editor
(its canvas is SVG), so the status text says what really happens ("Painting the first frame...") instead of "Compiling shaders". `EditorLoader` takes `progress`/`statusText`; the displayed
bar eases toward the real value at <= 45 %/s so the three scenes are always watchable, and the loader stays mounted in one tree slot from the first request until the workspace is painted, then
fades (250 ms) and calls `onDone`. The 3D narrative follows the DISPLAYED value: **hammer 0-39** (chrome frame of four bars; a hammer swings down onto the top-right corner every 0.95 s, frame recoils,
burst of red additive sparks with gravity), **panel 40-79** (white flat face fades in with a red grid shader whose brightness ripples outward), **paint 80-100** (nozzle sweeps left to right with vertical
scanning, spraying red mist, revealing the Adinn logo behind it via a scaled plane + cropped texture). Bugs found and fixed while testing: the grid was invisible because a fresh `uniforms` literal
each render reset the values `useFrame` wrote (now memoised); sparks were square (now a soft round sprite). Verified in Edge with a simulated slow build (scene route answering 202 with
15/45/80 %): BUILD tracked "objects 20/138" then "110/138", TRANSFORM, PAINT, 100 %, editor appeared; frames of all three scenes viewed; red-pixel counts near the corner spike once per hammer cycle
(sparks). 6 new frontend tests (155 total).

**Editor loader v5: designer at a workstation (current; supersedes v4's CAD hub below - its visual only, the telemetry is kept).**
The loader's centrepiece is `components/DesignerWorkstation.jsx`, an SVG illustration on its own requestAnimationFrame clock: an
isometric desk with a glowing red mat, keyboard, mouse and a monitor (ambient red/cyan glow behind it) running a CorelDRAW-like window
("CorelDRAW · master.cdr" title bar, toolbox, dot-grid canvas, status bar), a designer seated at the right with a hand on the mouse. On
the canvas a small signboard in a dashed cyan selection box with 8 scale handles loops through sizes (wide -> larger -> portrait -> back,
5.2 s, eased with a short hold, always centred with centre guides while dragging); its layout re-flows to each size (logo + text side by
side when wide, stacked when tall); the cursor drags the highlighted corner and the physical mouse follows; floating "W: ... mm" /
"H: ... mm" markers and the status bar read the live size; an action pill cycles "Stretching to target size..." / "Scaling vector
nodes..." / "Auto-aligning layout...". Reduced motion: one still frame. Unchanged: the tag, the status line (EditorPage's real status,
fallbacks now "Initializing the editor workspace..." / "Loading master template artwork..." / "Loading vector object layers..." /
"Finalizing signage layout for the editor..."), the bar, the BUILD/TRANSFORM/PAINT footer (right label now "Auto-resize loop"), the
easing/fade/onDone plumbing. Not used as requested: "CorelDRAW 2026" (no such version), "COREL ENGINE V27" (this screen does not know
the version), "Designer initializing CorelDRAW COM engine" / "Executing vector node transformation" as progress text (untrue for a
cached board; the real status line is shown instead). Verified in Edge on the user's dev server with the scene request delayed: frames
at 0.6/1.8/3.0/4.2 s show the stretch/scale/portrait re-flow and the markers updating; desktop and 390 px without h-scroll; no errors.
**Update: no hand - aligned workstation + a mouse synced to the cursor (supersedes the hand-on-mouse version, removed on request).**
`DesignerWorkstation.jsx` draws only the workstation: desk, glowing mat and a dark keyboard laid out symmetrically on the monitor
stand's centre line (x = 190), the keyboard directly in front of the stand, and a glow-accented optical mouse on the right of the mat
(red scroll wheel with a glow, seam line, cyan side accent, a blurred red under-glow that brightens while the artwork is changing, and a
cable to the back of the desk). The mouse position is a LINEAR map of the on-screen cursor's travel over the loop (`MOUSE_HOME`,
`MOUSE_RANGE`, `CURSOR_SPAN` from the `KEYS` sizes; the cursor drags the bottom-right handle, so it spans CX + w/2, CY + h/2) - verified
by sampling both positions 12 times across the loop in the browser: correlation 1.0000 in x and y, mouse travel 26 units across its
area of the mat. The rAF clock clamp and `frameAt` normalisation (the negative-first-frame crash fix) are unchanged.

**Editor loader v4: CAD workspace hub (current; supersedes v3's signboard build/hoist/paint loop below).** `EditorLoader.jsx`
is now a CSS/SVG "vector workspace" hub: an outer coordinate ring (SVG, 72 ticks, degree labels at the quarters, 30 s rotation) with a
laser scan beam (masked conic sweep), a red-arc ring and a counter-rotating amber-arc ring, three stacked isometric grid planes
(`perspective rotateX(60deg) rotateZ(-45deg)`, breathing apart in Z, vector paths drawing on the top plane) and a metallic red chip
emblem with a pulsing glow; an "ADINN AUTOMATION EDITOR" tag at the top; a glowing red/amber progress bar with a shine, the percentage,
and the REAL load stage (BUILD / TRANSFORM / PAINT, done/active colours). The status line is EditorPage's own (`statusText`:
"Contacting the server...", "CorelDRAW is rendering objects n/m", "Loading object images n/m", "Painting the first frame...") and falls
back to `stageMessage(pct)` ("Initializing workspace pipeline..." / "Loading master scene data..." / "Loading vector object layers..." /
"Finalizing Workspace Canvas..."); it is keyed by stage so per-image updates do not restart its fade-in. Not used as requested, because
untrue for a cached board (no CorelDRAW involved): "Initializing COM Engine Pipeline", "Parsing Dual-Master Vector Layers", "COM PIPE
ACTIVE", "GPU ACCELERATED" (the footer says "SVG canvas"). Progress plumbing unchanged (MIN_RATE/CATCH_UP easing, finish at 97, 250 ms
fade, `onDone`). Verified in Edge on the user's dev server with the scene request delayed 3 s (in-page trace: "Contacting the server..."
2 % for 3 s -> images 17/117 -> 69/117 -> painting -> fade at 3.7 s -> editor at 3.97 s) and at 390 px. The previous loader's source was
kept only as a scratch copy (it was untracked).

**Editor loader v3: 2D SVG build narrative (supersedes the 3D scenes above).** `components/EditorLoader.jsx` no longer uses three.js.
With no card or viewport box (everything floats on the #0D0D0D background under a 600 px red radial glow; the ground line fades out at both ends), the title ("ADINN AUTOMATION EDITOR" + pulsing dot) sits above an inline-SVG scene (viewBox 320x180) whose geometry is computed from the
eased displayed progress: **BUILD 0-35** the steel frame is drawn along its perimeter (`strokeDashoffset`) with a spark burst at the welding
head, then two struts, then the #2D2D35 face; **HOIST 35-75** (the TRANSFORM load stage) two poles rise and the board is lifted from the ground
onto them on dashed crane cables (running dash + tension jitter), corner bolts turn red when locked; **PAINT 75-100** a nozzle sweeps left to right
and a `clipPath` reveals a white panel with the real Adinn logo (`src/assets/logo.jpeg`, the same file as the repo-root `assets/logo.jpeg`, drawn as an SVG `<image>` cropped by a nested `viewBox` to its measured content box 173,418-1457,920 px) above a red base stripe, with a glowing red wet-paint edge at the nozzle. Below: a pulsing stage headline, the server's real detail
line, a gradient bar and the percentage. Real-progress plumbing (`loadStages.js`, MAX_RATE easing, min show time, fade, `onDone`) unchanged.
Plain CSS `el-*` classes (no Tailwind, no Framer Motion); reduced motion stops the loops. Loader chunk 5.8 KB. Checked by screenshots of the
real component (esbuild harness) at 8/20/42/58/84/99 % and at 360 px wide; not re-driven through a live editor load.
**Update: the scene is decoupled from progress.** It loops Build -> Hoist -> Paint on its own clock (`animAt(elapsed)`: 2.8 s per stage,
each stage's motion completes in 2.3 s then holds, the scene fades out/in over 0.35 s at the cycle boundary; the same rAF that eases the bar
drives it, so the motion is continuous, not stepped by a setInterval) for as long as the loader is up. Only the bar, the percentage and the
single status line (server `statusText`, else "Loading master CDR vector assets..." / "Applying shop dimensions & layout rules..." /
"Initializing vector canvas engine..." by the REAL load stage) follow progress. Reduced motion shows the finished board, no loop. Verified with
progress pinned at 40 %: the scene went build (1.2 s) -> hoist (4.0 s) -> paint (6.6 s) -> fade (8.3 s) -> build again (9.6 s), bar stayed 40 %,
no console errors.

## Batch import (`backend/app/batch_import.py`)

`parse_shop_lines(text)` turns pasted designer-filename-style lines —
`"16 - 12 X 4 Feet - Nonlit - AL MADEENA POOJA STORE"` — into shop rows
(`name`, `width`, `height`, `unit`, `type`). Handles the real dataset's
messiness: multi-word free-text types (`"2 Nos Double Side GSB"`, and the
typo `"Nonlt"`, passed through unvalidated), shop names containing their own
`" - "` (e.g. a `"- Copy"` suffix) or commas — the name is everything after
the third `" - "`-delimited segment, not just the fourth. Verified against
all 76 real filenames in `signage_dataset/` (Agarpathi + dalmia), 0
failures. Exposed as `POST /api/parse-shops` (`{"text": "..."}` →
`{"shops": [...], "errors": [{"line", "reason"}, ...]}`) - filenames never
encode phone/GST/address, so parsing only ever produces
`name`/`width`/`height`/`unit`/`type`.

`POST /api/jobs`'s `shops` field accepts further optional per-shop keys
beyond what parsing produces - `shop_name_local`, `phone`, `gst`,
`address_lines` (list[str]) - see "Per-shop content replacement" above.
The frontend (`App.jsx`) collects these in a second row under each shop's
name/size fields and omits any left blank (never sends `""` for them - see
that section for why `""` and "omitted" mean different things to
`compute_layout`).

## Master preparation guide (`docs/master-preparation.md`)

A one-page, CorelDRAW-2019-exact guide for the design team: how to group
each logo/product image (Ctrl+G) and name objects (`bg`, `frame`,
`fixed_*`, `shopname`, `logo_*`) so a *new* master gets the reliable, tagged
path through `compute_layout` instead of the untagged heuristics. Points
back here (the "ungrouped logo" gap above) for *why* grouping matters, so
it doesn't read as an arbitrary rule.

## Remaining limitations (honest, as of the dalmia validation above)

- **Only 4/12 real dalmia boards match the designer's file within 2%**
  (6/12 within 5%; board 12 excluded from both as a known outlier).
  Same-size and mildly-resized boards are fine; wide-format tiling is the
  main gap (see "Validation: all 13 dalmia files") because the designer
  chooses *which* sub-elements to repeat, and how to reposition/rescale
  them, rather than duplicating everything non-text uniformly - a judgment
  call, not a geometric rule. **The data-driven per-brand tiling rule
  (`brand_rules/dalmia.json`) made the tiled boards' diffs *worse*, not
  better** - see "Per-brand tiling rule attempt" above; it's kept for its
  underlying data, not as a demonstrated improvement. **Agarpathi has not
  been validated this way yet** (explicitly held back - see below).
- **Even with real CorelDRAW work isolated in a worker subprocess (see
  "Process isolation"), individual jobs still occasionally fail with
  transient COM errors** (`RPC failed`, `Object is not connected to
  server`) under this machine's sustained memory pressure after a very
  long session - the full 13-file dalmia batch needed 4 `--resume` passes
  to get every board through. The *process* no longer hangs (the actual
  goal of that work), but a job can still fail and needs a retry; this
  looks like host-level resource exhaustion rather than a code bug -
  restarting the machine before a long batch is the practical mitigation. **Update:** the reuse failures ("Object is not connected to server") had a code cause, not memory: see "Performance: the ~15 s per CorelDRAW job was a bug".
- **Fonts missing on the host machine** render as tofu boxes in the PNG
  export/real CorelDRAW output — seen with the original (untouched) Tamil
  text in a real dalmia master during testing. Not a `CorelEngine` bug;
  verify required fonts are installed on whatever machine runs jobs. (Shop
  -name *replacement* text avoids this proactively — see above — but any
  other pre-existing text in the master is only as good as the fonts
  already on the machine that made it.)
- **Ungrouped multi-fragment logos** (see "Designer dataset analysis")
  shatter under per-shape scaling; no clustering/auto-grouping was
  implemented in `compute_layout` itself (`derive_brand_rules.py`'s
  clustering is used only to build the static `brand_rules/*.json` file
  offline, not applied generically at layout time).
  `docs/master-preparation.md` is the mitigation (group future masters
  properly) rather than a code fix.
- **Manual creative redesign for extreme aspect ratios** (repositioning
  content to be more compact, dropping elements that don't fit, or
  choosing which elements to repeat when tiling) isn't and can't fully be
  replicated by a geometric rule — `validate_all.py`/`analyze_designs.py`
  quantify the gap so it can be judged case by case, not eliminated.
- **Two rare hangs were found and fixed**: a relative-output-path bug that
  made CorelDRAW show an interactive save dialog (see "The Save Drawing
  hang"), and `win32com.client.Dispatch()` itself hanging with no PID to
  recover by (see "Process isolation"). The defence-in-depth hardening
  (timeouts, watchdog, orphan cleanup, worker-process isolation) stays in
  place regardless, because a *different* rare dialog or hang is still
  possible in production and hasn't been exhaustively ruled out.
- **Agarpathi is untested beyond the original manual spot-checks** earlier
  in this file (dataset analysis, one real master+small-target readiness
  check). Its master has a large embedded bitmap (100-350MB files) that
  makes COM operations much slower than dalmia's ~9MB files - do not run a
  full `validate_all.py agarpathi` batch without confirming timing/memory
  behavior on one file first, and only when explicitly asked to.

## Content check, geometric accuracy in mm, and threshold calibration (Steps 1-3)

Follow-up engine work (no UI touched) closing three gaps flagged earlier:
the visual score can't tell a right board from a wrong one, `validate_all.py`'s
diffs are only ever reported as % of page (not comparable in absolute terms
across board sizes), and `metrics_config.json`'s pass threshold was an
uncalibrated guess. Dalmia only - Agarpathi untouched, per instruction.

### Step 1: content check (`backend/app/content_check.py`)

Reads the generated file's text shapes back (from the same COM dump
`corel_worker.py` already produces - `entry["ours_dump"]`, see "Process
isolation") and compares them against whatever was **actually requested**,
returning `CONTENT_OK` / `CONTENT_FAIL` / `NOT_CHECKED` per field
(`shop_name`, `phone`, `gst`) and overall - `NOT_CHECKED` when nothing was
requested for that field, never a vacuous pass. Pure Python, no COM of its
own; 12 unit tests including the exact regression that motivated it -
`sensitivity_test.py`'s "wrong shop's board" scored 0.990 on the visual
metric (PASS); the same content now correctly comes back `CONTENT_FAIL`.

**What "expected" means, precisely** (this was a real design decision, not
obvious): `shop_name`'s expected value is whatever was passed as
`shop["name"]` at generation time - this checks that CorelEngine faithfully
wrote what it was told to write, NOT that our (English-only) name matches
the designer's own, possibly differently-scripted rendering of that shop's
name (dalmia's real shopname text is often a Tamil transliteration, e.g.
"NR திரேடர்ஸ்" for "NR Traders" - a separate, already-documented gap: no
`shop_name_local` is requested by `validate_all.py`). Comparing against the
designer's own text would make content check fail on almost every real
board for a reason that has nothing to do with correctness. `phone`/`gst`
have no such alternative - a real shop's phone/GST value only exists in the
designer's own file (filenames never encode it), so `validate_all.py` now
extracts those from each real file's cached dump
(`content_check.extract_contact_values`, reusing `layout.find_contact_ids`'s
label match) and requests exactly those values when regenerating, so the
check is meaningful rather than comparing against a guess.

Locating the shopname shape in the generated file is an **exact-match
existence check** (whitespace/case normalized, so a `CorelEngine._fit_text`
manual `\r` wrap still counts as a match), not a "which shape is the
shopname" lookup - there is no content-independent way to identify that
shape once its content has already been replaced (`layout.find_shopname_ids`
matches the untouched MASTER's *old* text, which is gone after replacement).
On a genuine mismatch `found` comes back `None`: the check confirms whether
the requested text exists anywhere, it does not guess which different shape
was "supposed to be" the shopname.

**Live result, all 13 dalmia boards (including the master-as-its-own-target
board): 13/13 `CONTENT_OK`.** Verified against real CorelDRAW, not just
synthetic tests - every regenerated board's shop name, and phone/GST where
the real file has one, read back exactly as requested (e.g. M Pandi's real
file has no GST line at all - regenerating it correctly leaves `gst`
`NOT_CHECKED` rather than failing or guessing). This is a narrower claim
than "the boards look right" - see "Validation: all 13 dalmia files" above
for the (unrelated, still-open) geometric/tiling gap; content correctness
and layout correctness are independent axes, which is the whole point of
splitting this into its own check.

Regenerating all 13 boards to get this live result surfaced a **process
-isolation limitation worth recording honestly**: under this machine's
memory pressure this session (free RAM oscillating 1.9-2.5GB for long
stretches), `corel_supervisor`'s pooled/recycled-instance path failed on
almost every job after the first *within the same worker-process batch* -
not just the "occasional transient COM error" the existing docs describe,
but a near-100% failure rate for jobs 2+ in one batch invocation across
three separate attempts. Splitting the remaining boards into **one
`validate_all.py --only <file> --resume` invocation per board** (a fresh
worker subprocess launched per board, never reusing a pooled instance)
reliably succeeded where the batched runs did not - every single board
succeeded as "job 1" of its own process. This is a workaround, not a fix to
`corel_util.py`'s pooling code (out of scope here); if batch validation runs
start failing this badly again, falling back to one-file-per-invocation is
the practical mitigation alongside the already-documented "restart the
machine." **Root cause found later** (not memory pressure): `CorelEngine.process` CoUninitialize()d the pooled proxy after every job and the orphan cleanup killed the in-use pooled instance - see "Performance: the ~15 s per CorelDRAW job was a bug".

`validate_all.py`'s markdown table gained a Content column
(`OK`/`FAIL (field,...)`/`NOT_CHECKED`); the HTML report
(`build_metrics_report.py`) gained a per-board expected/found breakdown, and
a `CONTENT_FAIL` now flips that board's overall PASS/FAIL badge regardless
of its visual score - the whole reason this exists.

### Step 2: geometric accuracy in millimetres (`metrics.geometric_accuracy`)

`cluster_compare`'s diffs were always %-of-target-page, which isn't
comparable across boards of different target sizes (30mm on a 300mm board
and 30mm on a 3000mm board report very differently). `geometric_accuracy`
reuses the exact same cluster matching (so a cluster gets both a % and an mm
figure from one matching pass) and reports, per matched cluster,
`position_error_mm` (centre-to-centre Euclidean distance) and
`size_error_mm` (Euclidean distance between (w, h) pairs), plus per-board
max/mean of each. It also reports `area_matched_pct[t]` for t in
{2, 5, 10}mm - the % of the real file's *total* non-bg/frame cluster area
belonging to a cluster matched within t mm on BOTH axes - deliberately
area-weighted (a misplaced full logo should count for more than a misplaced
accent mark; a plain per-cluster count would not distinguish them). 5 unit
tests, including one that specifically checks this is mm, not %-of-page.
Wired into `build_metrics_report.py` next to the visual similarity table,
plus a new summary table at the top of the report listing all 12 boards at
once (shop, target, visual combined, max position/size error mm, area
matched at each tolerance, content check) - anchored links jump to each
board's full card.

**All 12 dalmia boards**, live this session (the visual `combined` column
matches the numbers already recorded in "Wide-board panel sequence" above,
confirming today's regeneration/caching didn't regress anything):

| Board | Target | Tile | Visual | Max pos err (mm) | Max size err (mm) | Area matched @5mm | Content |
|---|---|---|---|---|---|---|---|
| 02 (180x48) | 4572x1219 | x,2 | 0.785 | 191.9 | 1524.0 | 16.0% | OK |
| 06 (180x60) | 4572x1524 | x,2 | 0.727 | 837.7 | 762.0 | 16.2% | OK |
| 03 (120x48) | 3048x1219 | none | 0.976 | 97.0 | 115.6 | 93.8% | OK |
| 05 (120x48) | 3048x1219 | none | 0.972 | 201.6 | 108.1 | 96.5% | OK |
| 09 (144x60) | 3658x1524 | none | 0.853 | 533.0 | 1073.0 | 57.9% | OK |
| 10 (144x60) | 3658x1524 | none | 0.870 | 115.7 | 153.4 | 62.3% | OK |
| 11 (216x48) | 5486x1219 | x,2 | 0.625 | 510.9 | 2438.4 | 0.0% | OK |
| 11 (240x60) | 6096x1524 | x,2 | 0.821 | 344.0 | 2286.0 | 16.1% | OK |
| 12 (120x60, outlier) | 3048x1524 | none | 0.663 | 241.0 | 211.7 | 1.0% | OK |
| 13 (144x60) x2 | 3658x1524 | none | 0.864 | 88.7 | 139.4 | 62.3% | OK |
| 14 (120x48) | 3048x1219 | none | 0.954 | 322.7 | 22.9 | 88.7% | OK |

The mm numbers tell a sharper story than the %-of-page numbers did: a
"good" board (03, 05, 14) has 88-97% of its content area matched within
5mm, while the four tiled/wide boards (02, 06, 11-216, 11-240) sit at
0-16% - the geometric gap documented since "Validation: all 13 dalmia
files" is concentrated almost entirely in tiling/wide-format boards, not
spread evenly across the set. Content check is a clean 12/12 OK, confirming
(again, independently of the geometric numbers) that the text-correctness
axis and the layout-correctness axis really are independent.

### Step 3: threshold calibration tooling (`tools/calibrate_threshold.py`, `tools/build_label_page.py`)

`metrics_config.json`'s `visual.pass_threshold` (0.75) has been an
uncalibrated guess since it was introduced - this builds the tooling to
replace the guess with a number backed by the user's own eye, **without
this script ever writing to metrics_config.json itself** (project rule of
engagement: never tune a threshold to raise a pass rate; a human decides
whether/how to apply a proposal).

- `build_label_page.py <brand>` generates a standalone, self-contained
  `dataset_analysis/metrics_report/<brand>/label.html` - open it directly in
  a browser (no server), click OK/NOT OK per board by eye against the two
  images, click "Download labels.json", save it as
  `dataset_analysis/labels/<brand>_labels.json`. Re-running the script after
  labels exist preloads them (reads that same file back in), so labelling
  can happen across more than one sitting.
- `calibrate_threshold.py <brand>` reads that labels file plus every
  candidate metric already computed for the report (`build_cards`, factored
  out of `build_metrics_report.py` so both tools share one source of the
  numbers - `want_images=False` skips the thumbnail work `calibrate` doesn't
  need). For each candidate metric it finds the threshold that best
  reproduces the user's OK/NOT_OK labels: every candidate is a midpoint
  between two sorted board values (plus "always pass" / "always fail"), and
  ties are broken toward the middle of the observed range so a proposal
  doesn't happen to sit exactly on one board's own number. Candidates:
  `visual_combined`, `geo_max_position_error_mm`, `geo_max_size_error_mm`,
  `geo_mean_position_error_mm`, and `geo_area_matched_within_{2,5,10}mm_pct`
  - plus a non-threshold reference row for the content check (already a
  hard OK/FAIL). Writes `dataset_analysis/calibration/<brand>/proposal.{json,md}`.
  10 unit tests on the search algorithm itself (perfect/imperfect
  separability, tie-breaking, "everything passes" as a valid answer),
  independent of any real board data.

### Step 3b: calibration run on real labels (10 OK / 2 NOT_OK) - full report in `docs/engine-report.md`

First pass came back all-OK (0 negative labels - every metric trivially
"succeeds," which is meaningless, not a good sign; reported honestly rather
than presented as a real result). Second pass had 10 OK / 2 NOT_OK
(AHMED TRADERS 240x60, TAMILNADU STEELS). No existing candidate beats 83%
(10/12) - AHMED 216x48 (labelled OK) scores *worse* than both NOT_OK boards
on visual/position/size/area, while AHMED 240x60 (labelled NOT_OK) scores
*better* than several OK boards, so no single threshold on any one metric
can separate them. Manually inspecting the labelled set found
`clusters.unmatched_ours` (how many of our clusters matched nothing in the
real file) separates 11/12 - added to `calibrate_threshold.py`'s candidate
list, with an explicit warning (in both the code and its rendered report)
that this rests on only 2 negative labels and is a lead, not a validated
rule. `metrics_config.json` is still untouched; the threshold search was
stopped here per instruction. Full table, the "what 12 labels can/cannot
show" discussion, and the GOOD-vs-NOT_OK safety check against Step 5 (0
violations on this labelled set - see below) are in `docs/engine-report.md`,
not duplicated here.

### Step 4: wide-board scale gap - leave-one-out on the panel_sequence size_table (negative result, no rule changed)

`tools/wide_board_loo.py` asks: given only 4 real wide dalmia boards, each
one's `panel_sequence` `size_table` row IS a direct real-render measurement
(see "Wide-board panel sequence" above) - if a board's own row were removed,
how well would `layout._interp_size_table`'s per-aspect interpolation have
predicted it from the other 3, and does any simple alternative do better?
Pure Python, no CorelDRAW, no new dumps - it re-derives nothing from a real
file that wasn't already measured and stored in `brand_rules/dalmia.json`.
Only `tamil_card` and `roof_graphic` have their own `size_table`;
`enlarged_badge_card` inherits `tamil_card`'s via `card_from` (see above),
so its accuracy is entirely downstream of `tamil_card`'s.

**Leave-one-out result (mm error on that board's own target page):**

| Board | Sequence | tamil_card h err | tamil_card w err | roof_graphic h err | roof_graphic w err |
|---|---|---|---|---|---|
| 06 (180x60) | seq3 | +94.5mm | -150.9mm | +70.1mm | -86.9mm |
| 02 (180x48) | seq3 | +33.2mm | +58.3mm | +24.4mm | +35.4mm |
| 11-240 (240x60) | seq3 | -99.1mm | -142.2mm | -73.2mm | -83.3mm |
| **11-216 (216x48)** | **seq4** | **+98.8mm** | **+318.2mm** | **+73.2mm** | **+181.1mm** |

The 3 `sequence_3` boards land in a 24-150mm error band - real, but
moderate. **11-216 is a different story**: it's the *only* sample above
`aspect_split` (4.25), so leaving it out removes every `sequence_4`
data point - `_interp_size_table` can then only clamp to the nearest
`sequence_3` point (4.0), never truly interpolate, and its width error
(181-318mm, roughly a quarter of the group's own real size) is 2-5x worse
than any `sequence_3` fold. This is exactly the "one sample, don't force a
fit" case flagged when `panel_sequence` was first built - leave-one-out
now gives it a number instead of a hunch.

`target_cy_frac` (vertical centring) is nearly constant across all 4
boards regardless of aspect (0.528-0.531 for `tamil_card`, 0.566-0.577 for
`roof_graphic`) - both the current per-aspect interpolation AND a tested
flat-average alternative land within a few mm either way, with **no
consistent winner** (the flat average wins 2 of 4 `tamil_card` folds and 1
of 4 `roof_graphic` folds, all by single-digit mm) - not a real
improvement, within noise.

**Decision: no rule was changed.** The flat-average-cy alternative doesn't
clearly beat the current scheme in leave-one-out (the bar this task set),
and the dominant error is in size (h/w), which is non-monotonic in aspect
across only 4 points - not enough, non-monotonic data to fit anything more
sophisticated than the current linear interpolation without just fitting
noise. `brand_rules/dalmia.json` and `layout.py` are unchanged.
**11-216 stays flagged as a REVIEW case for insufficient evidence** (used
directly by Step 5's confidence label, below), not folded into a rule that
the data doesn't support. 5 unit tests on the analysis script's own
mechanics (which board's row is held out, mm conversion uses that board's
own page size, sequence_4 detection) - there is nothing to unit-test about
"whether the proposed rule works" since the answer was no.

### Step 5: confidence label (`app/confidence.py`) - GOOD / REVIEW / MANUAL, callable with no designer file

`confidence_label(width_mm, height_mm, brand, content_check=None,
layout_checks=None)` can be called **before generation even starts** with
just the requested size and brand - no designer file needed, because the
one thing it can know ahead of time (whether this aspect ratio has ever
been validated) is looked up from `app/confidence_bounds.json`, a small
table derived OFFLINE from past validation runs
(`tools/derive_confidence_bounds.py <brand>`, excludes
`validate_all.KNOWN_OUTLIERS`), not compared live against a real file for
the new request - there isn't one yet. `content_check`
(`content_check.check_content`) and `layout_checks` (`metrics.layout_checks`)
are optional, only meaningful once a board has actually been generated, and
can only ever make the label **worse**, never rescue one that's already bad
- a request already outside every validated aspect range doesn't become
trustworthy just because nothing else went wrong (a dedicated test locks
this ordering in).

**Rules** (in order, worst wins): MANUAL if the brand has no bounds file at
all, or the aspect ratio (tiled or not - decided by `layout._tile_plan`,
called directly so this can never drift from the real tiling decision) is
outside every validated range for that regime, or a hard `layout_checks`
failure, or `content_check.overall == "CONTENT_FAIL"`. REVIEW if the regime
matched but its historical median position/size error exceeds
`REVIEW_ERROR_MM` (200mm, calibrated by eye against the numbers below - the
tiled bucket's ~430-1900mm median clears it easily, the untiled bucket's
~116-139mm doesn't), or a `layout_checks` warning. GOOD otherwise.

**Every validated dalmia board, real bounds** (`n=7` untiled samples,
aspect range [2.40, 2.50]; `n=4` tiled samples, [3.00, 4.50] - both exclude
board 12 as a known outlier):

| Board | Target | Tile | Label | Why |
|---|---|---|---|---|
| 02 (180x48) | 4572x1219 | x,2 | REVIEW | tiled regime's historical median error (427/1905mm) |
| 06 (180x60) | 4572x1524 | x,2 | REVIEW | same |
| 03 (120x48) | 3048x1219 | none | GOOD | aspect 2.50 in validated untiled range, median 116/139mm |
| 05 (120x48) | 3048x1219 | none | GOOD | same |
| 09 (144x60) | 3658x1524 | none | GOOD | aspect 2.40 in range |
| 10 (144x60) | 3658x1524 | none | GOOD | same |
| 11 (216x48) | 5486x1219 | x,2 | REVIEW | tiled regime |
| 11 (240x60) | 6096x1524 | x,2 | REVIEW | tiled regime |
| **12 (120x60, outlier)** | 3048x1524 | none | **MANUAL** | **aspect 2.00 is outside [2.40, 2.50] - extrapolation** |
| 13 (144x60) x2 | 3658x1524 | none | GOOD | aspect 2.40 in range |
| 14 (120x48) | 3048x1219 | none | GOOD | aspect 2.50 in range |

None of these 12 hit a content-check or layout-check downgrade (content
check was 12/12 `CONTENT_OK`, per Step 1; no layout check warned or failed
on any of them) - every label above comes purely from the aspect-range/
regime lookup. **Board 12 landing on MANUAL, entirely from its aspect ratio
being outside the validated range, is a real, useful confirmation**: it is
independently already known (from `validate_all.KNOWN_OUTLIERS`, derived
from a completely different signal - a large systematic geometric shift) to
be the one board in this set that doesn't behave like the others. The
confidence label reaches the same conclusion from aspect ratio alone,
before any comparison to its real file.

9 unit tests, all against synthetic `bounds` dicts (no real file, no
CorelDRAW, no dependency on `confidence_bounds.json`'s current contents) -
covering GOOD/REVIEW/MANUAL from the aspect-range check alone, the
tiled-regime REVIEW, content/layout checks only ever making things worse,
an unknown brand, non-positive sizes, and a regime with zero samples. A
10th test DOES load the real `confidence_bounds.json` and checks every
validated dalmia board comes back GOOD or REVIEW (never MANUAL purely for
"unknown"), skipped gracefully if that file isn't present in a checkout.

**Checked against the real Step 3b labels** (`docs/engine-report.md`): the
property that actually matters - never GOOD on a board a human rejected -
holds on all 12 labelled boards (0 violations). 3 boards the human accepted
come back REVIEW (all tiled/wide, the regime with historically large
error) - conservative in the safe direction, not treated as a defect.

## Metrics suite (Phase 1: `backend/tools/metrics.py`)

A second, independent scoring layer on top of `validate_all.py`'s
object-geometry diff, built to answer "does this board actually look
right" rather than only "do individual shapes land within N% of the real
file's." Pure Python (PIL/numpy/scipy), no CorelDRAW - callers must already
have PNGs and shape dumps (`cache_ours_dumps.py` / `cache_real_renders.py`
/ `validate_all.py` produce those over COM; `metrics.py` itself never opens
CorelDRAW).

- **(a) Visual similarity** - three independent signals on the same
  greyscale, common-width downscale of ours vs. real PNGs: SSIM
  (`image_compare.py`, pre-existing), a gradient-based perceptual hash
  (dHash, Hamming distance), and a Sobel edge-map normalized correlation.
  Combined into one weighted score (`visual.ssim_weight` /`phash_weight`
  /`edge_weight` in `metrics_config.json`, default 0.4/0.3/0.3). No single
  one is reliable alone - SSIM is fooled by a uniform colour shift, phash is
  coarse, edges ignore fill/colour entirely - reporting all three plus the
  combination is more honest than picking one metric.
- **(b) Cluster-level comparison** - reuses `derive_brand_rules.cluster()`
  (bbox-proximity union-find) to collapse each file's non-bg/frame leaf
  shapes into logical clusters (so ~130 loose curves become 2-3 comparable
  blobs instead of shattering an object-level diff - same reasoning as
  `validate_all.py`'s leaf-flattening, one level up in granularity), then
  greedy-matches ours vs. real clusters by role + nearest centre + similar
  size (`_size_similar`, reused directly from `validate_all.py`). bg/frame
  are compared directly, never clustered - they usually span the whole
  page and would swallow every other cluster into one via bbox overlap.
- **(c) Counts** - shape/text/cluster counts ours vs. real.
- **(d) No-ground-truth layout checks** - run on ours' own shape dump
  alone (so they also work at real deployment time, with no designer file
  to compare against - see Phase 6's planned "master check"):
  `within_page` (nothing outside the page), `no_cluster_overlap` (no two
  clusters' bounding-box *envelopes* overlapping more than
  `layout.max_overlap_ratio` of the smaller one's area), `min_margin`
  (minimum margin from the page edge, `layout.min_margin_mm` -
  **full-bleed clusters are exempted from this one check only**: a
  cluster spanning the full page width/height, or covering
  `layout.full_bleed_area_ratio` of the page area, is a deliberate
  edge-to-edge design element - every dalmia board flagged a spurious
  0.0mm margin from exactly this before the exemption was added, GATE 1
  feedback), `text_legibility` (no text shape below `layout.min_text_pt`,
  CorelDRAW's own reported point size post-layout), and `text_overlap`
  (any two text/shopname *shapes* - not clusters, see below - overlapping
  at all is a hard fail, zero tolerance). A board with any `fail`-status
  check (currently `within_page` or `text_overlap`) is reported FAIL in
  the HTML report regardless of its visual score.

  `text_overlap` is deliberately built from leaf text shapes, not
  `_bbox_clusters`: two text shapes close enough to actually overlap are,
  by definition, close enough that `_bbox_clusters`'s proximity margin
  would already have union-find-merged them into one cluster - checking at
  cluster granularity would hide exactly the collision this check exists
  to catch (found this the hard way: the first version of the check never
  fired on a board known to have a real overlap). Real masters use one
  literal CorelDRAW text shape per label (shop name, phone/GST), not
  fragmented curves the way logos are, so no clustering step is needed
  for text at all.

Every threshold lives in **`backend/tools/metrics_config.json`** (single
file, per the project's rules of engagement: calibrate by eye, never tune a
number just to raise a pass rate). `backend/tools/build_metrics_report.py
<brand>` renders an HTML side-by-side report
(`dataset_analysis/metrics_report/<brand>/index.html`) - ours vs. real PNG
plus every metric and a PASS/FAIL badge (visual `combined` score AND every
layout check, not visual alone - see above) - for every non-trivial board
in that brand's `validate_all.py` report. `cache_ours_dumps.py` /
`cache_real_renders.py` are small batch helpers (routed through the same
`corel_supervisor` worker-subprocess hang protection as everything else
that touches CorelDRAW - see "Process isolation" above) that backfill the
shape dumps and real-file PNGs a report needs when `validate_all.py`'s own
`worker_results.json` (which only holds the *last* run's jobs, not the
accumulated history across several `--resume` cycles) no longer has them.
**Caution found the hard way**: running `validate_all.py --only <substr>`
without `--resume` rebuilds `validation_report.json` from scratch for just
that filtered subset, silently discarding every other board's entry - it
happened once while regenerating two boards to verify the text-fit fix
(below), caught immediately because the report shrank from 13 boards to 1,
and fixed by reconstructing the other 12 boards' entries offline from
their already-cached shape dumps (no COM needed, since nothing about their
actual generated output had changed). If this happens again, `--only`
should only be used together with `--resume`, or on a full un-filtered
run.

**Dalmia calibration data (12 boards, excludes the master-as-its-own
-target sanity check)**: the combined visual score cleanly separates the 4
known-over-tiled boards (0.35-0.41) from the 8 correctly-placed ones
(0.67-0.98), and the current default threshold (0.75) happens to land
almost exactly on that boundary - **7/12 pass** (both by visual score alone
and combined with the layout checks - none of the 7 visually-passing
boards have a hard-fail layout check), close to `validate_all.py`'s
existing 5%-tolerance geometry pass count (6/12) without being identical
to it (they're different metrics, not expected to agree exactly), and it
correctly fails board 12 (0.667), the already-known geometry outlier. This
is a promising sign the metric is measuring something real, not noise, but
**the 0.75 default in `metrics_config.json` is still provisional** - kept
at GATE 1 pending further review, not yet exhaustively confirmed by eye
board-by-board.

**Sensitivity test** (`backend/tools/sensitivity_test.py`,
`dataset_analysis/sensitivity/sensitivity_report.md`) - checks the visual
metric actually reacts to known-wrong output, using one real "ours" PNG as
a clean baseline against synthetic/real perturbations:

| Case | Combined score | Delta vs. control | PASS? |
|---|---|---|---|
| control (identical image) | 1.000 | +0.000 | PASS |
| 5% horizontal shift | 0.663 | -0.337 | FAIL |
| 10% horizontal shift | 0.499 | -0.501 | FAIL |
| wrong shop's board (a different real board, same target size) | 0.990 | -0.010 | PASS |
| missing graphic (central 40% blacked out) | 0.629 | -0.371 | FAIL |

Shifts and a missing graphic - genuine geometric/content errors - both
drop the score sharply and correctly flip it to FAIL. **The "wrong shop's
board" case barely moves the score at all (0.990, still PASS)** - a real,
important gap: the visual metric is measuring *layout/graphics similarity*,
which is nearly identical between two boards from the same master, and is
effectively blind to whether the *shop name is actually correct* - the one
error a print shop can least afford to ship. This isn't a metric to fix by
reweighting SSIM/phash/edge (none of them are the right tool for "is this
text string correct"); it needs a **separate, ground-truth-free content
check** - comparing the generated text against the shop's own intended
`name`/`phone`/`gst`. **Implemented - see "Content check, geometric
accuracy in mm, and threshold calibration" below**, which reproduces this
exact scenario as a unit test and confirms it now correctly fails.

## Tests

`backend/tests/test_layout.py` (41 tests: the original 7, tiling, shop-name
replacement, the footer-text-not-tiled fix, brand-rule tiling, per-shop
contact-info replacement - `find_contact_ids`/`_contact_replacement` - and
`panel_sequence` tiling - slot count by aspect, badge enlargement, even
spacing, schema precedence, the unmatched-shape fallback that catches the
accent-strip bug, per-aspect `size_table` interpolation/clamping instead
of a flat average, width-constrained scaling for a group whose real
proportions don't match its master shape's own aspect ratio, the
`card_from` borrowed-card-background mechanism, and its white-content
recolor-to-match-template logic - all described above),
`backend/tests/test_batch_import.py`
(8 tests), `backend/tests/test_metrics.py` (22 tests: dhash/edge math,
cluster matching, counts, and every layout check including the full-bleed
margin exemption, text_overlap, and shopname_in_bottom_bar, all on small
synthetic shape lists/images - no CorelDRAW, no real dataset files) and
`backend/tests/test_example_engine.py` (7 tests: entity classification, the
shopname-by-relative-height matching fix, example transform recording,
nearest/interpolated prediction, and the brand-override hook, all on small
synthetic masters) cover pure logic. `backend/tests/test_corel_supervisor.py`
(5 tests) covers the worker/supervisor timeout, progress-callback and kill
logic using a fake worker (`tests/fake_hanging_worker.py`) that hangs,
partially completes, or finishes normally on command - no real CorelDRAW
needed, but Windows-only (uses `taskkill`; skipped elsewhere). Run with
`pytest` from `backend/` — 436 passed as of this writing (that includes
the new-UI suites: `test_main_v2.py`, and Phase C's `test_scene_ops.py`,
`test_scene_export.py` - fake COM objects, `test_editor_api.py`,
`test_corel_worker_io.py`, `test_fonts.py`, `test_export_replay.py` (now
including the `swap_image`/`update_product_slot` COM replay - see
"Product slots"), `test_product_engine.py` - see "Product slots" above -,
`test_orientation_adapter.py` and `test_orientation_api.py` - see
"Orientation adaptation" above); `npm test` from `frontend/` runs 121 more
(`ops.test.mjs` against the shared golden cases, `model.test.mjs`,
`product_engine.test.mjs` - `package.json`'s `test` script was fixed to
actually run this file, see "Product slots" above) -
`orientation_adapter.py` itself still has no frontend mirror (only its
API/UI wiring is JS; the geometry stays backend-only, unlike
product_engine.py which is mirrored). Note that the
`engines.py._resize_and_tile` reuse-vs-duplicate bug (see "Wide-board panel
sequence") has NO unit test coverage - it's COM-shape-lifecycle logic, only
exercisable against a live CorelDRAW, and was only caught by looking at a
rendered PNG, not by any automated check; there isn't currently a good way
to catch a regression here without a real CorelDRAW in CI. There's no
automated test for `CorelEngine` itself (including the new `_fit_text`
shrink/wrap logic) beyond that — it needs a live CorelDRAW on Windows, so
it's verified by hand against real master files
(`validate_all.py`, and see notes above and `backend/dataset_analysis/`).
