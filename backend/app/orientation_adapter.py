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

import contextvars
import re

from . import product_engine as pe
from . import scene_ops
from .scene_ops import OpError

# Tamil Unicode block (U+0B80-U+0BFF) - used to tell an English text shape from a Tamil one for the
# footer banner's English/Tamil split (see _place_footer_banner). This is a content check, not a
# font/language-tag check - CLAUDE.md ("Shop name replacement") already documents that this codebase
# reads a shape's actual text, never trusts its declared font name, for exactly this kind of decision.
_TAMIL_RE = re.compile(r"[஀-௿]")

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
FOOTER_FRAC = 0.20          # of target height (wide/grid templates)
FOOTER_FRAC_PORTRAIT = 0.20  # of target height (stack template only, R < GRID_RATIO): the heavy anchor bar
# Stack (portrait) template: normalized vertical bands of the canvas height (0 = bottom, 1 = top), the
# same for EVERY aspect ratio < 1.0 (nothing here is tuned to one page size). Zones are these bands
# inset by margin/gap, see _stack_bands.
STACK_BAND_PRODUCT = (FOOTER_FRAC_PORTRAIT, 0.55)    # tables/pedestals/product boxes, base right above the footer
STACK_BAND_BRAND = (0.55, 0.72)                      # central brand logo: a "roof" strictly above the products
STACK_BAND_HEADER = (0.72, 0.98)                     # top-left / top-right corner badges
# Untagged shapes on a landscape source's upper-right quadrant are promoted to the header (see
# convert_orientation): source centre-y > PROMOTE_MIN_CY, badge-sized, and lying wholly in the right half
# (left edge >= PROMOTE_MIN_CX). A plain "centre-x > 0.5" test was tried first and promoted a central brand
# logo whose centre sat at x = 0.525 into the header corner - the quadrant test must exclude shapes that
# straddle the centre line.
PROMOTE_MIN_CY = 0.50
PROMOTE_MIN_CX = 0.50
PROMOTE_MAX_H_FRAC = 0.40
PORTRAIT_FILL_BOOST = 1.3        # main object's scale boost in the portrait product/branding zones
PORTRAIT_MAIN_FRAC_MAX = 0.85    # never let the main column take more than this share of the zone width
# Portrait corner badges snap to fixed points of the CANVAS (not of the header frame): centres at 18% / 82% of the
# width, tops at 92% of the height.
BADGE_LEFT_CX, BADGE_RIGHT_CX, BADGE_TOP_Y = 0.18, 0.82, 0.92
# Footer text (shop name, contact lines) occupies this share of the footer band's height: big enough to read from a
# distance, small enough not to cross the band's edge (the 30x40 AL MADEENA render had it spilling over).
FOOTER_CONTENT_FRAC = 0.65   # a single text block that can fill it comes out at ~61% (the 3% fill padding). 0.73 was tried and
                             # reverted: it pushed a 10:1 footer line's stretch over MAX_STRETCH_RATIO at 24x36, and the uniform
                             # fallback then halved its height (0.68 -> 0.34 of the band). Very wide lines cannot reach 60-70%
                             # without being stretched.
# A bottom-anchored product composite may fill its zone but never more than 92% of the CANVAS width. The portrait
# product frame is (1 - 2*MARGIN_FRAC) = 94% of the canvas wide at every portrait ratio, so 92% of the canvas is this
# share of the frame. (A composite whose own aspect is too tall to reach it - e.g. DARSHAN's table, 0.46 - is
# height-limited long before: 85-92% of the width would need 4x the zone's height.)
PRODUCT_MAX_WIDTH_OF_FRAME = 0.92 / (1 - 2 * MARGIN_FRAC)
# Pedestal stage: a main product object whose group has a bottom SUPPORT element (a table/pedestal that is bottom
# aligned and spans most of the group's width) is widened toward PEDESTAL_TARGET_W_FRAC of the CANVAS width (the spec's
# range is 80-88%) - but only when that width still fits under the product stage's ceiling, and never by squeezing a
# flanking product below PEDESTAL_MIN_FLANK_FRAC of the frame width.
PEDESTAL_TARGET_W_FRAC = 0.80
PEDESTAL_MAX_W_FRAC = 0.88
PEDESTAL_MIN_FLANK_FRAC = 0.09   # 0.10 held the table at 78% of the canvas; 0.09 lets it reach 80% (the bottle ends ~55% of its old size)
SUPPORT_MIN_WIDTH_SHARE = 0.5     # the support element spans at least this share of the group's width ...
SUPPORT_BOTTOM_TOL = 0.03         # ... and its bottom edge is within this share of the group's height of the group's bottom
# Central branding roof: a LONE group in the branding zone may grow up to BRAND_MAX_WIDTH_FRAC of the zone's width, as far
# as it fits between the corner badges above and the products below without touching them (BRAND_CLEARANCE_FRAC of the
# canvas height).
BRAND_MAX_WIDTH_FRAC = 0.72
BRAND_CLEARANCE_FRAC = 0.012

# Portrait source -> wide target ("stage") template: normalized bands of the canvas height, identical for
# every wide ratio. Footer is a slim full-width bar; the product stage is one wide horizontal band whose bottom
# is the shared baseline; branding (a centred roof) and the header (far top-left / top-right badge slots) are
# two rows above it.
WIDE_STAGE_FOOTER = (0.0, 0.12)
WIDE_STAGE_PRODUCT = (0.12, 0.65)
WIDE_STAGE_BRAND = (0.65, 0.83)
WIDE_STAGE_HEADER = (0.83, 1.0)
UNSTACK_MIN_CLEARANCE_FRAC = 0.02   # of the stage width: minimum gap between neighbouring products
UNSTACK_SAME_COLUMN_OVERLAP = 0.5   # two products whose x-ranges overlap this much (of the narrower) were stacked
CORNER_SLOT_FRAC = 0.25             # width share of each far-left / far-right badge slot in the header row
BRAND_SLOT_FRAC = 0.5               # width share of the centred branding slot
P2L_BADGE_MAX_H_FRAC = 0.20         # a promotable badge on a portrait source: <= 20% of its height ...
P2L_BADGE_MAX_W_FRAC = 0.50         # ... and <= 50% of its width, so a product/table is never taken for one


class Direction(tuple):
    """(source, target) orientation of a conversion: each 'portrait', 'landscape' or 'square'. Replaces the
    old single `portrait = target_w < target_h` flag, which said nothing about the source and so could not tell
    a landscape->portrait fold from a portrait->landscape unfold."""
    __slots__ = ()

    def __new__(cls, page_w, page_h, target_w, target_h):
        def kind(w, h):
            return "portrait" if w < h else "landscape" if w > h else "square"
        return super().__new__(cls, (kind(page_w, page_h), kind(target_w, target_h)))

    source = property(lambda self: self[0])
    target = property(lambda self: self[1])
    to_portrait = property(lambda self: self[1] == "portrait")
    portrait_to_wide = property(lambda self: self[0] == "portrait" and self[1] == "landscape")
    same_orientation = property(lambda self: self[0] == self[1])


LANDSCAPE_MASTER_MIN_RATIO = 1.25  # target width / height at or above which the LANDSCAPE master is used


def target_orientation(target_w: float, target_h: float) -> str:
    """Which dual-master template a target uses: 'landscape' when width / height >= LANDSCAPE_MASTER_MIN_RATIO (1.25),
    else 'portrait' - so square and near-square boards (5x5, 6x6, 7x6 ft, 60x75 in) come from the portrait master,
    which scales to them with far less aspect change than a wide master. Sizes arrive in mm, so a ratio is used rather
    than an equality test (48 in = 1219.1999999999998 mm but 4 ft = 1219.2 mm)."""
    if target_h <= 0:
        return "landscape"
    return "landscape" if target_w / target_h >= LANDSCAPE_MASTER_MIN_RATIO - 1e-9 else "portrait"


def select_master(target_w: float, target_h: float, landscape_id: str | None, portrait_id: str | None) -> tuple[str, str, bool]:
    """(orientation, master id, fallback) for a target size. The master whose orientation matches the target
    is used, so conversion never crosses orientations when a matching master exists; if only the OTHER
    orientation was uploaded it is used as a fallback (fallback=True) and the caller should say so. Raises
    OpError when neither id is given."""
    want = target_orientation(target_w, target_h)
    ids = {"landscape": landscape_id or None, "portrait": portrait_id or None}
    if ids[want]:
        return want, ids[want], False
    other = "portrait" if want == "landscape" else "landscape"
    if ids[other]:
        return other, ids[other], True
    raise OpError("no landscape or portrait master to convert from")
PRODUCT_COL_FRAC = 0.32     # of target width, wide template only
HEADER_OF_UPPER_FRAC = 0.35  # of the upper (non-footer) area's height, wide template only
GRID_HEADER_FRAC = 0.28     # of the upper area's height, grid template only
ZONE_PADDING_FRAC = 0.03    # of the zone's own width/height, per axis - keeps content off the zone edges
MAIN_OBJECT_FRAC = 0.5      # of a center-region frame's width - reserved for main_object, centred;
                            # the remainder is split evenly between the left/right subobject columns

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


def _stack_bands(m: float, gap: float, target_w: float, target_h: float) -> dict[str, dict]:
    """R < GRID_RATIO: all four zones from the normalized height bands (footer 0-0.20, product
    0.20-0.55, brand/main_text 0.55-0.72, header 0.72-0.98), identical fractions for every portrait
    aspect ratio. Each band is inset by gap/2 on a boundary it shares with a neighbour, by `m` at the page
    bottom (footer) and by `m` at the page top when the header band would come closer than that
    (0.98 leaves 0.02*target_h; the top is only pulled in further if m is larger).

    Non-degeneracy for ANY target_w < target_h (so min(target_w, target_h) = target_w < target_h):
    gap = 0.02*target_w < 0.02*target_h and m = 0.03*target_w < 0.03*target_h; the thinnest band
    (brand, 0.17*target_h) loses gap = <= 0.02*target_h, the footer (0.20) loses m + gap/2 <= 0.04*
    target_h, the header (0.26) loses at most gap/2 + m, so every height stays >= 0.13*target_h > 0."""
    h = target_h
    half = gap / 2
    fx, fw = m, target_w - 2 * m
    footer_y0, footer_y1 = m, STACK_BAND_PRODUCT[0] * h - half
    prod_y0, prod_y1 = STACK_BAND_PRODUCT[0] * h + half, STACK_BAND_PRODUCT[1] * h - half
    main_y0, main_y1 = STACK_BAND_BRAND[0] * h + half, STACK_BAND_BRAND[1] * h - half
    head_y0, head_y1 = STACK_BAND_HEADER[0] * h + half, min(STACK_BAND_HEADER[1] * h, h - m)
    return {
        ZONE_FOOTER: _rect(fx, footer_y0, fw, footer_y1 - footer_y0),
        ZONE_PRODUCT: _rect(fx, prod_y0, fw, prod_y1 - prod_y0),
        ZONE_MAIN_TEXT: _rect(fx, main_y0, fw, main_y1 - main_y0),
        ZONE_HEADER: _rect(fx, head_y0, fw, head_y1 - head_y0),
    }


def _wide_stage_bands(m: float, gap: float, target_w: float, target_h: float) -> dict[str, dict]:
    """Portrait source -> wide target: the four zones from the normalized bands (footer 0-0.12, product stage
    0.12-0.65, branding 0.65-0.83, header 0.83-1.0), each inset by gap/2 on a shared boundary and by `m` at the
    page bottom/top, all full width (margin to margin). For a wide target min(W, H) = H, so m = 0.03*H and
    gap = 0.02*H: the thinnest zone (footer, 0.12*H) keeps 0.12H - m - gap/2 = 0.08*H and the header
    0.17H - gap/2 - m = 0.13*H, so every rectangle is positive for ANY target_w > target_h, whatever the ratio."""
    H, half = target_h, gap / 2
    fx, fw = m, target_w - 2 * m
    return {
        ZONE_FOOTER: _rect(fx, m, fw, WIDE_STAGE_FOOTER[1] * H - half - m),
        ZONE_PRODUCT: _rect(fx, WIDE_STAGE_PRODUCT[0] * H + half, fw, (WIDE_STAGE_PRODUCT[1] - WIDE_STAGE_PRODUCT[0]) * H - gap),
        ZONE_MAIN_TEXT: _rect(fx, WIDE_STAGE_BRAND[0] * H + half, fw, (WIDE_STAGE_BRAND[1] - WIDE_STAGE_BRAND[0]) * H - gap),
        ZONE_HEADER: _rect(fx, WIDE_STAGE_HEADER[0] * H + half, fw, H - m - (WIDE_STAGE_HEADER[0] * H + half)),
    }


def calculate_zone_rects(target_w: float, target_h: float, portrait_source: bool = False) -> dict[str, dict]:
    """The four named zones' rectangles (mm) on a page of `target_w` x `target_h`, disjoint by
    construction, for ANY positive target_w/target_h - not just "typical" signage sizes. The footer
    is always a horizontal banner across the full width at the bottom; the aspect ratio R = target_w
    / target_h picks the template for the area above it (see the module docstring's table and each
    `_*_zones` helper's own docstring for why its geometry can't degenerate): `_wide_zones` for R >=
    WIDE_RATIO, `_grid_zones` for GRID_RATIO <= R < WIDE_RATIO, `_stack_bands` for R < GRID_RATIO. A wide
    target (R > 1) converted FROM a portrait master (`portrait_source=True`) uses `_wide_stage_bands` instead."""
    if target_w <= 0 or target_h <= 0:
        raise OpError("target width/height must be positive")
    m = MARGIN_FRAC * min(target_w, target_h)
    gap = GAP_FRAC * min(target_w, target_h)
    r = target_w / target_h
    if r < GRID_RATIO:
        return _stack_bands(m, gap, target_w, target_h)
    if portrait_source and target_w > target_h:
        return _wide_stage_bands(m, gap, target_w, target_h)
    footer_h = FOOTER_FRAC * target_h
    footer = _rect(m, m, target_w - 2 * m, footer_h)
    upper_y0 = m + footer_h + gap
    upper_h = target_h - m - upper_y0
    # upper_h is a fixed fraction of target_h (margin/gap/footer are all fractions of min(target_w,
    # target_h) <= target_h), so it cannot go non-positive for any target_w, target_h > 0 - already
    # checked above; each _*_zones helper's own docstring shows why its ZONES can't degenerate either.

    if r >= WIDE_RATIO:
        zones = _wide_zones(m, gap, target_w, upper_y0, upper_h)
    else:
        zones = _grid_zones(m, gap, target_w, upper_y0, upper_h)
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


# A vector/text group that is resized as ONE rigid unit into a zone frame used to be stretched
# non-uniformly to fill it. At extreme target ratios that is a large distortion (measured live: up to 12x on the
# real AL MADEENA / DARSHAN boards at 1:4, visibly warping Tamil glyphs and logos). The aspect stretch of such a
# group, max(scale_x / scale_y, scale_y / scale_x), is capped: above MAX_STRETCH_RATIO the group falls back to a
# uniform contain fit (scale = min(scale_x, scale_y)), centred in its frame (bottom-anchored on the product
# stage). Configurable per call via `convert_orientation(..., max_stretch=...)`.
MAX_STRETCH_RATIO = 2.0
FULL_BLEED_FRAC = 0.95          # a bare, childless shape spanning >= this share of the SOURCE page in either
                                # dimension is a full-bleed bar/panel and is exempt from the cap
_MAX_STRETCH_CTX: contextvars.ContextVar = contextvars.ContextVar("orientation_max_stretch", default=None)


class _Idx(dict):
    """`scene_ops._index` result that also remembers the SOURCE page size (`page` = (w, h)), which the
    full-bleed exemption of `_stretch_exempt` needs and `_zone_fit` otherwise has no way to know."""
    page = None


def _max_stretch() -> float:
    v = _MAX_STRETCH_CTX.get()
    return MAX_STRETCH_RATIO if v is None else float(v)


def _stretch_exempt(idx: dict, ids: list[str]) -> bool:
    """True when every id is something that is MEANT to stretch edge to edge: a plain rectangle (a solid colour
    panel) or, for a top-level shape, a bare childless non-text shape spanning >= FULL_BLEED_FRAC of the source page
    in either dimension (a full-bleed border bar). Anything with text or children - a logo, a text block, a group -
    is not exempt, and neither is a mixed group: one glyph-carrying member makes the whole rigid group subject to
    the cap. (The page-covering background container never reaches `_zone_fit`; it is cover-fit uniformly.)"""
    page = getattr(idx, "page", None)
    for i in ids:
        e = idx[i]
        n = e["node"]
        if n.get("children") or n.get("text"):
            return False
        if n.get("type") == "rectangle":
            continue
        if page and e.get("parent") is None and (n["w"] >= FULL_BLEED_FRAC * page[0] or n["h"] >= FULL_BLEED_FRAC * page[1]):
            continue
        return False
    return True


def _zone_fit(idx: dict, ids: list[str], frm: dict, frame: dict, anchor_bottom: bool = False,
              boost: float = 1.0) -> dict:
    """The `to` box for a zone's content: `pe.aspect_fit(..., fit="contain")` (uniform scale, centred
    - never truncates/overflows the frame, which is what "Table Surface & Assembly Anchoring" asks
    for when a product image is grouped with a table/pedestal surface) if ANY of `ids` contains a
    bitmap anywhere in its subtree (see `_contains_bitmap`), else `_fill_frame` (non-uniform stretch
    to occupy the full zone bounds - safe for vector/text-only content, see its own docstring).

    Vector/text groups are capped at MAX_STRETCH_RATIO of aspect distortion (see above): beyond it they get a
    uniform contain fit instead of filling the frame. Full-bleed bars and plain rectangles are exempt
    (`_stretch_exempt`).

    `anchor_bottom` (the portrait PRODUCT zone only): the box stands on the frame's bottom edge - the
    baseline just above the footer - instead of floating at the frame's vertical midpoint; horizontal
    centring is unchanged. A centred fit leaves a gap under a table/pedestal whenever the content is
    shorter than its zone (a wide table in a tall band), which reads as an object hovering over the
    footer bar.

    `boost` (> 1, bitmap content, only used together with `anchor_bottom`) enlarges the contain fit by up
    to that factor, bounded by the UNPADDED frame on both axes: the top edge can never pass the frame's top
    (which is below the branding band) and the width can never pass the frame's width, so it cannot collide
    with the zone above or its neighbours. A contain fit already saturates one axis of its frame, so the
    real headroom is only the padding that was reserved (~3-6%): the factor actually applied is
    min(boost, that headroom), never more. Objects that need a real 1.3x get it from a wider column instead
    (see `_place_zone_content`)."""
    if any(_contains_bitmap(idx[i]["node"]) for i in ids):
        pad = ZONE_PADDING_FRAC * min(frame["w"], frame["h"])
        fit_frame = frame
        if anchor_bottom:
            # standing on the frame's bottom edge: the bottom padding is dead space, only the top keeps its inset
            fit_frame = _rect(frame["x"], frame["y"] - pad, frame["w"], frame["h"] + pad)
        to = pe.aspect_fit(frm["w"], frm["h"], fit_frame, fit="contain", padding=pad)
        if anchor_bottom:
            w, h = to["w"], to["h"]
            if boost > 1.0 and w > 0 and h > 0:
                b = max(1.0, min(boost, frame["w"] * PRODUCT_MAX_WIDTH_OF_FRAME / w, frame["h"] / h))
                w, h = w * b, h * b
            return _rect(frame["x"] + (frame["w"] - w) / 2, frame["y"], w, h)
        return to
    to = _fill_frame(frame)
    if frm["w"] > 0 and frm["h"] > 0 and not _stretch_exempt(idx, ids):
        sx, sy = to["w"] / frm["w"], to["h"] / frm["h"]
        if sx > 0 and sy > 0 and max(sx / sy, sy / sx) > _max_stretch():
            # too distorting: uniform contain (the SMALLER factor on both axes), centred in the fill box, or
            # standing on the frame's bottom edge on the product stage
            k = min(sx, sy)
            w, h = frm["w"] * k, frm["h"] * k
            return _rect(to["x"] + (to["w"] - w) / 2, frame["y"] if anchor_bottom else to["y"] + (to["h"] - h) / 2, w, h)
    if anchor_bottom:
        return _rect(to["x"], frame["y"], to["w"], to["h"])
    return to


# --------------------------------------------------------------------------------------------------
# Structural wireframe sub-placement: within a zone's OWN frame (unchanged from calculate_zone_rects
# - none of the three templates' geometry is touched here), split that zone's assigned ids into named
# sub-roles - top-left/top-right logo, main_object/subobjects, English/Tamil footer text - and give
# each its own reserved bound, rather than always resizing a zone's whole content as one rigid union.
# A zone holding 0 or 1 id (still the common case for every currently-verified real board's product/
# main_text zone) reduces exactly to the previous single-group `_zone_fit` behaviour, so this is
# additive: it only changes anything once a zone actually holds more than one distinct piece of
# content to arrange, via a tag or absorption.

def _is_tamil_text(node: dict) -> bool:
    t = node.get("text")
    return bool(t) and bool(_TAMIL_RE.search(str(t.get("content") or "")))


def _split_left_right_by_x(idx: dict, ids: list[str]) -> tuple[list[str], list[str]]:
    """`ids` split into (left, right) by each shape's own centre-x relative to the GROUP's own mean
    centre-x (not the page's, so this works regardless of which template positioned the zone). Falls
    back to splitting the x-sorted id list at its midpoint index if every id lands on the same side of
    the mean (e.g. near-identical x's) - so "reserve bounds for top-left AND top-right" is still
    honoured with two genuinely non-empty sides whenever there are 2+ ids to place."""
    if len(ids) < 2:
        return list(ids), []
    order = sorted(ids, key=lambda i: idx[i]["node"]["x"] + idx[i]["node"]["w"] / 2)
    centers = [idx[i]["node"]["x"] + idx[i]["node"]["w"] / 2 for i in order]
    mid = sum(centers) / len(centers)
    left = [i for i, c in zip(order, centers) if c < mid]
    right = [i for i, c in zip(order, centers) if c >= mid]
    if not left or not right:
        cut = len(order) // 2
        left, right = order[:cut], order[cut:]
    return left, right


def _place_two_up(idx: dict, ids_a: list[str], ids_b: list[str], frame: dict, axis: str) -> list[dict]:
    """`axis="x"`: `ids_a` into the LEFT half of `frame`, `ids_b` into the RIGHT half (side by side -
    the landscape footer's English-left/Tamil-right layout, and every left/right logo or subobject
    column). `axis="y"`: `ids_a` into the TOP half (the higher-y half, this scene model's origin is
    bottom-left), `ids_b` into the BOTTOM half (stacked - the portrait footer's English-top/Tamil
    -bottom layout). A side with no ids is simply skipped - its bounds stay reserved and empty rather
    than letting the other side's content stretch into them, which is the entire point of giving each
    side its own dedicated half instead of one shared union box."""
    if axis == "x":
        half_w = frame["w"] / 2
        frame_a = _rect(frame["x"], frame["y"], half_w, frame["h"])
        frame_b = _rect(frame["x"] + half_w, frame["y"], frame["w"] - half_w, frame["h"])
    else:
        half_h = frame["h"] / 2
        frame_a = _rect(frame["x"], frame["y"] + half_h, frame["w"], frame["h"] - half_h)
        frame_b = _rect(frame["x"], frame["y"], frame["w"], half_h)
    ops: list[dict] = []
    for ids, subframe in ((ids_a, frame_a), (ids_b, frame_b)):
        if not ids:
            continue
        frm = _union_box(idx, ids)
        ops.append({"op": "resize", "ids": ids, "from": frm, "to": _zone_fit(idx, ids, frm, subframe)})
    return ops


def _place_top_region(idx: dict, ids: list[str], frame: dict) -> list[dict]:
    """The wireframe's "Top Region": reserves separate top-left/top-right bounds for 2+ logo objects
    (`_split_left_right_by_x` + `_place_two_up`). A single id fills the WHOLE header frame, exactly as
    before this task - a lone logo is not squeezed into just one half merely because there is no
    second one to pair it with."""
    if len(ids) <= 1:
        if not ids:
            return []
        frm = _union_box(idx, ids)
        return [{"op": "resize", "ids": ids, "from": frm, "to": _zone_fit(idx, ids, frm, frame)}]
    left, right = _split_left_right_by_x(idx, ids)
    return _place_two_up(idx, left, right, frame, axis="x")


def _place_main_and_subobjects(idx: dict, ids: list[str], frame: dict, main_frac: float = MAIN_OBJECT_FRAC,
                               anchor_bottom: bool = False, single_boost: float = 1.0, pedestal: bool = False) -> list[dict]:
    """The wireframe's "Center Region": the largest-by-area id becomes `main_object`, centred in a
    dedicated `MAIN_OBJECT_FRAC` of the frame's width; every other id (`subobjects`) flows into a LEFT
    or RIGHT column flanking it, grouped by which side of `main_object`'s own centre-x they originally
    sat on. With 0 or 1 id total - the common case for every currently-verified real board's product/
    main_text zone before absorption adds extras - this reduces exactly to the previous single-group
    `_zone_fit` behaviour (unchanged)."""
    if len(ids) <= 1:
        if not ids:
            return []
        frm = _union_box(idx, ids)
        return [{"op": "resize", "ids": ids, "from": frm,
                 "to": _zone_fit(idx, ids, frm, frame, anchor_bottom, single_boost)}]

    areas = {i: idx[i]["node"]["w"] * idx[i]["node"]["h"] for i in ids}
    main_id = max(areas, key=areas.get)
    rest = [i for i in ids if i != main_id]
    main_cx = idx[main_id]["node"]["x"] + idx[main_id]["node"]["w"] / 2
    left = [i for i in rest if idx[i]["node"]["x"] + idx[i]["node"]["w"] / 2 < main_cx]
    right = [i for i in rest if i not in left]

    main_w = frame["w"] * main_frac
    side_w = (frame["w"] - main_w) / 2
    left_frame = _rect(frame["x"], frame["y"], side_w, frame["h"])
    main_frame = _rect(frame["x"] + side_w, frame["y"], main_w, frame["h"])
    right_frame = _rect(frame["x"] + side_w + main_w, frame["y"], side_w, frame["h"])
    if pedestal and anchor_bottom and bool(left) != bool(right) and _has_bottom_support(idx[main_id]["node"]):
        # Pedestal stage: the flanking objects are all on ONE side, so the symmetric split wastes the other side. Give the
        # table composite the width it needs to reach PEDESTAL_TARGET_W_FRAC of the canvas (its column keeps the padding
        # `_zone_fit` will remove) and put the flanks in the remaining column on their own side - the source's own
        # arrangement (table on one side, bottle on the other). Only when that width is reachable under the stage's
        # ceiling; a taller composite (aspect too small) keeps the symmetric split.
        frm_main = _union_box(idx, [main_id])
        canvas_w = frame["w"] / (1 - 2 * MARGIN_FRAC)                     # portrait: the frame is the canvas minus 2 margins
        target_w = PEDESTAL_TARGET_W_FRAC * canvas_w
        if frm_main["h"] > 0 and target_w * frm_main["h"] / frm_main["w"] <= frame["h"]:
            pad = ZONE_PADDING_FRAC * min(target_w, frame["h"])
            col_w, clear = target_w + 2 * pad, 2 * pad
            flank_w = frame["w"] - col_w - clear
            floor_w = PEDESTAL_MIN_FLANK_FRAC * frame["w"]
            if flank_w < floor_w:                                          # never squeeze the flank below its floor
                flank_w = floor_w
                col_w = frame["w"] - clear - flank_w
            if col_w - 2 * pad >= 0.5 * canvas_w:
                flank_on_right = bool(right)
                m_x = frame["x"] if flank_on_right else frame["x"] + flank_w + clear
                f_x = frame["x"] + col_w + clear if flank_on_right else frame["x"]
                main_frame = _rect(m_x, frame["y"], col_w, frame["h"])
                left_frame = right_frame = _rect(f_x, frame["y"], flank_w, frame["h"])

    ops: list[dict] = []
    frm_main = _union_box(idx, [main_id])
    ops.append({"op": "resize", "ids": [main_id], "from": frm_main,
                "to": _zone_fit(idx, [main_id], frm_main, main_frame, anchor_bottom)})
    for col_ids, col_frame in ((left, left_frame), (right, right_frame)):
        if not col_ids:
            continue
        frm = _union_box(idx, col_ids)
        ops.append({"op": "resize", "ids": col_ids, "from": frm, "to": _zone_fit(idx, col_ids, frm, col_frame, anchor_bottom)})
    return ops


def _place_footer_banner(idx: dict, ids: list[str], frame: dict, portrait: bool,
                         content_frac: float | None = None) -> list[dict]:
    """The wireframe's "Bottom Region": splits `ids` by detected script (`_is_tamil_text`) and places
    English/Tamil STACKED (English top, Tamil bottom) for a portrait target, or SIDE BY SIDE (English
    left, Tamil right) for a landscape one. With no Tamil text present at all (every real board sampled
    so far - see CLAUDE.md "Shop name replacement"/"Per-shop content replacement") or only one distinct
    id, this reduces exactly to the previous single-group `_zone_fit` behaviour (unchanged).

    `content_frac` (portrait targets and the portrait->wide stage): the text occupies only that share of the band's
    height, vertically centred (`FOOTER_CONTENT_FRAC` = 65%), instead of the whole frame - legible from a distance
    but inside the band, not crossing its edge."""
    if content_frac:
        h = frame["h"] * content_frac
        frame = _rect(frame["x"], frame["y"] + (frame["h"] - h) / 2, frame["w"], h)
    tamil = [i for i in ids if _is_tamil_text(idx[i]["node"])]
    english = [i for i in ids if i not in tamil]
    if not tamil or not english:
        frm = _union_box(idx, ids)
        return [{"op": "resize", "ids": ids, "from": frm, "to": _zone_fit(idx, ids, frm, frame)}]
    axis = "y" if portrait else "x"
    return _place_two_up(idx, english, tamil, frame, axis=axis)


def _place_snapped_badges(idx: dict, ids: list[str], canvas: tuple, frame: dict) -> list[dict] | None:
    """Portrait header with 2+ objects: the left group is centred on X = BADGE_LEFT_CX * canvas width and the right
    group on BADGE_RIGHT_CX, both with their TOP edge on Y = BADGE_TOP_Y * canvas height (or the header frame's top,
    if that is lower). Each group is one rigid unit scaled UNIFORMLY (contain) into its slot - the slot reaches from the
    page margin to the mirrored point, so a wide badge can use up to ~36% of the width - and a group is never
    stretched. The split into left/right is the existing mean-centre-x rule."""
    W, H = canvas
    if len(ids) > 1:
        left, right = _split_left_right_by_x(idx, ids)
    else:
        # a LONE header badge snaps to the side of the source it sat on (left / right third); a badge in the
        # middle third, or one whose source page is unknown, keeps the centred header-frame fit
        page = getattr(idx, "page", None)
        if not page or not page[0]:
            return None
        n = idx[ids[0]]["node"]
        cx = (n["x"] + n["w"] / 2) / page[0]
        if cx < 1 / 3:
            left, right = list(ids), []
        elif cx > 2 / 3:
            left, right = [], list(ids)
        else:
            return None
    m = frame["x"]
    top = min(BADGE_TOP_Y * H, frame["y"] + frame["h"])
    slot_h = max(top - frame["y"], 1e-6)
    ops: list[dict] = []
    for group, cx in ((left, BADGE_LEFT_CX * W), (right, BADGE_RIGHT_CX * W)):
        if not group:
            continue
        half = min(cx - m, W - m - cx)
        slot = _rect(cx - half, frame["y"], 2 * half, slot_h)
        frm = _union_box(idx, group)
        ops.append({"op": "resize", "ids": group, "from": frm, "to": _contain_box(frm, slot, "center", "top")})
    return ops


def _has_bottom_support(node: dict) -> bool:
    """True if `node` is a group with a child that is bottom aligned to it and spans most of its width - the table/pedestal
    of a table-with-products composite (the real DARSHAN board's `s46`: its `s47` table bitmap is 100% of the width and sits on
    the group's bottom edge, the box and incense sticks stand on it)."""
    kids = node.get("children") or []
    if len(kids) < 2 or node["w"] <= 0 or node["h"] <= 0:
        return False
    return any(abs(c["y"] - node["y"]) <= SUPPORT_BOTTOM_TOL * node["h"] and c["w"] >= SUPPORT_MIN_WIDTH_SHARE * node["w"]
               for c in kids)


def _boost_brand_roof(idx: dict, ids: list[str], frame: dict, canvas: tuple, obstacles: list[dict]) -> dict | None:
    """The `to` box of a lone branding group grown as large as it fits: uniform scale, centred on the zone's X centre, up to
    BRAND_MAX_WIDTH_FRAC of the zone's width. Vertically it may leave the zone (its floor is the tallest already-placed product
    whose columns it overlaps, its ceiling the lowest already-placed header badge it overlaps, else the badge line
    BADGE_TOP_Y), so it never touches a snapped corner badge or a product. Never smaller than the standard fit; returns None when
    it cannot be larger (the caller keeps the standard fit)."""
    W, H = canvas
    frm = _union_box(idx, ids)
    if frm["w"] <= 0 or frm["h"] <= 0:
        return None
    base = _zone_fit(idx, ids, frm, frame)
    aspect = frm["w"] / frm["h"]
    cx, mid = frame["x"] + frame["w"] / 2, frame["y"] + frame["h"] / 2
    clear = BRAND_CLEARANCE_FRAC * H
    top_limit = BADGE_TOP_Y * H

    def room(w):
        x0, x1 = cx - w / 2, cx + w / 2
        floor, ceiling = frame["y"] - frame["h"] * 0.0, top_limit
        for o in obstacles:
            if o["x"] < x1 and o["x"] + o["w"] > x0:                      # shares columns with the roof
                if o["y"] + o["h"] / 2 >= mid:
                    ceiling = min(ceiling, o["y"] - clear)               # a badge above: stay below its bottom edge
                else:
                    floor = max(floor, o["y"] + o["h"] + clear)          # a product below: stay above its top edge
        return floor, ceiling

    max_w = BRAND_MAX_WIDTH_FRAC * frame["w"]
    for step in range(60):                                                # largest width first, down to the standard fit
        w = max_w - (max_w - base["w"]) * step / 59
        h = w / aspect
        floor, ceiling = room(w)
        if h <= ceiling - floor and w >= base["w"] - 1e-9:
            y = min(max(mid - h / 2, floor), ceiling - h)                 # centred on the band, clamped into the free room
            return _rect(cx - w / 2, y, w, h) if w > base["w"] + 1e-6 else None
    return None


def _place_zone_content(zone: str, idx: dict, ids: list[str], frame: dict, portrait: bool,
                        canvas: tuple | None = None, obstacles: list[dict] | None = None) -> list[dict]:
    """Dispatches to the right wireframe sub-placement for `zone`'s own kind of content: `header` ->
    top-left/top-right logos, `product`/`main_text` -> main_object + flanking subobject columns,
    `footer` -> the English/Tamil banner split. Used identically whether `ids` came from a real slot
    tag or from the "absorb untagged other content into an empty zone" fallback (see
    `convert_orientation`) - the wireframe applies to whatever ends up in a zone, regardless of how it
    got there."""
    if zone == ZONE_HEADER:
        if portrait and canvas:
            snapped = _place_snapped_badges(idx, ids, canvas, frame)
            if snapped is not None:
                return snapped
        return _place_top_region(idx, ids, frame)
    if zone == ZONE_FOOTER:
        return _place_footer_banner(idx, ids, frame, portrait, FOOTER_CONTENT_FRAC if portrait else None)
    # ZONE_PRODUCT / ZONE_MAIN_TEXT - "center" content. On a portrait target the main object gets a wider
    # column (PORTRAIT_FILL_BOOST x the default share, capped) so it is scaled up ~1.3x instead of
    # shrinking into a narrow middle column; the flanking subobject columns give up that width. A boost
    # by plain scaling would overflow into the flanks - widening the column is the boost that cannot.
    main_frac = min(PORTRAIT_MAIN_FRAC_MAX, MAIN_OBJECT_FRAC * PORTRAIT_FILL_BOOST) if portrait else MAIN_OBJECT_FRAC
    # The portrait PRODUCT zone also stands its content on the zone's bottom baseline (just above the footer)
    # and gives a lone object the bounded extra boost of `_zone_fit`; branding (`main_text`) keeps its standard
    # centred fit. Anchoring/boost are portrait-only - the wide template's product zone is a side column.
    is_product = zone == ZONE_PRODUCT and portrait
    if zone == ZONE_MAIN_TEXT and portrait and canvas and obstacles is not None and len(ids) == 1:
        big = _boost_brand_roof(idx, ids, frame, canvas, obstacles)
        if big is not None:
            return [{"op": "resize", "ids": ids, "from": _union_box(idx, ids), "to": big}]
    return _place_main_and_subobjects(idx, ids, frame, main_frac, anchor_bottom=is_product,
                                      single_boost=PORTRAIT_FILL_BOOST if is_product else 1.0, pedestal=is_product)


def _contain_box(frm: dict, slot: dict, align_x: str = "center", align_y: str = "center") -> dict:
    """`frm` scaled UNIFORMLY to fit inside `slot` (never enlarged past the slot on either axis) and aligned:
    align_x left/center/right, align_y top/center/bottom."""
    if frm["w"] <= 0 or frm["h"] <= 0:
        return _rect(slot["x"], slot["y"], 0.0, 0.0)
    k = min(slot["w"] / frm["w"], slot["h"] / frm["h"])
    w, h = frm["w"] * k, frm["h"] * k
    x = slot["x"] if align_x == "left" else slot["x"] + slot["w"] - w if align_x == "right" else slot["x"] + (slot["w"] - w) / 2
    y = slot["y"] + slot["h"] - h if align_y == "top" else slot["y"] if align_y == "bottom" else slot["y"] + (slot["h"] - h) / 2
    return _rect(x, y, w, h)


def _unstack_order(idx: dict, ids: list[str]) -> list[str]:
    """Left-to-right order for a horizontal lineup of `ids`: products that overlapped in x on the source (a
    vertical stack, UNSTACK_SAME_COLUMN_OVERLAP of the narrower) form one column, read top to bottom; columns
    run left to right by source centre-x. So a tall master's stack unfolds top-first."""
    nodes = sorted((idx[i]["node"] for i in ids), key=lambda n: n["x"] + n["w"] / 2)
    columns: list[list[dict]] = []
    for n in nodes:
        if columns:
            last = columns[-1]
            x0, x1 = min(k["x"] for k in last), max(k["x"] + k["w"] for k in last)
            overlap = min(x1, n["x"] + n["w"]) - max(x0, n["x"])
            if overlap >= UNSTACK_SAME_COLUMN_OVERLAP * min(x1 - x0, n["w"]):
                last.append(n)
                continue
        columns.append([n])
    return [n["id"] for col in columns for n in sorted(col, key=lambda k: -(k["y"] + k["h"] / 2))]


def _unstack_products(idx: dict, ids: list[str], frame: dict) -> list[dict]:
    """Lay `ids` (each one a product object/group) out as ONE horizontal lineup across `frame`, all standing on
    the frame's bottom edge (the shared baseline). One common uniform scale keeps every product's proportions and
    the relative sizes between them; it is the largest scale at which the row fits the frame's width (leaving
    the minimum clearance between neighbours) and its height. Left over width is spread as equal gaps - between
    products and at both ends - so a few products span the stage instead of huddling in the middle; when the row
    is tight, neighbours keep exactly the minimum clearance."""
    order = _unstack_order(idx, ids)
    boxes = {i: _union_box(idx, [i]) for i in order}
    n = len(order)
    clear = min(UNSTACK_MIN_CLEARANCE_FRAC * frame["w"], 0.5 * frame["w"] / max(1, n - 1))
    total_w = sum(boxes[i]["w"] for i in order)
    max_h = max(boxes[i]["h"] for i in order)
    if total_w <= 0 or max_h <= 0:
        return []
    k = min((frame["w"] - (n - 1) * clear) / total_w, frame["h"] / max_h)
    free = frame["w"] - k * total_w
    if free >= (n + 1) * clear:
        gap = edge = free / (n + 1)
    else:
        gap, edge = clear, max(0.0, (free - (n - 1) * clear) / 2)
    ops, x = [], frame["x"] + edge
    for i in order:
        b = boxes[i]
        w, h = b["w"] * k, b["h"] * k
        ops.append({"op": "resize", "ids": [i], "from": b, "to": _rect(x, frame["y"], w, h)})
        x += w + gap
    return ops


def _place_p2l(idx: dict, zones: dict, frames: dict, page_w: float, promoted: set) -> list[dict]:
    """Portrait master -> wide target. Header badges go to the far top-left / top-right slots (uniform scale,
    aligned to the corner) by the side of the SOURCE they sat on; band-routed (not promoted) header shapes near
    the horizontal middle are branding, so they join the centred roof instead. Branding is one centred, uniformly
    scaled slot. Products unfold into a horizontal lineup on the stage baseline. The footer keeps its usual
    English-left / Tamil-right banner. No zone shares any area with another (see `_wide_stage_bands`)."""
    def live(zone):
        return [i for i in zones[zone] if not _is_locked(idx, i)]

    def cx(i):
        n = idx[i]["node"]
        return (n["x"] + n["w"] / 2) / page_w if page_w > 0 else 0.5

    header, brand = live(ZONE_HEADER), live(ZONE_MAIN_TEXT)
    middle = [i for i in header if i not in promoted and 1 / 3 <= cx(i) <= 2 / 3]
    header = [i for i in header if i not in middle]
    brand = brand + middle
    left = [i for i in header if cx(i) < 0.5]
    right = [i for i in header if cx(i) >= 0.5]

    ops: list[dict] = []
    hf = frames[ZONE_HEADER]
    slot_w = CORNER_SLOT_FRAC * hf["w"]
    for ids, slot, align in ((left, _rect(hf["x"], hf["y"], slot_w, hf["h"]), "left"),
                             (right, _rect(hf["x"] + hf["w"] - slot_w, hf["y"], slot_w, hf["h"]), "right")):
        if ids:
            frm = _union_box(idx, ids)
            ops.append({"op": "resize", "ids": ids, "from": frm, "to": _contain_box(frm, slot, align, "top")})
    if brand:
        bf = frames[ZONE_MAIN_TEXT]
        slot = _rect(bf["x"] + bf["w"] * (1 - BRAND_SLOT_FRAC) / 2, bf["y"], bf["w"] * BRAND_SLOT_FRAC, bf["h"])
        frm = _union_box(idx, brand)
        ops.append({"op": "resize", "ids": brand, "from": frm, "to": _contain_box(frm, slot)})
    products = live(ZONE_PRODUCT)
    if products:
        ops.extend(_unstack_products(idx, products, frames[ZONE_PRODUCT]))
    footer = live(ZONE_FOOTER)
    if footer:
        ops.extend(_place_footer_banner(idx, footer, frames[ZONE_FOOTER], False, FOOTER_CONTENT_FRAC))
    return ops


CLUSTER_GAP_FRAC = 0.009      # bbox proximity (of the source page's LONG side) that ties loose fragments together.
                              # ~20 mm on the 90x30in Dalmia board (the margin derive_brand_rules.cluster() was tuned to);
                              # 0.0066 left the "EXPERT" wordmark split (52 + 4 + 2 fragments), 0.009 gives exactly the
                              # documented 58 / 48 / 24 logo clusters


SHADOW_IOU = 0.6              # a bitmap-bearing shape whose box overlaps a logo cluster's box this much is its drop shadow
CLUSTER_NAME = "Logo cluster"


def _iou(a: dict, b: dict) -> float:
    ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    if ox <= 0 or oy <= 0:
        return 0.0
    inter = ox * oy
    return inter / (a["w"] * a["h"] + b["w"] * b["h"] - inter)


def _has_text_or_bitmap(node: dict) -> bool:
    if node.get("text") or pe.is_bitmap(node):
        return True
    return any(_has_text_or_bitmap(c) for c in node.get("children") or [])


def _has_bitmap_only(node: dict) -> bool:
    """A bitmap, or a group whose every leaf is a bitmap or holds no text - i.e. it has a bitmap and no text."""
    def has_text(n):
        return bool(n.get("text")) or any(has_text(c) for c in n.get("children") or [])
    return pe.is_bitmap(node) or (any(_has_bitmap_only(c) for c in node.get("children") or []) and not has_text(node))


def _group_fragments(scene: dict) -> list[dict]:
    """`group` ops that bind loose, ungrouped vector fragments into one group per visual cluster.

    Real masters (the Dalmia boards: 133 loose top-level curves) draw a logo as dozens of separate curves - a white
    card, its drop-shadow duplicate, the wordmark, the icon. Zone routing works per top-level object, so each
    fragment used to be routed by ITS OWN centre: a card and its content landed in different zones, the largest
    fragment (the card) became a zone's "main object" as a giant slab, and its content ended up stranded as tiny
    pieces (verified live: a 30x40 conversion of a Dalmia board rendered a white house shape cut off mid-word, a white
    rectangle with a cropped Tamil fragment, and a lone icon). Clustering by bounding-box proximity
    (CLUSTER_GAP_FRAC, union-find - the same idea as derive_brand_rules.cluster) recovers the logos (52 / 48 / 24
    fragments on the real board, matching the documented ~58 / 50 / 24), and one `group` op per cluster makes every
    later stage see ONE object per logo, card and content together.

    Deliberately conservative: only top-level, visible, unlocked, untagged, non-text, non-bitmap shapes or groups are
    candidates; the page-covering background is excluded; a cluster is grouped only when it holds at least two members
    AND at least one loose shape - so masters whose logos are already groups (AL MADEENA, DARSHAN) are untouched - and
    only members of the same layer are grouped together (the op requires one parent)."""
    page_w, page_h = float(scene["page"]["width"]), float(scene["page"]["height"])
    if page_w <= 0 or page_h <= 0:
        return []
    gap = CLUSTER_GAP_FRAC * max(page_w, page_h)
    idx = scene_ops._index(scene)
    used_ids = set(idx)
    ops: list[dict] = []
    for layer in scene["layers"]:
        if layer.get("locked"):
            continue
        cand = [n for n in layer["children"]
                if n.get("kind") in ("shape", "group") and n.get("visible") is not False and not n.get("locked")
                and not _has_text_or_bitmap(n) and pe.kind_from_name(n.get("name")) is None
                and float(n["w"]) * float(n["h"]) < pe.BG_AREA_RATIO * page_w * page_h]
        parent = {n["id"]: n["id"] for n in cand}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for i, a in enumerate(cand):
            for b in cand[i + 1:]:
                if (a["x"] - gap <= b["x"] + b["w"] and b["x"] - gap <= a["x"] + a["w"]
                        and a["y"] - gap <= b["y"] + b["h"] and b["y"] - gap <= a["y"] + a["h"]):
                    parent[find(a["id"])] = find(b["id"])
        clusters: dict[str, list[dict]] = {}
        for n in cand:
            clusters.setdefault(find(n["id"]), []).append(n)
        eligible = [m for m in clusters.values() if len(m) >= 2 and any(x["kind"] == "shape" for x in m)]
        # Drop shadows: on the Dalmia boards each white logo card has a soft shadow that is a GROUP HOLDING A BITMAP
        # with (almost) the card's own box. Left top-level it is claimed by the product-image heuristic and dragged
        # away from its card (a stray dark rectangle in the product zone, the card left shadowless). A bitmap-bearing,
        # text-free, untagged, unlocked top-level shape whose box overlaps a cluster's box by >= SHADOW_IOU joins
        # that cluster; a product photo next to a logo does not overlap it that much, so it stays independent.
        if eligible:
            boxes = []
            for m in eligible:
                x0, y0 = min(x["x"] for x in m), min(x["y"] for x in m)
                x1, y1 = max(x["x"] + x["w"] for x in m), max(x["y"] + x["h"] for x in m)
                boxes.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0})
            for n in layer["children"]:
                if (n.get("kind") not in ("shape", "group") or n.get("visible") is False or n.get("locked")
                        or n.get("text") or pe.kind_from_name(n.get("name")) is not None
                        or any(n is x for m in eligible for x in m)
                        or not _has_bitmap_only(n)
                        or float(n["w"]) * float(n["h"]) >= pe.BG_AREA_RATIO * page_w * page_h):
                    continue
                best = max(range(len(eligible)), key=lambda i: _iou(n, boxes[i]))
                if _iou(n, boxes[best]) >= SHADOW_IOU:
                    eligible[best].append(n)
        for members in eligible:
            k = len(ops) + 1
            while f"frag{k}" in used_ids:
                k += 1
            gid = f"frag{k}"
            used_ids.add(gid)
            ops.append({"op": "group", "ids": [m["id"] for m in members], "group_id": gid, "name": CLUSTER_NAME})
    return ops


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
    `main_text` is a deliberately small sliver (see `_stack_bands`'s own 0.06-of-`avail` fraction,
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
    every stacked absorbing zone in the stack template (`_stack_bands` gives header/main_text/footer
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


SEPARABLE_MIN_INSIDE = 0.9   # a PowerClip child must lie >= 90% inside the page to count as foreground


def _is_separable_foreground(node: dict, page_w: float, page_h: float) -> bool:
    """A direct child of a page-covering PowerClip that is real, movable foreground content - not the
    container's own backdrop. Rejected: hidden shapes, anything backdrop-sized (>= BG_AREA_RATIO of the
    page, e.g. the full-bleed texture bitmap that fills the clip), and CLIPPED ARTWORK - a shape whose
    bounding box mostly lies outside the page (< SEPARABLE_MIN_INSIDE inside it). The last rule comes from
    the real AL MADEENA/DARSHAN masters: each clip holds a large art group (159-318 leaves, the maroon
    footer artwork and dot pattern) whose bbox centre is BELOW the page bottom (cy = -0.06 / -0.10);
    its visible part is a sliver of its bbox, so treating it as a foreground object would squash a
    mostly-hidden box into a zone. Such art stays with the background and is carried by the container."""
    if node.get("visible") is False:
        return False
    w, h = float(node.get("w") or 0), float(node.get("h") or 0)
    area = w * h
    if area <= 0 or page_w <= 0 or page_h <= 0:
        return False
    if area >= pe.BG_AREA_RATIO * page_w * page_h:
        return False
    x, y = float(node["x"]), float(node["y"])
    ox = max(0.0, min(x + w, page_w) - max(x, 0.0))
    oy = max(0.0, min(y + h, page_h) - max(y, 0.0))
    return (ox * oy) / area >= SEPARABLE_MIN_INSIDE


def _child_zone(node: dict, page_w: float, page_h: float) -> str:
    """Zone for an extracted PowerClip child: an explicit slot tag wins (an image tag only on something
    that contains a bitmap, a text tag only on text - the same compatibility product_engine enforces);
    contact-looking text goes to the footer; everything else by its own normalized centre-y on the
    SOURCE page, using the same bands the portrait target uses (footer < 0.20 <= product < 0.55 <=
    branding < 0.72 <= header)."""
    kind = pe.kind_from_name(node.get("name"))
    if kind in pe.IMAGE_KINDS and _contains_bitmap(node):
        return _KIND_TO_ZONE[kind]
    if kind in pe.TEXT_KINDS and node.get("text"):
        return _KIND_TO_ZONE[kind]
    t = node.get("text")
    if t and pe.CONTACT_RE.search(str(t.get("content") or "")):
        return ZONE_FOOTER
    cy = (float(node["y"]) + float(node["h"]) / 2) / page_h
    if cy >= STACK_BAND_HEADER[0]:
        return ZONE_HEADER
    if cy >= STACK_BAND_BRAND[0]:
        return ZONE_MAIN_TEXT
    if cy >= STACK_BAND_PRODUCT[0]:
        return ZONE_PRODUCT
    return ZONE_FOOTER


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
            # A page-covering PowerClip is the background, but its foreground children are real content
            # (logos, the product/table composite, contact text) that must be routed, or a cross
            # -orientation conversion can never move them. The container keeps the background role (its
            # backdrop fill/texture and any clipped artwork ride along with it); only separable children
            # are extracted. Plain shapes/groups/gradients with no clip children are untouched.
            node = idx[tid]["node"]
            if node.get("kind") == "powerclip" and node.get("children") and not _is_locked(idx, tid):
                for child in node["children"]:
                    if _is_separable_foreground(child, page_w, page_h):
                        zones[_child_zone(child, page_w, page_h)].append(child["id"])
                        used.add(child["id"])

    slots, warnings = pe.map_slots(scene)
    for slot in slots:
        top_id = _slot_top_id(idx, slot)
        if top_id in used:
            continue     # its visible content is inside a shape already claimed as the page background
        if slot.source == "heuristic" and idx[top_id]["node"].get("name") == CLUSTER_NAME:
            continue     # a bitmap (drop shadow) inside a synthesized logo cluster does not make the logo a product
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


def convert_orientation(scene: dict, target_w: float, target_h: float, max_stretch: float | None = None,
                        same_orientation_fit: bool = False) -> list[dict]:
    """See `_convert_orientation`. `max_stretch` overrides MAX_STRETCH_RATIO for this call only (1.0 = never
    distort a vector/text group; a huge value restores the old fill-to-frame behaviour).

    `same_orientation_fit=True` is for a scene that already is a master of the TARGET's orientation (the
    dual-master flow): when source and target orientation match, the whole design is scaled uniformly into the
    new page and centred (padding on the spare axis) by `_same_orientation_ops` instead of being re-zoned and
    unstacked. A cross-orientation conversion ignores the flag. Off by default - existing callers unchanged."""
    if same_orientation_fit:
        pw, ph = float(scene["page"]["width"]), float(scene["page"]["height"])
        if Direction(pw, ph, target_w, target_h).same_orientation:
            return _same_orientation_ops(scene, target_w, target_h)
    token = _MAX_STRETCH_CTX.set(max_stretch)
    try:
        return _convert_orientation(scene, target_w, target_h)
    finally:
        _MAX_STRETCH_CTX.reset(token)


def _same_orientation_ops(scene: dict, target_w: float, target_h: float) -> list[dict]:
    """Same-orientation aspect fit: a `page` op, the page-covering background cover-fitted (as in the zone
    path), and every other unlocked top-level shape moved as ONE rigid unit - uniform scale (contain) into the
    new page, centred, so an aspect mismatch becomes padding, never distortion, never re-stacking. Text sizes
    follow through the resize op. Locked shapes are left alone."""
    idx = scene_ops._index(scene)
    zones, _ = classify_zones(scene)
    ops: list[dict] = [{"op": "page", "width": _r(target_w), "height": _r(target_h)}]
    bg = set(zones[ZONE_BACKGROUND])
    for i in bg:
        if _is_locked(idx, i):
            continue
        n = idx[i]["node"]
        frm = _rect(n["x"], n["y"], n["w"], n["h"])
        ops.append({"op": "resize", "ids": [i], "from": frm,
                    "to": pe.aspect_fit(frm["w"], frm["h"], _rect(0, 0, target_w, target_h), fit="cover")})
    ids = [n["id"] for L in scene["layers"] for n in L["children"]
           if n["id"] not in bg and n.get("visible", True) is not False and not _is_locked(idx, n["id"])]
    if ids:
        frm = _union_box(idx, ids)
        if frm["w"] > 0 and frm["h"] > 0:
            page_w, page_h = float(scene["page"]["width"]), float(scene["page"]["height"])
            k = min(target_w / page_w, target_h / page_h)           # the page's own contain factor
            w, h = frm["w"] * k, frm["h"] * k
            # keep the content's position relative to the (scaled, centred) old page, so the design's own
            # margins survive and the spare axis becomes symmetric padding
            ox, oy = (target_w - page_w * k) / 2, (target_h - page_h * k) / 2
            to = _rect(ox + frm["x"] * k, oy + frm["y"] * k, w, h)
            ops.append({"op": "resize", "ids": ids, "from": frm, "to": to})
    return ops


def _convert_orientation(scene: dict, target_w: float, target_h: float) -> list[dict]:
    """A self-contained op list (a `page` op, then one `resize` op per non-empty zone) that re-lays
    `scene` out for a `target_w` x `target_h` mm page - apply with `scene_ops.apply_ops(scene, ops)`
    exactly like any other op list. Never includes a locked shape (or one on a locked layer) in a
    zone's ids, so the returned ops always apply cleanly; call `classify_zones` first if you need to
    know what was classified where, or what was skipped."""
    pre_ops = _group_fragments(scene)                       # loose logo fragments -> one group per cluster
    work = scene_ops.apply_ops(scene, pre_ops) if pre_ops else scene
    idx = scene_ops._index(work)
    zones, _ = classify_zones(work)
    zones = dict(zones)  # about to redistribute `other` below - classify_zones' own dict is not touched
    page_w, page_h = float(scene["page"]["width"]), float(scene["page"]["height"])
    direction = Direction(page_w, page_h, target_w, target_h)
    frames = calculate_zone_rects(target_w, target_h, portrait_source=direction.portrait_to_wide)
    fit_scale = min(target_w / page_w, target_h / page_h) if page_w > 0 and page_h > 0 else 1.0

    ops: list[dict] = [{"op": "page", "width": _r(target_w), "height": _r(target_h)}] + pre_ops

    # Background first, then re-read the scene: a page-covering PowerClip is cover-scaled as a whole, and
    # `_scale` carries every child with it, so any foreground child extracted from inside it (see
    # classify_zones) must be placed from ITS post-cover position - a child op computed from the original
    # box and applied after the container op would be transformed twice. Top-level shapes are untouched by
    # these ops, so re-indexing changes nothing for scenes without extracted children.
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
    idx = _Idx(scene_ops._index(scene_ops.apply_ops(scene, ops)))
    idx.page = (page_w, page_h)

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
    # docstring's table). Each bucket is fit as its own rigid unit into its own zone, not merged into
    # one - found live that a single merged blob crammed a shop-name text that sat near the BOTTOM of
    # the original page against a logo badge that sat near the TOP, because both landed in one group
    # despite being far apart originally. Only fires when a zone is COMPLETELY empty, so a properly
    # slot-tagged master (this tool's own future masters - see "Product slots") is unaffected: its
    # header/main_text/footer are never empty to begin with.
    #
    # `product` is excluded from absorption for the wide/grid templates - there it is a full-height
    # side column (wide) or a half-width cell (grid), not comparable to the others by vertical
    # position. It IS included for a PORTRAIT target: `_stack_bands` places product in the same
    # top-to-bottom vertical band as header/main_text/footer, so it's exactly as absorbable as they
    # are. Without this, an untagged vector logo/badge graphic - which has no product_engine slot at
    # all (only a bitmap gets the product_image heuristic; a curve/group logo does not, see CLAUDE.md
    # "Designer dataset analysis" on real, often-ungrouped logos) - could never land in a properly
    # sized zone on a portrait conversion even when product was the only empty zone available,
    # reproducing the reported "shrinks into the canvas centre" symptom for exactly that content.
    portrait = direction.to_portrait
    # Untagged shapes are routed by the source's own vertical bands whenever the target is portrait OR a
    # portrait master is being unfolded into a wide one (its layout IS those bands); landscape -> landscape/grid
    # keeps the capacity absorption below.
    route_bands = portrait or direction.portrait_to_wide
    promoted: set[str] = set()
    other_ids = [i for i in zones[ZONE_OTHER] if not _is_locked(idx, i)]
    if route_bands and other_ids and page_h > 0:
        # Portrait (stack template): every untagged shape joins the zone whose normalized vertical band
        # contains ITS OWN normalized centre-y on the source page (the same 0.20/0.55/0.72 boundaries
        # the target bands use - ratio-driven, nothing tied to a page size), instead of the capacity
        # split below. That keeps top badges in the header (where the existing left/right-by-x split
        # puts them in the two top corners), the central brand logo in the branding band, and the
        # product/table group above the footer - and can no longer pull a mid-page shape up into the
        # header or stretch a thin one into a band it never belonged to. A band no shape falls in stays
        # empty (the source itself had nothing there); it is not padded with unrelated content.
        for i in other_ids:
            n = idx[i]["node"]
            cy = (n["y"] + n["h"] / 2) / page_h
            left = n["x"] / page_w if page_w > 0 else 0.0
            # Top-right badge promotion: a secondary badge on the source's upper-right (e.g. a
            # "BLACK STONE" header badge whose centre sits a little BELOW the 0.72 header boundary,
            # ~0.65) would otherwise be routed to the branding band and leave the top-right corner
            # empty. Promote it into the header, where the left/right-by-x split puts it top-right.
            # Only badge-sized shapes qualify (height <= PROMOTE_MAX_H_FRAC of the source page): a
            # tall product/table composite that merely has its centre in that quadrant is not a badge.
            right_edge = (n["x"] + n["w"]) / page_w if page_w > 0 else 1.0
            if portrait:
                is_badge = cy > PROMOTE_MIN_CY and left >= PROMOTE_MIN_CX and n["h"] <= PROMOTE_MAX_H_FRAC * page_h
            else:
                # portrait -> wide: an upper-quadrant badge on EITHER side of the source (wholly in its left or
                # right half, and small - never a product or the brand roof) is promoted to the header so it can
                # reach the far top-left / top-right slot of the wide banner.
                is_badge = (cy > PROMOTE_MIN_CY and n["h"] <= P2L_BADGE_MAX_H_FRAC * page_h
                            and n["w"] <= P2L_BADGE_MAX_W_FRAC * page_w and (left >= 0.5 or right_edge <= 0.5))
            if is_badge:
                z = ZONE_HEADER
                promoted.add(i)
            elif cy >= STACK_BAND_HEADER[0]:
                z = ZONE_HEADER
            elif cy >= STACK_BAND_BRAND[0]:
                z = ZONE_MAIN_TEXT
            elif cy >= STACK_BAND_PRODUCT[0]:
                z = ZONE_PRODUCT
            else:
                z = ZONE_FOOTER
            zones[z] = list(zones[z]) + [i]
        assigned = set(other_ids)
        zones[ZONE_OTHER] = [i for i in zones[ZONE_OTHER] if i not in assigned]
        other_ids = []
    absorbing = sorted((z for z in (ZONE_HEADER, ZONE_MAIN_TEXT, ZONE_FOOTER) if not zones[z]),
                       key=lambda z: -frames[z]["y"])                 # top zone first
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
            # Deliberately NOT _place_zone_content here: absorbed "other" content has no semantic
            # role (it's just whatever untagged shapes landed in this zone by capacity - see
            # _split_by_capacity), so it keeps the plain single-group _zone_fit that preserves its
            # own original top-to-bottom internal order. The wireframe sub-placement below assumes
            # its ids genuinely belong to the zone's own role (a real brand_title/product_image/etc.
            # slot tag - see classify_zones) - applying it here was tried and found live to scramble
            # order: a header-capacity bucket that absorbed a LOWER-page shape (because it had more
            # room, not because that shape is a logo) then split by x-position alone, landing that
            # lower shape's content side-by-side with a genuine top shape instead of staying below it.
            frm = _union_box(idx, bucket)
            to = _zone_fit(idx, bucket, frm, frame)
            ops.append({"op": "resize", "ids": bucket, "from": frm, "to": to})
            placed.update(bucket)
        zones[ZONE_OTHER] = [i for i in zones[ZONE_OTHER] if i not in placed]  # the rest keep the fallback below

    if direction.portrait_to_wide:
        ops.extend(_place_p2l(idx, zones, frames, page_w, promoted))
    else:
        placed_boxes: list[dict] = []          # header badges and products, already placed: the branding roof avoids them
        for zone in (ZONE_HEADER, ZONE_PRODUCT, ZONE_MAIN_TEXT, ZONE_FOOTER):
            ids = [i for i in zones[zone] if not _is_locked(idx, i)]
            if not ids:
                continue
            frame = frames[zone]
            zone_ops = _place_zone_content(zone, idx, ids, frame, portrait, (target_w, target_h), placed_boxes)
            ops.extend(zone_ops)
            if zone in (ZONE_HEADER, ZONE_PRODUCT):
                placed_boxes.extend(o["to"] for o in zone_ops if o["op"] == "resize")

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
