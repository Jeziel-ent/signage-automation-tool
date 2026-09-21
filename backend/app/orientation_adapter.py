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

    zone         slot kinds                    landscape placement
    header       brand_title                    top of the right-hand text column
    product      product_image (incl. nested    left column, aspect-fit scaled
                 inside a PowerClip)
    main_text    product_title                   middle of the right-hand text column
    footer       address, contact                a horizontal banner across the full width at the
                                                  bottom - the same "shop name belongs in the bottom
                                                  bar" placement CLAUDE.md's wide-board work already
                                                  established for contact-like text, reused here
                                                  rather than invented fresh
    background   any unslotted top-level shape   stretched to exactly fill the new page (bg role,
                 covering >= product_engine.       see layout.py's own `bg` role)
                 BG_AREA_RATIO of the page area
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

# Template proportions (fractions of the target page), tuned by eye for a wide landscape target -
# see the module docstring for why the portrait fallback below is the less-tested path.
MARGIN_FRAC = 0.03
GAP_FRAC = 0.02
FOOTER_FRAC = 0.20          # of target height
PRODUCT_COL_FRAC = 0.32     # of target width, landscape template only
HEADER_OF_UPPER_FRAC = 0.35  # of the upper (non-footer) area's height, landscape template only
ZONE_PADDING_FRAC = 0.06    # of the zone's own smaller dimension - keeps content off the zone edges


def _r(v: float) -> float:
    return round(float(v), scene_ops.ROUND)


def _rect(x: float, y: float, w: float, h: float) -> dict:
    return {"x": _r(x), "y": _r(y), "w": _r(w), "h": _r(h)}


def zone_frames(target_w: float, target_h: float) -> dict[str, dict]:
    """The four named zones' rectangles (mm) on a page of `target_w` x `target_h`, disjoint by
    construction. Chooses a left-column-product / right-column-text template for a wide (landscape)
    target, or a simple top-to-bottom stack for a tall one - see the module docstring."""
    if target_w <= 0 or target_h <= 0:
        raise OpError("target width/height must be positive")
    m = MARGIN_FRAC * min(target_w, target_h)
    gap = GAP_FRAC * min(target_w, target_h)
    footer_h = FOOTER_FRAC * target_h
    footer = _rect(m, m, target_w - 2 * m, footer_h)
    upper_y0 = m + footer_h + gap
    upper_h = target_h - m - upper_y0
    # upper_h/text_w etc. are all fixed proportions of target_w/target_h by construction (the
    # margin/gap/footer fractions are relative to the SAME dimensions they're subtracted from), so
    # they cannot go non-positive for any target_w, target_h > 0 - already checked above.

    if target_w >= target_h:
        product_w = PRODUCT_COL_FRAC * target_w
        product = _rect(m, upper_y0, product_w, upper_h)
        text_x0 = m + product_w + gap
        text_w = target_w - m - text_x0
        header_h = HEADER_OF_UPPER_FRAC * upper_h
        header = _rect(text_x0, upper_y0 + upper_h - header_h, text_w, header_h)
        main_text = _rect(text_x0, upper_y0, text_w, upper_h - header_h - gap)
    else:
        avail = upper_h - 2 * gap
        header_h = 0.18 * avail
        product_h = 0.55 * avail
        main_h = avail - header_h - product_h
        top = upper_y0 + upper_h
        header = _rect(m, top - header_h, target_w - 2 * m, header_h)
        product = _rect(m, top - header_h - gap - product_h, target_w - 2 * m, product_h)
        main_text = _rect(m, upper_y0, target_w - 2 * m, main_h)

    return {ZONE_HEADER: header, ZONE_PRODUCT: product, ZONE_MAIN_TEXT: main_text, ZONE_FOOTER: footer}


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
    frames = zone_frames(target_w, target_h)
    page_w, page_h = float(scene["page"]["width"]), float(scene["page"]["height"])
    fit_scale = min(target_w / page_w, target_h / page_h) if page_w > 0 and page_h > 0 else 1.0

    ops: list[dict] = [{"op": "page", "width": _r(target_w), "height": _r(target_h)}]

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
        to = _rect(0, 0, target_w, target_h)
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
