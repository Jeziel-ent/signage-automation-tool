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
  **text change** the canvas keeps showing Corel's original render and
  marks the object "edited" until Phase D regenerates it; rotated shapes
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

## Product slots (`backend/app/product_engine.py`, `frontend/src/editor/product_engine.js`) — foundation only, not wired into any UI

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

- **`swap_image`** `{id, asset:{name,w,h}, fit?, frame?, padding?}` - fits
  a new image (given only as its *natural pixel size*; no actual bitmap
  asset pipeline exists yet - see "Not built" below) into a *frame*, then
  sets the shape's box to the fitted result and marks it (and any
  PowerClip ancestor) `stale`. The frame is: the PowerClip's own box, when
  the image sits inside one (an explicit `frame` that disagrees is
  refused, not silently ignored - a stale client could otherwise fit into
  the wrong box); otherwise an explicit `frame`, else the frame
  *remembered* from an earlier swap of this same shape (`node.slot_frame`),
  else the shape's own current box. Remembering the frame is what stops a
  second swap from fitting into the *shrunk* result of the first one -
  covered by a golden case (`10,10,50,30` remains the frame across two
  chained swaps even though the first swap's own box ends up `10,12.5,50,25`).
  `fit: "contain"` (default) shows the whole image; `"cover"` fills the
  frame and lets the PowerClip clip the overflow, so it's refused outside
  one. `padding` insets the frame on every side first.
- **`update_product_slot`** `{id, kind, asset?/text?}` - the slot-aware
  wrapper: `product_engine.map_slots(scene)` finds the slot for a node id
  (or `slot_id` via `update_slot_op`), and this op takes `asset` for an
  image slot or `text` for the other three (rejecting the wrong one for a
  given `kind`, and any unknown `kind`) - a `product_image` slot delegates
  straight to `swap_image`'s geometry; a text slot just replaces
  `text.content` and marks the shape stale, like the existing `text` op.

**Not built, deliberately, per this task's scope**: there is no bitmap
asset pipeline (upload/storage/serving) - `asset` is just `{name, w, h}`,
the natural pixel size a caller already knows, used only for the aspect
ratio; and `export_replay.Replayer` has no COM handler for either op yet -
`Replayer.apply` now refuses both with a clear "cannot be exported to
CorelDRAW yet" `ReplayError` instead of crashing on the missing method (a
real gap this surfaced and fixed, unrelated to the swap logic itself:
before this, ANY future op with no `_op_<name>` handler would have failed
with a raw `AttributeError`-derived message rather than a clear one).
Neither module is imported by any page/route - this is registry +
op-semantics groundwork, not a feature a designer can use yet.

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
  restarting the machine before a long batch is the practical mitigation.
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
machine."

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
`pytest` from `backend/` — 347 passed as of this writing (that includes
the new-UI suites: `test_main_v2.py`, and Phase C's `test_scene_ops.py`,
`test_scene_export.py` - fake COM objects, `test_editor_api.py`,
`test_corel_worker_io.py`, `test_fonts.py`, `test_export_replay.py`, and
`test_product_engine.py` - see "Product slots" above); `npm test` from
`frontend/` runs 90 more (`ops.test.mjs` against the shared golden cases,
`model.test.mjs`, `product_engine.test.mjs`). Note that the
`engines.py._resize_and_tile` reuse-vs-duplicate bug (see "Wide-board panel
sequence") has NO unit test coverage - it's COM-shape-lifecycle logic, only
exercisable against a live CorelDRAW, and was only caught by looking at a
rendered PNG, not by any automated check; there isn't currently a good way
to catch a regression here without a real CorelDRAW in CI. There's no
automated test for `CorelEngine` itself (including the new `_fit_text`
shrink/wrap logic) beyond that — it needs a live CorelDRAW on Windows, so
it's verified by hand against real master files
(`validate_all.py`, and see notes above and `backend/dataset_analysis/`).
