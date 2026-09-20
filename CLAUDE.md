# CLAUDE.md

Guidance for working in this repo.

## What this is

Designer flow: upload a master `.cdr` → pick a brand → add shops with W×H →
**Generate** → per-shop `.cdr`, print PDF, PNG preview, and an object
position/size report. Backend is FastAPI (`backend/app`), frontend is
React + Vite (`frontend/`).

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

## Wide-board panel sequence (dalmia) - the rule Phase 2 should implement

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
whichever cluster's size matches." Not yet implemented in `compute_layout`
or `CorelEngine` - this section is the derived rule Phase 2 should build
from, in place of `brand_rules/dalmia.json`'s disproven approach.

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

**Not yet integrated**: none of this is wired into `CorelEngine` or the
website - `compute_layout`/`load_brand_rule` are still what actually runs
a job. Given the negative plain-board result above, integrating the
example engine as-is would be a regression; the wide-panel rule's
*findings* (which entity is which, which one repeats) are worth folding
into `layout.py`'s existing brand-rule mechanism directly - a targeted,
rule-based fix - in preference to shipping the general example-interpolation
approach that this validation shows underperforms.

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
  successful ones). Never touches `signage_dataset/`; all output goes under
  `backend/dataset_analysis/`.
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
text string correct"); it means a **separate, ground-truth-free content
check belongs in `layout_checks`** - comparing `Placed.text` against the
shop's own intended `name`/`phone`/`gst` is trivial (that data already
exists in every job's report, no visual comparison needed) and catches
this exactly. Not yet implemented - flagged here as a known gap the visual
score alone will never close, not something Phase 2 should try to solve by
tuning image-similarity weights.

## Tests

`backend/tests/test_layout.py` (27 tests: the original 7, tiling, shop-name
replacement, the footer-text-not-tiled fix, brand-rule tiling, and per-shop
contact-info replacement - `find_contact_ids`/`_contact_replacement`),
`backend/tests/test_batch_import.py` (8 tests), `backend/tests/test_metrics.py`
(19 tests: dhash/edge math, cluster matching, counts, and every layout
check including the full-bleed margin exemption and text_overlap, all on
small synthetic shape lists/images - no CorelDRAW, no real dataset files)
and `backend/tests/test_example_engine.py` (7 tests: entity classification,
the shopname-by-relative-height matching fix, example transform recording,
nearest/interpolated prediction, and the brand-override hook, all on small
synthetic masters) cover pure logic. `backend/tests/test_corel_supervisor.py`
(5 tests) covers the worker/supervisor timeout, progress-callback and kill
logic using a fake worker (`tests/fake_hanging_worker.py`) that hangs,
partially completes, or finishes normally on command - no real CorelDRAW
needed, but Windows-only (uses `taskkill`; skipped elsewhere). Run with
`pytest` from `backend/` — 66 passed as of this writing. There's no
automated test for `CorelEngine` itself (including the new `_fit_text`
shrink/wrap logic) beyond that — it needs a live CorelDRAW on Windows, so
it's verified by hand against real master files
(`validate_all.py`, and see notes above and `backend/dataset_analysis/`).
