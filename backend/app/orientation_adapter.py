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
ZONE_PADDING_FRAC = 0.06    # of the zone's own smaller dimension - keeps content off the zone edges

# Aspect-ratio thresholds (R = target_w / target_h) selecting which template calculate_zone_rects
# uses for the area above the footer banner - see the module docstring's table.
WIDE_RATIO = 2.0
GRID_RATIO = 1.0


def _r(v: float) -> float:
    return round(float(v), scene_ops.ROUND)


def _rect(x: float, y: float, w: float, h: float) -> dict:
    return {"x": _r(x), "y": _r(y), "w": _r(w), "h": _r(h)}


def _wide_zones(m: float, gap: float, target_w: float, upper_y0: float, upper_h: float) -> dict[str, dict]:
    """R >= WIDE_RATIO: a left product column at PRODUCT_COL_FRAC of the FULL target width (so it
    keeps growing with target_w, unlike the grid template's column, which is why this template is
    reserved for wide-enough targets - see calculate_zone_rects), header stacked above main_text in a
    right-hand text column. product_w < target_w - m always (PRODUCT_COL_FRAC=0.32 < 1), so text_w =
    target_w - m - (m+product_w+gap) is positive whenever target_w > 2m + product_w + gap; since m and
    gap are fractions of min(target_w, target_h) <= target_w and product_w = 0.32*target_w, this holds
    for every target_w > 0 once the fixed fractions (0.06+0.32+0.02 = 0.40 of target_w, at most) are
    accounted for - true across the whole WIDE_RATIO domain, verified by
    test_calculate_zone_rects_never_degenerates_across_a_wide_range_of_aspect_ratios."""
    product_w = PRODUCT_COL_FRAC * target_w
    product = _rect(m, upper_y0, product_w, upper_h)
    text_x0 = m + product_w + gap
    text_w = target_w - m - text_x0
    header_h = HEADER_OF_UPPER_FRAC * upper_h
    header = _rect(text_x0, upper_y0 + upper_h - header_h, text_w, header_h)
    main_text = _rect(text_x0, upper_y0, text_w, upper_h - header_h - gap)
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
    positive - see calculate_zone_rects)."""
    avail = upper_h - 2 * gap
    header_h = 0.18 * avail
    product_h = 0.55 * avail
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


def _layer_top_ids(scene: dict) -> list[str]:
    return [n["id"] for layer in scene["layers"] for n in layer["children"]]


def _slot_top_id(slot: pe.ProductSlot) -> str:
    return slot.container_id or slot.node_id


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
        top_id = _slot_top_id(slot)
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
        placed: set[str] = set()
        for zone, bucket in zip(absorbing, _split_evenly(other_sorted, len(absorbing))):
            if not bucket:
                continue
            frame = frames[zone]
            pad = ZONE_PADDING_FRAC * min(frame["w"], frame["h"])
            frm = _union_box(idx, bucket)
            to = pe.aspect_fit(frm["w"], frm["h"], frame, fit="contain", padding=pad)
            ops.append({"op": "resize", "ids": bucket, "from": frm, "to": to})
            placed.update(bucket)
        zones[ZONE_OTHER] = [i for i in zones[ZONE_OTHER] if i not in placed]  # the rest keep the fallback below

    for zone in (ZONE_HEADER, ZONE_PRODUCT, ZONE_MAIN_TEXT, ZONE_FOOTER):
        ids = [i for i in zones[zone] if not _is_locked(idx, i)]
        if not ids:
            continue
        frame = frames[zone]
        pad = ZONE_PADDING_FRAC * min(frame["w"], frame["h"])
        frm = _union_box(idx, ids)
        to = pe.aspect_fit(frm["w"], frm["h"], frame, fit="contain", padding=pad)
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
