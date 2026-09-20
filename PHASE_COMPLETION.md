# Phase completion

Status of the new UI (Phases A-D) and the engine work it sits on, as of commit `72758d9`.
"Verified" means it was run against the real CorelDRAW engine or a real browser, not only unit-tested.

Legend: DONE = built and verified, PARTIAL = works with a stated limit, NOT DONE = not built.

Test suites: 190 backend (pytest) + 58 frontend logic tests (`npm test`). No automated browser tests are
committed; browser checks were run with scripted headless Chromium and are described below.

---

## Summary

| Phase | What | Status |
|---|---|---|
| A | App shell + Automation page (upload, preview, brands, shops, convert) | DONE |
| B | Recently generated page | DONE |
| C | Editor v1 (scene export, canvas, layers, properties, undo/redo, op list) | DONE (with listed limits) |
| C+ | Follow-ups: nudge, snapping, layer reorder, font check, page-size dialog, properties tabs | DONE |
| D | Save and Generate (replay through CorelDRAW + CDR/PDF/PNG/JPEG export) | DONE (no JPEG quality, see below) |
| D | Text editing by double-click, with font-installed warning | DONE |

---

## Phase A - Automation page

DONE
- Left-sidebar shell, red/white/black theme, all design tokens in one file (`frontend/src/theme.css`).
- Upload with a real XHR progress bar; instant preview extracted from the `.cdr` itself (a `.cdr` is a ZIP), no CorelDRAW call.
- Brand dropdown with "+" to add a brand.
- Shops table: name, width/height each with its own unit (in/cm/mm/ft), reference, per-row Convert with live step progress, Editor "Open" button.
- SQLite persistence (survives a server restart); reuses the existing worker/supervisor and single-job queue.
- Smoothed convert progress from measured step durations; never shows 100% until the backend reports done.
- Failed convert shows the reason inline and a Retry button.
- `reference_file_path` column exists in the data model.

NOT DONE / LIMITS
- Reference field is still free text only (no file upload UI) - waiting for you to confirm what it should be.
- The new UI does not pass shop-name / phone / GST / address replacement to the engine, so the master's own shop name stays unless you edit it in the editor (the old UI has these fields).
- Convert progress is step-level (coarse), not byte-level.
- Free RAM below 1.5 GB makes the memory guard refuse to start CorelDRAW (by design); close programs and Retry.

## Phase B - Recently generated

DONE
- Lists all shops newest first: brand, shop, size (own units), created time, status, download links, thumbnail.
- Brand and status filters, Refresh, auto-refresh while something is converting.
- "Open in editor" opens `/editor/:jobId/:shopId` in a new tab.

LIMITS
- With the mock engine the thumbnail is an SVG placeholder; with CorelDRAW it is the real PNG.

## Phase C - Editor v1

DONE
- Scene export through CorelDRAW COM: page size, layer tree (layers -> groups -> shapes), per-object x/y/w/h/rotation/type/name/visibility/lock/text. Every object image is rendered by CorelDRAW (SVG for vectors, PNG for text/bitmaps/PowerClips). Measured: 138 shapes in about 10 s of export; 200 shapes in about 18 s.
- Endpoint `GET /api/editor/{job}/{shop}/scene` with live build progress, RAM guard (503 with reason), cached result.
- Canvas in plain SVG: Corel-like rulers with red dimension arrows (page W and H), zoom (wheel), middle-mouse pan, 8 resize handles, marquee selection, pixel-accurate click selection through transparent areas.
- Groups and PowerClips select as one object; double-click enters a group; Esc leaves it.
- Shortcuts: Ctrl+Z, Ctrl+Shift+Z / Ctrl+Y, Ctrl+C/X/V, Ctrl+A, Ctrl+click multi-select, Ctrl+G, Ctrl+U, Delete, F2, Esc.
- Toolbar: page W/H with unit dropdown (default in), undo, redo, zoom in/out, fit, snap toggle, "Corel page render" comparison toggle, Save and Generate.
- Properties panel (tabs): Dimensions (W/H, keep proportions), Position (X/Y, stacking order), Text (font, size, content).
- Layers panel mirroring CorelDRAW's Object Manager wording: expandable groups, click selects both ways, drag to reorder (including into groups), ungroup per group, Group button, eye icon per object and layer, layer drag-reorder.
- Every edit is an operation in a replayable list (move, resize, order, reorder, visibility, group, ungroup, text, delete, paste, page, layer_order), autosaved, restored on reload. Python and JavaScript implementations are checked against the same hand-computed test cases.
- Verified: composite of per-object images vs CorelDRAW's own page render differs by a mean of 2.3/255.
- Nudge with arrow keys (Shift x10, Ctrl x0.1, held key merges into one undo step).
- Snapping to page edges/centre and other objects with guide lines (Alt bypasses; toggle in toolbar).
- Font-installed check via `GET /api/fonts` (uninstalled fonts are refused with an explanation).
- Page W/H change opens a dialog: re-convert at the new size through the real layout engine, or change the page only.

PARTIAL (works, with a limit)
- Resize stretches the image; it is not re-rendered by CorelDRAW until you generate.
- Text edits show an "edited" badge; the canvas keeps CorelDRAW's original render until you generate.
- Rotated shapes keep their rotation baked into the image and are resized by bounding box.
- Locked objects and layers are shown but cannot be edited.
- A real dalmia board shows about 130 "Curve" rows in the layers list - that is the master's actual structure (ungrouped curves), verified against the file.

NOT DONE
- Editing inside a PowerClip (contents are listed but read-only; the clipped result moves/resizes as one object). Deferred on purpose: none of the exported dalmia boards has a PowerClip.
- Creating new layers, drawing new shapes, rotating, or importing images in the editor (not in the brief).
- Automated browser tests committed to the repo.

## Phase D - Save and Generate

DONE
- Popup: choose CDR / PDF / PNG / JPEG (multi-select) with options, then a real progress bar (launch, open, replay i/n, verify, each format), paced by measured durations.
- Backend replays the saved operation list through CorelDRAW COM on the converted `.cdr` (original never overwritten) and exports the chosen formats. Every output file is written by CorelDRAW.
- Options CorelDRAW honours (each checked live):
  - PDF: colour mode (keep / RGB / CMYK), embed fonts or text as curves, image dpi.
  - PNG / JPEG: size by longest side (1600 / 4000 / 8000 px) or dpi, PNG transparent or white background, anti-aliasing.
  - Exact pixel size (verified 600x200 and 13500x4500 on real CorelDRAW).
  - Hard limits: 20000 px longest side, 200 megapixels, enforced in the popup and the API; extra RAM check for large images.
- Replay verification: after replay, the real document is compared with what the editor expects (structure, z-order, positions, visibility, text) and the result is shown in the popup.
  - Live: 14 mixed operations applied, 142 objects compared; via the UI a 3-edit session verified "all 142 objects match".
- Results panel: download links, file sizes, pixel sizes, preview image, warnings.
- Text editing: double-click a text object (or F2) to type; Ctrl+Enter or click away applies, Esc cancels; warns inside the editor and in the export popup if the font is not installed; at replay the font is read back and a warning is recorded if CorelDRAW ignored it.

NOT DONE / LIMITS
- **JPEG quality setting is not offered.** CorelDRAW's automation interface does not expose it (file size does not change with any value tried). It was not faked, and JPEGs are never re-encoded outside CorelDRAW.
- Text that becomes longer after an edit is not refitted to its slot (check the preview in the popup).
- Setting new text replaces the run, so mixed per-character formatting inside one text object is not preserved.
- Editing inside PowerClips (same as Phase C).
- Moving objects into an existing group is done by ungroup + regroup (works and verified; the group keeps its editor id, but it is a workaround, not a native call).
- Export history is listed by the API but there is no history screen in the UI yet.
- PDF export can take up to about 18 s on boards with large embedded bitmaps.

---

## Engine work the UI depends on (separate from the UI phases)

DONE
- Layout rules, tiling, brand rule with per-aspect panel sequence, card borrowing and recolor, shop-name / phone / GST replacement, text fit, worker/supervisor process isolation, watchdog, orphan cleanup, memory guard.
- Metrics suite and validation harness. Dalmia: 9 of 12 boards pass the visual metric at the provisional 0.75 threshold (7 of 12 before the wide-board work); board 12 is a documented outlier.

NOT DONE (from the earlier engine task, Steps 1-3)
- Step 1 content check (verify placed text matches the intended shop name / phone / GST): not implemented. The visual score cannot catch a wrong shop name (a wrong-shop board still scored 0.990).
- Step 2 leave-one-out: done, negative result - the example-based engine scored 25.7% mean max-diff vs 5.4% for the rule engine, so it is not integrated; its wide-board comparison is not a valid measurement.
- Step 3 confidence labels: not implemented (only an informal REVIEW note on board 12).
- Agarpathi: not validated and not touched (held back on instruction).
- Wide-board layouts still do not match designer files exactly; 11 (216x48) remains the weakest wide case and 06 (180x60) is just under the threshold.
- The 0.75 visual threshold is still provisional.

---

## Known environment limits

- RAM: free memory on the dev machine ranged from about 0.9 GB to 4.4 GB during this work. Below 1.5 GB the guard refuses to start CorelDRAW (convert, editor scene build, export). Large image exports need more.
- Fonts: the Tamil footer renders as boxes when the master's font is not available to CorelDRAW on this machine (existing behaviour, not caused by the editor).
- Only one CorelDRAW job runs at a time (shared queue across convert, scene build and export).
