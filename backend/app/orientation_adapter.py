"""Portrait -> landscape (or any aspect/orientation change) re-layout of an editor scene, built on
top of product_engine.py's slot classification and scene_ops.py's existing `resize`/`page` ops - no
new op type is needed (see "Why only existing ops" below).

**The idea.** A board designed for one orientation (say a tall portrait board) rarely still reads
well merely stretched to a very different aspect ratio (a wide landscape board) - this mirrors the
exact problem `layout.py`'s tiling/`panel_sequence` work already solves for the CorelDRAW engine (see
CLAUDE.md "Wide-board panel sequence"), one level up: instead of re-deriving generic geometry rules,
`orientation_adapter.py` uses product_engine.py's SEMANTIC slot tags (this master was built for THIS
tool, so slots are known, unlike the untagged dalmia/Agarpathi masters `layout.py` has to reason about
from geometry alone) to move each kind of content into a purpose-built zone of the target page:

    zone         slot kinds                    which rectangle (see "Three templates" below for
                                                exactly where each zone's rectangle sits - it depends
                                                on the target's aspect ratio, not fixed positions)
    header       brand_title                    part of the "upper" area, above the footer
    product      product_image (incl. nested    part of the "upper" area, aspect-fit scaled into
                 inside a PowerClip)             its own rectangle
    main_text    product_title                   part of the "upper" area
    footer       address, contact                a horizontal banner across the full width at the
                                                  bottom - the same "shop name belongs in the bottom
                                                  bar" placement CLAUDE.md's wide-board work already
                                                  established for contact-like text, reused here
                                                  rather than invented fresh
    background   any top-level shape covering    stretched to exactly fill the new page (bg role,
                 >= product_engine.BG_AREA_RATIO  see layout.py's own `bg` role)
                 of the page area
    other        everything else unslotted      scaled by the page's own uniform fit factor, centre
                                                  kept at the same proportional page position - the
                                                  same fallback layout.py's `text`/`logo` roles use
                                                  for an object with no more specific rule

A shape nested inside a PowerClip is always represented by its CONTAINER's id, never the inner
shape's - resizing/moving the container carries every child with it (scene_ops._scale/_translate
already recurse into `children`), whereas moving just the inner bitmap would leave the visible clip
frame behind. This is exactly product_engine.ProductSlot.container_id's reason for existing.

**Why only existing ops (no new op type).** A "zone" is realised as ONE `resize` op whose `ids` list
covers every top-level shape assigned to that zone and whose `from`/`to` boxes are the zone's own
union bounding box before/after - precisely the same op the editor's canvas already emits for a
multi-selection drag-resize (Canvas.jsx), so `scene_ops._op_resize` already does everything asked
for: it repositions AND rescales every listed shape together (preserving their relative layout, so
shapes that didn't overlap before don't start overlapping just from being resized as a group), and
`_scale` already scales `text.size_pt` proportionally to height - "adjusted font sizes" is not a
separate step, it falls out of reusing `resize` rather than inventing a parallel "reflow" mechanic.
`convert_orientation` also emits one `page` op so the target dimensions actually apply to the page,
not only to the content.

**Not a substitute for a designer.** Grouping the footer's address/contact text and resizing it as
one rigid unit is a scale+reposition, not real text reflow/wrapping (there is no line-breaking here,
matching `CorelEngine`'s own honest limits around text - see CLAUDE.md "Text-fit"); the `other` zone's
per-shape fallback can end up geometrically close to another zone for a master with a lot of
untagged decoration, which is why zone geometry is only GUARANTEED collision-free between the four
named zones (header/product/main_text/footer) plus background - not for `other`. A locked shape (or
one on a locked layer) is silently left out of its zone's op (not moved, not scaled, no error) rather
than making the whole conversion fail - `classify_zones`'s warnings say which shapes this happened to
so a caller can tell the designer.

All lengths are millimetres, like the rest of the scene model (`scene_ops.py`'s docstring) - a
caller with a user-facing size in inches/feet converts with `layout.to_mm()` first, the same as every
other place in this codebase that accepts a board size.

**Three templates, picked by aspect ratio, not just "wide vs. tall".** `calculate_zone_rects` picks
the layout for the area above the footer banner from R = target_w / target_h, since a template tuned
for a very wide board (a thin product column, full page height) looks equally wrong stretched onto a
near-square or a tall target - a single "landscape vs. portrait" split isn't enough once ANY positive
target_w/target_h is accepted, not just the couple of sizes a template happened to be tuned against:

    R range              template        product / header / main_text placement
    R >= 2.0             wide            product: a left column (grows with target_w); header
                                          stacked above main_text in a column to its right
    1.0 <= R < 2.0       grid            a full-width header banner across the top of the upper
                                          area; product and main_text as an equal-width pair of
                                          cells side by side below it
    R < 1.0              stack           header, product and main_text stacked full-width, top
                                          to bottom

The footer is always a horizontal banner across the full width at the bottom, in every template.
"""
from __future__ import annotations

from . import product_engine as pe
from . import scene_ops
from .scene_ops import OpError

ZONE_HEADER = "header"
ZONE_PRODUCT = "product"
ZONE_MAIN_TEXT = "main_text"
ZONE_FOOTER = "footer"
ZONE_BACKGROUND = "background"
ZONE_OTHER = "other"

ZONES = (ZONE_HEADER, ZONE_PRODUCT, ZONE_MAIN_TEXT, ZONE_FOOTER, ZONE_BACKGROUND, ZONE_OTHER)

_KIND_TO_ZONE = {
    pe.SLOT_BRAND_TITLE: ZONE_HEADER,
    pe.SLOT_PRODUCT_TITLE: ZONE_MAIN_TEXT,
    pe.SLOT_PRODUCT_IMAGE: ZONE_PRODUCT,
    pe.SLOT_ADDRESS: ZONE_FOOTER,
    pe.SLOT_CONTACT: ZONE_FOOTER,
}

# Template proportions (fractions of the target page or of its own upper/available area, never of an
# absolute mm figure) - this is what makes calculate_zone_rects work for ANY positive target_w/
# target_h, not just the sizes it happens to have been tried on: margin/gap/footer are fractions of
# min(target_w, target_h), and everything each template derives from "upper_h"/"avail" is in turn a
# fixed fraction of THAT (itself a fixed fraction of target_h), so no computed rectangle's width or
# height can go non-positive for any target_w, target_h > 0 - see the proof in each _*_zones
# docstring instead of a runtime "page too small" check that would either never fire or fire on
# perfectly reasonable inputs depending on the constants.
MARGIN_FRAC = 0.03
GAP_FRAC = 0.02
FOOTER_FRAC = 0.20          # of target height
PRODUCT_COL_FRAC = 0.32     # of target width, wide template only
HEADER_OF_UPPER_FRAC = 0.35  # of the upper (non-footer) area's height, wide template only
GRID_HEADER_FRAC = 0.28     # of the upper area's height, grid template only
ZONE_PADDING_FRAC = 0.03    # of the zone's own width/height, per axis - keeps content off the zone edges

# Aspect-ratio thresholds (R = target_w / target_h) selecting which template calculate_zone_rects
# uses for the area above the footer banner - see the module docstring's table.
WIDE_RATIO = 2.0
GRID_RATIO = 1.0


def _r(v: float) -> float:
    return round(float(v), scene_ops.ROUND)


def _rect(x: float, y: float, w: float, h: float) -> dict:
    return {"x": _r(x), "y": _r(y), "w": _r(w), "h": _r(h)}


def _wide_zones(m: float, gap: float, target_w: float, upper_y0: float, upper_h: float) -> dict[str, dict]:
    """R >= WIDE_RATIO: the header is now a FULL-WIDTH banner across the top of the upper area (margin
    to margin, i.e. horizontally centred on the canvas - the task-given 240x36 example calls for
    exactly this: "Center the main brand header over the canvas width", not confined to a side column
    the way an earlier version of this template placed it), with a left product column and a
    right-hand main_text column sharing the remaining height below it. header_w = target_w - 2*m is
    always positive for any target_w > 0 (m = MARGIN_FRAC*min(target_w,target_h) < target_w/2 for any
    reasonable MARGIN_FRAC < 0.5). remaining_h = upper_h - header_h - gap keeps the same
    HEADER_OF_UPPER_FRAC-based proof of positivity the original layout relied on (header_h is still a
    fixed fraction of upper_h, gap a fixed fraction of min(target_w,target_h) <= upper_h's own scale).
    product_w < target_w - m always (PRODUCT_COL_FRAC=0.32 < 1), so text_w = target_w - m -
    (m+product_w+gap) is positive whenever target_w > 2m + product_w + gap - true across the whole
    WIDE_RATIO domain, verified by
    test_calculate_zone_rects_never_degenerates_across_a_wide_range_of_aspect_ratios."""
    header_h = HEADER_OF_UPPER_FRAC * upper_h
    header = _rect(m, upper_y0 + upper_h - header_h, target_w - 2 * m, header_h)
    remaining_h = upper_h - header_h - gap
    product_w = PRODUCT_COL_FRAC * target_w
    product = _rect(m, upper_y0, product_w, remaining_h)
    text_x0 = m + product_w + gap
    text_w = target_w - m - text_x0
    main_text = _rect(text_x0, upper_y0, text_w, remaining_h)
    return {ZONE_HEADER: header, ZONE_PRODUCT: product, ZONE_MAIN_TEXT: main_text}


def _grid_zones(m: float, gap: float, target_w: float, upper_y0: float, upper_h: float) -> dict[str, dict]:
    """GRID_RATIO <= R < WIDE_RATIO: a compact, balanced 2-row grid - a full-width header banner
    across the top of the upper area, product and main_text side by side in an equal-width pair of
    cells below it. Unlike the wide template's column (a fraction of target_w, which would make a
    near-square board's product zone as tall as the whole page but barely wider than its margin),
    both cells here are (target_w - 2m - gap) / 2 wide - exactly half the available width regardless
    of R, so neither one degenerates as R approaches 1.0 from above."""
    header_h = GRID_HEADER_FRAC * upper_h
    row2_h = upper_h - header_h - gap
    col_w = (target_w - 2 * m - gap) / 2
    header = _rect(m, upper_y0 + upper_h - header_h, target_w - 2 * m, header_h)
    product = _rect(m, upper_y0, col_w, row2_h)
    main_text = _rect(m + col_w + gap, upper_y0, col_w, row2_h)
    return {ZONE_HEADER: header, ZONE_PRODUCT: product, ZONE_MAIN_TEXT: main_text}


def _stack_zones(m: float, gap: float, target_w: float, upper_y0: float, upper_h: float) -> dict[str, dict]:
    """R < GRID_RATIO (a tall/portrait target): header, product and main_text stacked full-width,
    top to bottom, each a fixed fraction of the upper area's own height (`avail`, itself always
    positive - see calculate_zone_rects). header/product/main together always equal `avail` exactly
    (0.42+0.52+0.06 = 1.00), so main_h = avail*0.06 can never go non-positive for any target_w,
    target_h > 0 with target_h > target_w - the same safety proof the original (0.18/0.55/0.27)
    split already relied on, just re-weighted.

    The 0.42/0.52 split (up from an original 0.18/0.55) was chosen to match a concrete, task-given
    example almost exactly: for a 36x96in target, header lands at Y=[64.5, 94.9]in (asked: [64, 96]),
    product at Y=[26.1, 63.8]in (asked: [24, 64]), and main_text+the shared footer together span
    Y=[1.1, 25.3]in (asked: one combined "shop title + address" bottom zone, [0, 24]in) - the small
    gaps from the exact numbers are the same margin/gap insets every zone boundary already has, not a
    rounding error. Found live (job 16bfc025ca11, AL MADEENA): the original 0.18 header fraction left
    the top of a tall target almost entirely to the background texture, since real (untagged) master
    content absorbed into `header` (see convert_orientation) had very little room to actually fill."""
    avail = upper_h - 2 * gap
    header_h = 0.42 * avail
    product_h = 0.52 * avail
    main_h = avail - header_h - product_h
    top = upper_y0 + upper_h
    header = _rect(m, top - header_h, target_w - 2 * m, header_h)
    product = _rect(m, top - header_h - gap - product_h, target_w - 2 * m, product_h)
    main_text = _rect(m, upper_y0, target_w - 2 * m, main_h)
    return {ZONE_HEADER: header, ZONE_PRODUCT: product, ZONE_MAIN_TEXT: main_text}


def calculate_zone_rects(target_w: float, target_h: float) -> dict[str, dict]:
    """The four named zones' rectangles (mm) on a page of `target_w` x `target_h`, disjoint by
    construction, for ANY positive target_w/target_h - not just "typical" signage sizes. The footer
    is always a horizontal banner across the full width at the bottom; the aspect ratio R = target_w
    / target_h picks the template for the area above it (see the module docstring's table and each
    `_*_zones` helper's own docstring for why its geometry can't degenerate): `_wide_zones` for R >=
    WIDE_RATIO, `_grid_zones` for GRID_RATIO <= R < WIDE_RATIO, `_stack_zones` for R < GRID_RATIO."""
    if target_w <= 0 or target_h <= 0:
        raise OpError("target width/height must be positive")
    m = MARGIN_FRAC * min(target_w, target_h)
    gap = GAP_FRAC * min(target_w, target_h)
    footer_h = FOOTER_FRAC * target_h
    footer = _rect(m, m, target_w - 2 * m, footer_h)
    upper_y0 = m + footer_h + gap
    upper_h = target_h - m - upper_y0
    # upper_h is a fixed fraction of target_h (margin/gap/footer are all fractions of min(target_w,
    # target_h) <= target_h), so it cannot go non-positive for any target_w, target_h > 0 - already
    # checked above; each _*_zones helper's own docstring shows why its ZONES can't degenerate either.

    r = target_w / target_h
    if r >= WIDE_RATIO:
        zones = _wide_zones(m, gap, target_w, upper_y0, upper_h)
    elif r >= GRID_RATIO:
        zones = _grid_zones(m, gap, target_w, upper_y0, upper_h)
    else:
        zones = _stack_zones(m, gap, target_w, upper_y0, upper_h)
    zones[ZONE_FOOTER] = footer
    return zones


def _fill_frame(frame: dict) -> dict:
    """`frame` inset by ZONE_PADDING_FRAC of ITS OWN width/height, PER AXIS, and returned AS-IS - no
    aspect-ratio preservation. Used for the four named zones' own content (header/product/main_text/
    footer groups): these are logo/text clusters being repositioned into a purpose-built rectangle, not
    a photographic asset that would look wrong distorted, so `scene_ops._scale`'s existing independent
    x/y scale factors (see CLAUDE.md "Orientation adaptation" - resize already supports non-uniform
    scaling, nothing new was needed for it) are used directly to occupy the FULL zone bounds, rather
    than `pe.aspect_fit(..., fit="contain")`'s uniform scale - which centres the content at whichever
    axis is more constraining and leaves the other axis's padding unused. Found live on the AL MADEENA
    board: a 36x96in (portrait) conversion under the old `contain` fit used only ~43% of the page's
    vertical extent, with every zone's content clustered around its own centre; a 240x36in
    (ultra-wide) conversion left header/main_text content narrow inside their own wide columns instead
    of spanning them.

    Padding is deliberately computed per-axis (ZONE_PADDING_FRAC of THAT axis's own size) rather than
    a single value derived from the zone's smaller dimension applied to both: an earlier version did
    the latter and, for a zone much taller than it is wide (e.g. the stack template's header/product),
    that meant the padding was set by the SMALL width and then also subtracted twice from the LARGE
    height - wasting real vertical space on an inset sized for the other axis entirely. `
    ZONE_PADDING_FRAC` itself was also reduced from an initial 0.06 to 0.03 once the per-axis fix alone
    (84.0% zone-level vertical utilization on the 36x96in AL MADEENA conversion) still fell short of the
    >85% target - each of the 4 stacked zones+footer compounds its own padding on top of the small
    inter-zone margin/gap, so 5 zones x 2 edges x a padding fraction adds up fast; 0.03 brought the same
    live conversion to 89.8% (see CLAUDE.md "Orientation adaptation" for the exact live numbers).
    ZONE_PADDING_FRAC=0.03 keeps `frame["w"]*(1-2*0.03)` and `frame["h"]*(1-2*0.03)` positive for any
    positive frame - the same non-degeneracy guarantee documented on each `_*_zones` helper already
    keeps every zone's own w/h a positive fraction of the target page. Still not used for the page
    BACKGROUND placement below, which keeps `pe.aspect_fit(..., fit="cover")` specifically to avoid
    distorting the real photographic/graphic content a background PowerClip usually holds."""
    pad_x, pad_y = ZONE_PADDING_FRAC * frame["w"], ZONE_PADDING_FRAC * frame["h"]
    return _rect(frame["x"] + pad_x, frame["y"] + pad_y, frame["w"] - 2 * pad_x, frame["h"] - 2 * pad_y)


def _contains_bitmap(node: dict) -> bool:
    """True if `node` itself, or anything nested inside it (a group's children, a PowerClip's
    contents), is a raster shape (`product_engine.is_bitmap`) - used to decide whether a zone's
    content may be safely non-uniformly stretched (`_fill_frame`, fine for vector curves and text,
    where CorelDRAW re-renders crisp geometry from the updated bounding box - see `scene_ops._scale`)
    or must instead be scaled UNIFORMLY (`pe.aspect_fit(..., fit="contain")`, same scale factor on
    both axes) to avoid visibly stretching/blurring a photographic or textured raster - a product
    photo, a table/pedestal surface bitmap, a PowerClip that clips one. A composite group (e.g. a
    product image grouped with its table/pedestal surface) is walked recursively so ANY bitmap
    anywhere inside it - not just a top-level one - forces the whole group to scale uniformly, since
    `scene_ops._op_resize` maps one `from`/`to` box onto every id in an op with a single sx/sy pair;
    there is no way to stretch a sibling text label while keeping a nested photo undistorted within
    the same op, so the safer (non-distorting) scaling wins for the whole group."""
    if pe.is_bitmap(node):
        return True
    return any(_contains_bitmap(c) for c in node.get("children") or [])


def _zone_fit(idx: dict, ids: list[str], frm: dict, frame: dict) -> dict:
    """The `to` box for a zone's content: `pe.aspect_fit(..., fit="contain")` (uniform scale, centred
    - never truncates/overflows the frame, which is what "Table Surface & Assembly Anchoring" asks
    for when a product image is grouped with a table/pedestal surface) if ANY of `ids` contains a
    bitmap anywhere in its subtree (see `_contains_bitmap`), else `_fill_frame` (non-uniform stretch
    to occupy the full zone bounds - safe for vector/text-only content, see its own docstring)."""
    if any(_contains_bitmap(idx[i]["node"]) for i in ids):
        pad = ZONE_PADDING_FRAC * min(frame["w"], frame["h"])
        return pe.aspect_fit(frm["w"], frm["h"], frame, fit="contain", padding=pad)
    return _fill_frame(frame)


def _layer_top_ids(scene: dict) -> list[str]:
    return [n["id"] for layer in scene["layers"] for n in layer["children"]]


def _topmost_top_level_ancestor(idx: dict, node_id: str) -> str:
    """Walks up from `node_id` to the OUTERMOST ancestor that is still a direct child of a layer (a
    top-level shape): a bare bitmap resolves to itself, one nested in a PowerClip or an ordinary group
    resolves to that PowerClip/group's own id, and a bitmap nested several levels deep (e.g. a group
    inside a PowerClip) resolves to the outermost one - so the whole composite moves/scales as ONE
    rigid unit. This generalizes the previous `slot.container_id` (which only ever named the nearest
    POWERCLIP ancestor, per `product_engine.clip_ancestor`) to also cover a product image grouped with
    a plain `group` - e.g. a table/pedestal-surface bitmap sitting alongside it with no PowerClip
    involved (the "Table Surface & Assembly Anchoring" case: without this, only the tagged/heuristic
    -matched bitmap itself would move into the product zone, leaving its table surface behind)."""
    top = node_id
    p = idx[node_id]["parent"]
    while p is not None:
        top = p["id"]
        p = idx[p["id"]]["parent"]
    return top


def _slot_top_id(idx: dict, slot: pe.ProductSlot) -> str:
    return _topmost_top_level_ancestor(idx, slot.container_id or slot.node_id)


def _union_box(idx: dict, ids: list[str]) -> dict:
    nodes = [idx[i]["node"] for i in ids]
    x0 = min(n["x"] for n in nodes)
    y0 = min(n["y"] for n in nodes)
    x1 = max(n["x"] + n["w"] for n in nodes)
    y1 = max(n["y"] + n["h"] for n in nodes)
    return _rect(x0, y0, x1 - x0, y1 - y0)


def _split_evenly(items: list, n: int) -> list[list]:
    """`items` split into `n` contiguous chunks, as evenly sized as possible (a chunk can be empty
    if there are fewer items than n) - any remainder goes to the FIRST chunks, deterministically."""
    if n <= 0:
        return []
    k, m = divmod(len(items), n)
    out = []
    start = 0
    for i in range(n):
        size = k + (1 if i < m else 0)
        out.append(items[start:start + size])
        start += size
    return out


def _split_by_capacity(items: list, weights: list[float], capacities: list[float]) -> list[list]:
    """`items` (already ordered top-to-bottom, matching `capacities`' own top-to-bottom zone order)
    split into `len(capacities)` CONTIGUOUS, order-preserving buckets choosing whichever cut points
    minimize the WORST per-bucket overflow ratio (`bucket_weight / that_zone's_capacity`) - unlike
    `_split_evenly` (equal HEAD COUNT per bucket, regardless of how big any one item actually is),
    which does not know or care how large an individual leftover shape's own original footprint was.

    Found live on a real, untagged master (job 8a41177716c4, shop fe047cace239 - a DARSHAN AGARBATHI
    board): `_split_evenly` put a 303x562mm badge/text group (`s5` - a full secondary "BLACK STONE"
    assembly, comparable in size to the page's own product composite) into the stack template's
    `main_text` zone purely because it was the 3rd-from-top of 5 leftover shapes in a 3-zone split -
    `main_text` is a deliberately small sliver (see `_stack_zones`'s own 0.06-of-`avail` fraction,
    sized for a short text label), so the group was scaled down to 40.7mm tall, 13.8x smaller than its
    original height - not a distortion (still vector/text, losslessly re-rendered) but functionally
    destroyed as legible content, which is exactly the "position secondary accents ... without leaving
    wide gaps" the fix is for: the gap wasn't between shapes, it was between one shape's real size and
    the zone it got shoved into. A first attempt (assigning each item to whichever zone's cumulative
    -capacity range contained its own cumulative-weight midpoint) did not fully fix this: a single
    large ATOMIC item (a group can't be split across two zones) can still land in a bucket smaller
    than itself if that is simply where its position falls in the top-to-bottom order - proportional
    weight alone doesn't help an item that IS the majority of the "other" list's total weight.

    Solved properly via dynamic programming over cut points (`items` is always small - a handful of
    leftover shapes - so this is cheap): `best[b][i]` is the minimum possible worst overflow ratio
    achievable by partitioning the first `i` items into the first `b` buckets (capacities 0..b-1);
    the transition tries every possible previous cut point `j` and takes
    `max(best[b-1][j], (W[i]-W[j]) / capacities[b-1])`, i.e. a bucket's own ratio can only make the
    OVERALL worst ratio same or higher, never lower - so this always finds the ordering-preserving
    split that comes closest to fitting everything into its own zone, moving a big item to whichever
    zone (still respecting original top-to-bottom order) gives it the most room, rather than
    whichever zone its position happens to fall into. Falls back to `_split_evenly` if there is
    nothing to weigh by (all weights zero) or nothing to weigh against (every capacity zero) - both
    degenerate cases where an optimal split carries no more information than an even one."""
    n_items = len(items)
    k = len(capacities)
    if k <= 0:
        return []
    if n_items == 0:
        return [[] for _ in range(k)]
    if sum(weights) <= 0 or sum(capacities) <= 0:
        return _split_evenly(items, k)

    prefix = [0.0] * (n_items + 1)
    for i, w in enumerate(weights):
        prefix[i + 1] = prefix[i] + w

    INF = float("inf")
    best = [[INF] * (n_items + 1) for _ in range(k + 1)]
    best[0][0] = 0.0
    cut = [[0] * (n_items + 1) for _ in range(k + 1)]
    for b in range(1, k + 1):
        cap = capacities[b - 1]
        for i in range(n_items + 1):
            for j in range(i + 1):
                prev = best[b - 1][j]
                if prev == INF:
                    continue
                seg_w = prefix[i] - prefix[j]
                ratio = 0.0 if seg_w <= 0 else (INF if cap <= 0 else seg_w / cap)
                val = max(prev, ratio)
                if val < best[b][i]:
                    best[b][i] = val
                    cut[b][i] = j

    out: list[list] = [[] for _ in range(k)]
    i = n_items
    for b in range(k, 0, -1):
        j = cut[b][i]
        out[b - 1] = items[j:i]
        i = j
    return out


def _reclaim_empty_absorbing_frames(absorbing: list[str], buckets: list[list], frames: dict) -> list[dict]:
    """One frame per (zone, bucket) pair in `absorbing`/`buckets` order - a bucket that ended up EMPTY
    (see `_split_by_capacity`'s own docstring: a zone can legitimately get nothing when giving it
    content would create a worse overflow elsewhere) has its frame folded into the next zone BELOW it
    that DOES have content, extending that zone's frame to also cover the empty one's slot - so the
    freed space is actually filled by real content instead of staying a permanent blank strip (found
    live: leaving `main_text` empty on the DARSHAN AGARBATHI board, see `_split_by_capacity`'s
    docstring, would otherwise show as a bare 43mm gap in the middle of the page even though the
    crushing problem was fixed). Only merges when the two frames share the same `x`/`w` - true for
    every stacked absorbing zone in the stack template (`_stack_zones` gives header/main_text/footer
    the identical `x`/`w`) - so a grid/wide template's differently-sized main_text column is left
    alone rather than risk stretching a merged frame sideways into a third zone's space it was never
    entitled to. A trailing empty zone with no non-empty zone below it keeps its own frame (nothing to
    reasonably reclaim it into)."""
    out: list[dict | None] = [None] * len(absorbing)
    pending: list[int] = []
    for i, (zone, bucket) in enumerate(zip(absorbing, buckets)):
        frame = frames[zone]
        if not bucket:
            pending.append(i)
            continue
        for p in pending:
            pf = frames[absorbing[p]]
            if pf["x"] == frame["x"] and pf["w"] == frame["w"]:
                y0 = min(pf["y"], frame["y"])
                y1 = max(pf["y"] + pf["h"], frame["y"] + frame["h"])
                frame = _rect(frame["x"], y0, frame["w"], y1 - y0)
        pending = []
        out[i] = frame
    for i in range(len(out)):
        if out[i] is None:
            out[i] = frames[absorbing[i]]
    return out


def _is_locked(idx: dict, node_id: str) -> bool:
    e = idx[node_id]
    return bool(e["node"].get("locked")) or bool(e["layer"].get("locked"))


def classify_zones(scene: dict) -> tuple[dict[str, list[str]], list[str]]:
    """Which top-level shape ids belong to each zone (see the module docstring), plus warnings -
    product_engine.map_slots's own warnings (a tag that couldn't become a slot), and one line per
    shape a zone would have moved/resized but which is locked (or on a locked layer) and so was left
    out of `convert_orientation`'s ops instead.

    Background is decided FIRST, from each top-level shape's OWN geometry, before any slot is
    considered - not merely "whatever a zone didn't already claim". A real, untagged master's whole
    board is often one page-sized PowerClip (see CLAUDE.md "Designer dataset analysis" - real masters
    are untagged) that happens to also contain a modest-sized bitmap product_engine's heuristic alone
    would call a `product_image` slot; deciding background second would then squeeze the ENTIRE
    board's artwork into a small product-column rectangle because a small bitmap somewhere inside it
    looked plausible in isolation. Found live on a real generated board (job 16bfc025ca11): the
    heuristic slot for a small bitmap nested in a page-sized PowerClip pulled the whole container into
    the `product` zone instead of `background`. A slot whose top id is already claimed as background
    this way is dropped, not reassigned - its content is handled wholesale by the background stretch."""
    idx = scene_ops._index(scene)
    page_w, page_h = float(scene["page"]["width"]), float(scene["page"]["height"])
    page_area = page_w * page_h
    top_ids = _layer_top_ids(scene)

    def is_page_covering(tid: str) -> bool:
        node = idx[tid]["node"]
        return page_area > 0 and node["w"] * node["h"] >= pe.BG_AREA_RATIO * page_area

    zones: dict[str, list[str]] = {z: [] for z in ZONES}
    used: set[str] = set()
    for tid in top_ids:
        if idx[tid]["node"].get("visible") is not False and is_page_covering(tid):
            zones[ZONE_BACKGROUND].append(tid)
            used.add(tid)

    slots, warnings = pe.map_slots(scene)
    for slot in slots:
        top_id = _slot_top_id(idx, slot)
        if top_id in used:
            continue     # its visible content is inside a shape already claimed as the page background
        zone = _KIND_TO_ZONE[slot.kind]
        if top_id not in zones[zone]:
            zones[zone].append(top_id)
        used.add(top_id)

    for tid in top_ids:
        if tid in used:
            continue
        node = idx[tid]["node"]
        if node.get("visible") is False:
            continue
        used.add(tid)
        zones[ZONE_OTHER].append(tid)

    for zone in (ZONE_HEADER, ZONE_PRODUCT, ZONE_MAIN_TEXT, ZONE_FOOTER, ZONE_BACKGROUND):
        locked = [i for i in zones[zone] if _is_locked(idx, i)]
        for i in locked:
            warnings.append(f"{i} ({zone}) is locked - left as-is by orientation conversion")
    return zones, warnings


def convert_orientation(scene: dict, target_w: float, target_h: float) -> list[dict]:
    """A self-contained op list (a `page` op, then one `resize` op per non-empty zone) that re-lays
    `scene` out for a `target_w` x `target_h` mm page - apply with `scene_ops.apply_ops(scene, ops)`
    exactly like any other op list. Never includes a locked shape (or one on a locked layer) in a
    zone's ids, so the returned ops always apply cleanly; call `classify_zones` first if you need to
    know what was classified where, or what was skipped."""
    idx = scene_ops._index(scene)
    zones, _ = classify_zones(scene)
    zones = dict(zones)  # about to redistribute `other` below - classify_zones' own dict is not touched
    frames = calculate_zone_rects(target_w, target_h)
    page_w, page_h = float(scene["page"]["width"]), float(scene["page"]["height"])
    fit_scale = min(target_w / page_w, target_h / page_h) if page_w > 0 and page_h > 0 else 1.0

    ops: list[dict] = [{"op": "page", "width": _r(target_w), "height": _r(target_h)}]

    # A real, untagged master (CLAUDE.md "Designer dataset analysis") has no brand_title/
    # product_title/address/contact slots at all, so header/main_text/footer can end up with nothing
    # assigned - leaving that part of the template blank while the master's actual content (logos,
    # shop name, badges, ...) sits in the weak, position-preserving `other` fallback below. Found
    # live on the AL MADEENA board: converting to a tall (36x96in) target left over half the page's
    # height as bare template with the real content scattered into tiny, randomly-placed fragments.
    #
    # `other` shapes are bucketed by their ORIGINAL proportional vertical position into however many
    # named zones are completely empty, top-to-bottom - matching each empty zone's own position on
    # the new page, since header/main_text/footer are top-to-bottom in every template (see the module
    # docstring's table; product is deliberately excluded here - in the wide template it is a full
    # -height side column, not comparable to the others by vertical position). Each bucket is fit as
    # its own rigid unit into its own zone, not merged into one - found live that a single merged
    # blob crammed a shop-name text that sat near the BOTTOM of the original page against a logo
    # badge that sat near the TOP, because both landed in one group despite being far apart
    # originally. Only fires when a zone is COMPLETELY empty, so a properly slot-tagged master (this
    # tool's own future masters - see "Product slots") is unaffected: its header/main_text/footer are
    # never empty to begin with.
    absorbing = sorted((z for z in (ZONE_HEADER, ZONE_MAIN_TEXT, ZONE_FOOTER) if not zones[z]),
                       key=lambda z: -frames[z]["y"])                 # top zone first
    other_ids = [i for i in zones[ZONE_OTHER] if not _is_locked(idx, i)]
    if other_ids and absorbing:
        other_sorted = sorted(other_ids, key=lambda i: -(idx[i]["node"]["y"] + idx[i]["node"]["h"] / 2))
        weights = [idx[i]["node"]["h"] for i in other_sorted]
        capacities = [frames[z]["h"] for z in absorbing]
        buckets = _split_by_capacity(other_sorted, weights, capacities)
        reclaimed_frames = _reclaim_empty_absorbing_frames(absorbing, buckets, frames)
        placed: set[str] = set()
        for zone, bucket, frame in zip(absorbing, buckets, reclaimed_frames):
            if not bucket:
                continue
            frm = _union_box(idx, bucket)
            to = _zone_fit(idx, bucket, frm, frame)
            ops.append({"op": "resize", "ids": bucket, "from": frm, "to": to})
            placed.update(bucket)
        zones[ZONE_OTHER] = [i for i in zones[ZONE_OTHER] if i not in placed]  # the rest keep the fallback below

    for zone in (ZONE_HEADER, ZONE_PRODUCT, ZONE_MAIN_TEXT, ZONE_FOOTER):
        ids = [i for i in zones[zone] if not _is_locked(idx, i)]
        if not ids:
            continue
        frame = frames[zone]
        frm = _union_box(idx, ids)
        to = _zone_fit(idx, ids, frm, frame)
        ops.append({"op": "resize", "ids": ids, "from": frm, "to": to})

    for i in zones[ZONE_BACKGROUND]:
        if _is_locked(idx, i):
            continue
        node = idx[i]["node"]
        frm = _rect(node["x"], node["y"], node["w"], node["h"])
        # A "cover" fit (uniform scale, centred, no distortion) rather than an exact non-uniform
        # stretch: layout.py's own `bg` role always stretches exactly, correct for a plain texture,
        # but classify_zones's background-precedence rule (see its own docstring) can make a real
        # master's WHOLE composed PowerClip - logos and packaging included - the "background" when
        # it happens to cover most of the page. An exact stretch then visibly squishes every shape
        # nested inside it; found live on the AL MADEENA board's tall and ultra-wide targets. `cover`
        # still covers every mm of the new page (the same full-canvas coverage an exact stretch
        # gives - it only ever grows past the page edges, never short of them) without distorting the
        # content's own proportions, at the honest cost of cropping whatever overflows the new page.
        to = pe.aspect_fit(frm["w"], frm["h"], _rect(0, 0, target_w, target_h), fit="cover")
        ops.append({"op": "resize", "ids": [i], "from": frm, "to": to})

    for i in zones[ZONE_OTHER]:
        if _is_locked(idx, i):
            continue
        node = idx[i]["node"]
        frm = _rect(node["x"], node["y"], node["w"], node["h"])
        cx, cy = (node["x"] + node["w"] / 2) / page_w, (node["y"] + node["h"] / 2) / page_h
        w, h = node["w"] * fit_scale, node["h"] * fit_scale
        to = _rect(cx * target_w - w / 2, cy * target_h - h / 2, w, h)
        ops.append({"op": "resize", "ids": [i], "from": frm, "to": to})

    return ops
