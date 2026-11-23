# Master spatial intelligence

Status: implemented, **off by default** (`SIGNAGE_SPATIAL_PLANNER`). Real CorelDRAW 27.0.0.121 validation is listed at the end - read it before
trusting anything here.

## What it is

A master `.cdr` is analysed **once per file version** into a spatial model (`backend/app/master_model.py`), stored and reused for every shop size.
A separate planner (`adaptation_plan.py`) computes a target-specific placement from that model; `plan_validation.py` checks it. The legacy engine
still owns CorelDRAW, text replacement and fitting, designer examples and learned corrections.

```
upload ──► master_store.begin ──► CorelDRAW read (corel_worker "master_analysis")  ── local: corel_supervisor, one job at a time
                                              └─ or the owner's Windows agent (agent job kind "master_analysis")
        extraction.json + master_page.png ──► master_model.build_model ──► background_analysis (render) ──► spatial_model.json
convert ──► main._convert_job adds shop["spatial_model"] (only if planner on AND the stored model is current)
        ──► engines.CorelEngine._apply_spatial_plan ──► adaptation_plan.apply_to_placed ──► legacy Placed list (validated, or untouched)
```

## Modules

| File | Job |
|---|---|
| `app/master_analysis.py` | COM extraction (`extract_master`), page render, `analyze_master_file`. Every property read is defensive; failures are counted in `errors`, never fatal. |
| `app/master_model.py` | `MasterModel` / `ModelObject` (dataclasses, JSON): exact mm geometry + normalized, roles (`layout.detect_role`), adaptive bands, regions (`orientation_adapter._child_zone`), relations, visual units (`layout.cluster_fragments`), suggested adaptation mode, `to_scene()` (an editor scene for `orientation_adapter`). |
| `app/background_analysis.py` | Render-based colours (Pillow + NumPy, no OpenCV): dominant colour inside/around each object, text surface + WCAG contrast, texture class, empty regions. Declared fill and visible colour are kept apart. |
| `app/master_store.py` | Lifecycle: claim (atomic), store, validate, cache, `usable_model`, status for the API, mock extraction for tests. |
| `app/adaptation_plan.py` | `plan_adaptation(model, w, h)` → strategy `uniform` / `fit` / `tile` / `reflow` / `declined`; `apply_to_placed`. |
| `app/plan_validation.py` | Structured issues (severity, rule, ids, message, suggestion). |
| `app/corel_worker.py` | new job `{"master_analysis": cdr, "analysis_out": dir}`. |
| `app/main.py` | `_ensure_master_analysis`, `_master_analysis_worker`, `_finalize_agent_master_analysis`, routes below, `analysis` field on `/api/masters`. |
| `app/db.py` | table `master_models` + `claim_master_analysis`, `finish_master_analysis`, `fail_interrupted_master_analyses`. |
| `agent/signage_agent/{client,jobs}.py` | the agent claims and runs `master_analysis` jobs. **Needs an agent rebuild/publish** (not done). |

## Storage and cache

- Table `master_models(job_id, master_hash, extractor_version, schema_version, corel_version, status, error, model_path, started_at, created_at, updated_at)`
  - created by `CREATE TABLE IF NOT EXISTS` (no migration needed). The model is `<master folder>/spatial_model.json`; the render `analysis/master_page.png`.
- A version = `jobs.master_hash` (SHA-256 recorded at upload) + `EXTRACTOR_VERSION` + `SCHEMA_VERSION`. A completed row whose hash/versions differ is
  `needs_reanalysis` and is **never** used by a conversion (`master_store.usable_model`).
- One analysis per master version at a time (`BEGIN IMMEDIATE` claim); an `analysing` row older than 30 min can be taken over; a server start fails
  orphaned local rows (`recover_interrupted`), keeping rows with a live agent job.
- Replacing a master file (`PUT /api/masters/{id}/file`) re-analyses it.

## API (all additive)

- `GET /api/masters` and master JSON: new `analysis: {status, analysed_at}`; status ∈ `pending | analysing | completed | failed | needs_reanalysis | disabled`. No error text.
- `GET /api/masters/{id}/analysis` – same status.
- `POST /api/masters/{id}/analysis?force=` – admin: start / redo.
- `GET /api/admin/masters/{id}/analysis` – admin: error text, versions, hash, model path, model summary.
- No frontend change was made (the field is available for a badge).

## Environment

| Variable | Effect |
|---|---|
| `SIGNAGE_SPATIAL_PLANNER=1` | conversions receive the stored model and the planner may place plain objects. Default off. |
| `SIGNAGE_MASTER_ANALYSIS=1/0` | force analysis on/off. Unset: on only when the planner is on and the engine is real (corel / remote). The mock engine never analyses unless `=1`. |

## Priority with the existing intelligence

1. Designer example layout (`layout_source.mode == "example"`) – the planner is skipped.
2. Example text specs / `name_hints` / `keep_pos_spec`, and **learned corrections** (`shop["intelligence"]` – the planner is skipped).
3. Spatial planner – only for objects the legacy path placed by its generic rules (`adaptation_plan.eligible`: never a shop-name line, a `text_fit`,
   a PowerClip plan, a tile copy, or a background).
4. Legacy generic rules.

Any problem (no model, stale model, the model does not match the opened file's top-level objects/boxes, `declined`, a validation **error**, an exception)
leaves the legacy result exactly as it was and says why in the report (`report.layout.spatial`, warnings). Confidence labels are untouched.

## Adaptation behaviour

- **Same aspect (10×8 → 20×16):** `uniform`, one factor for every box.
- **Different aspect, same orientation (10×8 → 12×6):** `fit` – per-object mode: background STRETCH to the page, page-spanning rules STRETCH along their axis,
  logos/text/artwork UNIFORM_SCALE about their own centre, small edge-touching objects ANCHOR to that edge (distance scaled), `fixed_*` FIXED, loose
  fragments move as rigid units (`layout._place_logo_units`, reused). Nudged inside the board, never resized.
- **Very wide / tall (10×8 → 20×6):** `tile` – uses `layout._tile_plan` and `layout._place_tiled_panel`; the plan lists panel copies (`<id>_tile<n>`) but
  `apply_to_placed` declines tiled plans: the legacy engine performs the `Duplicate()` calls.
- **Orientation change (landscape ↔ portrait):** `reflow` – `orientation_adapter.classify_zones/convert_orientation` run on `model.to_scene()` and the final
  boxes are read back. Declined when the editor logic would move objects that sit inside the background PowerClip (the engine cannot place those). A
  reflow plan is a draft (confidence 0.6, warning).

## Limitations (honest)

- The planner places **top-level** objects only. A master whose artwork is one big group or one page-covering PowerClip (Hangyo `4 X 8.cdr` has 2 top-level
  objects) gets almost nothing from it.
- On the real Hangyo masters every tested size already has a designer example, so the planner did not run there; on a rules-mode brand the result was
  nearly identical to legacy (see validation).
- `reflow` into the engine is only unit-tested (fake data + a cached real dump); it has **not** been run through CorelDRAW conversion.
- The `texture` / `gradient` labels and empty-region search are heuristics. A sampled colour is a sample, not a shape fill.
- Alignment / equal-gap / repeat relations are geometric with fixed tolerances; the repeat→TILE suggestion has confidence 0.55 and is advisory.
- Fountain fills and bitmap resolution are read defensively but were not confirmed live (no such shape in the probed masters).
- Agent path: unit/integration tested with the **mock** agent only; a real agent run needs an agent build that includes `master_analysis` (not built/published).

## Real CorelDRAW validation (this PC, CorelDRAW 27.0.0.121, 2026-11-22)

RAN against real CorelDRAW:
- `master_analysis` on Hangyo `10 X 3.cdr` (50 objects, 25-28 s incl. launch), `6 X 3.cdr` (23 objects, 9 s), `4 X 8.cdr` (25 objects, 13 s).
- Verified live: page/layer walk (`page.Shapes` == layer shapes on these masters), geometry, rotation (the 180° frame), uniform fill CMYK, `Color.HexValue`
  RGB (212,29,108 for CMYK 11/100/25/1), `Outline.Width/Type/Color`, `Transparency.Type`; `Transparency.Uniform` exists but **raises for type 0** (fixed).
  `Color.RGBRed` raises "Incompatible color model" on CMYK colours (fixed: RGB from `HexValue`).
- Render-based background analysis on the real page renders: dominant page colour (212,29,108) agrees with the declared CMYK fill.
- One real conversion batch with the planner ON and the same batch with it OFF, brand `dalmia` (rules mode), from the `6 X 3` master to 5×5, 4×3 and 7×4 ft: all `done`, the engine accepted the
  model (ids/boxes matched the opened file), 4 objects placed by the planner; the PNGs next to the legacy ones show no corruption and near-identical layouts
  (only the edge-anchored badge position differs).

NOT run: real agent execution of `master_analysis`; a `reflow` conversion through CorelDRAW; PDF/CDR output checks of planner boards beyond the PNG preview;
comparison with designer boards for planner output (none exists for these sizes); CorelDRAW 2019.


## Validation round (2026-11-22, planner ON vs OFF) - the quality gate

Tool: `backend/tools/spatial_eval.py` (`run`, `compare`, `verify`, `offline`). Rules-mode brands (`dalmia`, `reflowtest`) so no designer example is used.

**Real CorelDRAW 27, 13 scenarios x {planner OFF, planner ON}** (Hangyo masters 10 X 3, 6 X 3, 4 X 8): all 26 conversions `done`; all 26 CDRs open in real
CorelDRAW with the right page size, PDFs have a valid header, PNGs open; no orphan CorelDRAW process, no dialog in either server log.
Planner ON was active in 9 scenarios (same / different aspect, portrait) and **placed every object exactly where the legacy engine did (0 objects differ by more than
0.5 mm; 0 rule violations either way; visual similarity ON vs OFF identical; vs the designer board of the same size delta 0.000)**. In the 2 very wide scenarios
(tile) and the 2 landscape->portrait scenarios (foreground inside the background PowerClip) the planner declined and the output is the legacy output.
=> **No improvement was demonstrated; the planner is equal to the legacy engine on these real masters.**

**Offline, real master geometry (6 masters x 10 targets, `offline`)**: `fit` / `uniform` plans equal the legacy boxes except one text object moved 1.8-2.4 mm
(legacy clamps it into the safe margin). The plan differs from legacy only in `reflow`; there, before the fix below, 2 cases put a 37% non-uniform stretch on a
text object (legacy had none) - a regression.

Bugs found and fixed in this round: (1) a master analysis can die with "The remote procedure call failed" when a worker starts right after another one
(seen on the 4th master of a back-to-back upload) -> one automatic retry in a fresh worker; (2) `reflow` validation let the editor's 2.0 stretch cap through ->
text / bitmaps >1.05x and vector groups >1.25x are now validation ERRORS (legacy layout runs); (3) a failed analysis was retried by every conversion ->
10-minute cool-down (`master_store.may_auto_retry`); (4) a conversion with a missing / stale model now asks for an analysis of the CHOSEN master.

Real **source** agent (`agent/signage_agent`, not the packaged exe - it predates this feature) against real CorelDRAW: pairing, claim, `master_analysis` (30.7 s),
model stored, status API `completed`, admin summary (23 objects / 6 top-level), then a convert used the cached model (`spatial planner (fit): 4 object(s)`) and
exactly one analysis job exists. The agent refuses a CorelDRAW batch below 1.5 GB free RAM (two earlier attempts were refused and reported as `failed` correctly).

**Release recommendation: keep `SIGNAGE_SPATIAL_PLANNER` OFF in production.** It is safe (falls back, never corrupted an output) but earns nothing yet. Analysis +
status can be switched on separately (`SIGNAGE_MASTER_ANALYSIS=1`) to collect models.
