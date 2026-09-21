"""Pure-Python layout engine.

Takes the objects of a master design (measured in millimetres, origin at the
bottom-left of the page, y pointing up - same as CorelDRAW) and computes new
positions/sizes for a different page size.

Nothing here touches CorelDRAW, so it can be unit-tested anywhere.

Roles (decided by object name tag first, then by heuristics):

    bg        stretches to cover the whole page
    frame     keeps its margins to the page edges, stretches in between
    fixed     keeps its size, keeps its distance to the nearest edge
    shopname  text that gets replaced per-shop (see "Shop name replacement")
    text      scaled uniformly, position anchored proportionally
    logo      scaled uniformly, position anchored proportionally
    (any other artwork is treated as "logo")

Name tags: name a shape in CorelDRAW (Object Manager) starting with the role,
e.g. "bg", "bg_wall", "logo_main", "frame", "fixed_phone". Case-insensitive.

Tiling (real designer masters, untagged): studying real production masters
against the designer's own manual resizes (see backend/tools/validate_all.py,
backend/tools/analyze_designs.py, and the top-level CLAUDE.md "Designer
dataset analysis" section) showed that for boards much wider or taller than
the master, designers duplicate the logos/graphics ("the panel") side by
side or stacked and move the shop-name text into the gap between copies,
rather than shrinking a single copy to an illegible size - but they do NOT
duplicate other standalone text (e.g. a small-print "authorized dealer" /
phone-number footer line next to the logos): only `logo`-role objects join
the tiled panel; `text` that isn't the shop name is always scaled/positioned
individually, tiling or not. `compute_layout(..., tile=True)` reproduces
this as one rigid, evenly-scaled panel repeated with even gaps - it is
opt-in and off by default so it can never change the output of the existing
bg/frame/fixed/text/logo rules or their tests.

Shop name replacement: real masters are untagged, so the shop-name text is
found by content, not by name - pass `shop_name`/`shop_name_local` plus
`shopname_ids` (ids found by `find_shopname_ids`, matching known old text)
or tag the shape `shopname` explicitly. Matched objects get their `text`
(and, for Tamil content, `font`) set on the returned `Placed` object; the
caller (CorelEngine) is the one that actually writes it via COM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field

ROLES = ("bg", "frame", "fixed", "shopname", "text", "logo")

# Floor for CorelEngine's text-fit shrink loop (see engines.py._fit_text) and
# metrics.py's text_legibility layout check (metrics_config.json's
# layout.min_text_pt - keep the two in sync). Derived from real data, not a
# guess: every text font size across all 13 cached dalmia real files ranges
# 80.7-300pt (large-format signage read from a distance, not desktop print) -
# 40pt is a floor comfortably below the smallest real value, meant to catch a
# genuinely broken shrink, not to model legibility-at-viewing-distance (which
# isn't specified anywhere in this dataset).
MIN_TEXT_PT = 40.0

TAMIL_FONT = "Nirmala UI"  # ships with Windows (Indic UI font); verified installed and
# renders Tamil without falling back to tofu boxes. "Noto Sans Tamil" is NOT a safe
# default even though it's commonly recommended online: it isn't installed on a stock
# Windows/CorelDRAW machine, and CorelDRAW silently no-ops the Font assignment when
# the name doesn't match an installed font (Text.Story.Font reads back "" afterwards)
# rather than raising - always verify the write stuck (see CorelEngine._set_replacement_text).
_TAMIL_RE = re.compile(r"[஀-௿]")


def is_tamil(text: str | None) -> bool:
    return bool(text) and bool(_TAMIL_RE.search(text))


@dataclass
class Obj:
    id: str
    name: str
    kind: str  # "text" | "shape" | "group" | "bitmap" ...
    x: float  # left, mm
    y: float  # bottom, mm
    w: float  # mm
    h: float  # mm
    text: str | None = None  # content, for kind=="text" objects only
    fill_cmyk: tuple | None = None  # (c, m, y, k) 0-100, best-effort - None if not a uniform fill or unreadable


@dataclass
class Placed:
    id: str
    name: str
    role: str
    x: float
    y: float
    w: float
    h: float
    orig: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    text: str | None = None  # new text content, if this shape's text should be replaced
    font: str | None = None  # font to set alongside `text` (e.g. for Tamil content)
    bring_to_front: bool = False  # CorelEngine should call Shape.OrderToFront() after positioning this shape
    recolor_cmyk: tuple | None = None  # CorelEngine should set this shape's uniform fill to this (c, m, y, k)

    def to_dict(self):
        return asdict(self)


def detect_role(o: Obj, page_w: float, page_h: float) -> str:
    n = o.name.strip().lower()
    for r in ROLES:
        if n.startswith(r):
            return r
    if page_w > 0 and page_h > 0 and (o.w * o.h) / (page_w * page_h) >= 0.9:
        return "bg"
    if o.kind == "text":
        return "text"
    return "logo"


TILE_ASPECT_THRESHOLD = 1.4  # new/old dimension ratio above which we tile that axis


def compute_layout(
    objects: list[Obj],
    page_w: float,
    page_h: float,
    new_w: float,
    new_h: float,
    safe_margin: float = 0.0,
    tile: bool = False,
    shop_name: str | None = None,
    shop_name_local: str | None = None,
    shopname_ids: set[str] | None = None,
    brand_rule: dict | None = None,
    phone: str | None = None,
    gst: str | None = None,
    address_lines: list[str] | None = None,
    contact_ids: set[str] | None = None,
) -> list[Placed]:
    """Return placement for every object on the new page size (all mm).

    `tile`, `shop_name`, `shop_name_local`, `shopname_ids` and `brand_rule`
    are opt-in additions (see module docstring); leaving them out reproduces
    the original bg/frame/fixed/text/logo behaviour exactly.

    `brand_rule` (see `load_brand_rule` and backend/tools/derive_brand_rules.py)
    only changes anything while tiling is active: instead of treating every
    `logo` object as one rigid panel repeated as a whole, it splits them into
    the named groups the rule was derived from (by nearest bounding-box
    match) and tiles each group independently, at the repeat count its own
    `repeat_table` gives for the target aspect ratio - some groups (a small
    badge) may never repeat while others (a logo) do, matching what the
    designer's real files actually did instead of a single blanket panel.

    `phone`/`gst`/`address_lines` + `contact_ids` (from `find_contact_ids`)
    are the per-shop-content counterpart to shop-name replacement - see
    "Per-shop content replacement" in CLAUDE.md. Unlike the shop name, the
    phone/GST footer's *label* text ("Phone No." / "GST NO.") is stable
    across every real dalmia file even though the value after it changes
    per shop, so it's found by that stable label pattern rather than by
    matching against an old known value.
    """
    if min(page_w, page_h, new_w, new_h) <= 0:
        raise ValueError("Page sizes must be positive")

    s = min(new_w / page_w, new_h / page_h)  # uniform "fit" scale
    aspect_ratio_change = (new_w / new_h) / (page_w / page_h)

    roles = {o.id: detect_role(o, page_w, page_h) for o in objects}
    shopname_ids = set(shopname_ids or ())
    shopname_ids |= {o.id for o in objects if roles[o.id] == "shopname"}
    contact_ids = set(contact_ids or ())

    axis, n_tiles = _tile_plan(page_w, page_h, new_w, new_h) if tile else (None, 1)

    out: list[Placed] = []
    panel_objs: list[Obj] = []

    for o in objects:
        role = roles[o.id]
        orig = {"x": o.x, "y": o.y, "w": o.w, "h": o.h}
        warns: list[str] = []

        if o.id in shopname_ids:
            text, font = _shopname_replacement(o, shop_name, shop_name_local)
            # A `panel_sequence` rule's real boards keep the shop name in its
            # normal bottom-bar spot (between the "authorized dealer" footer
            # and the phone/GST line), not in a gap between panel copies -
            # confirmed by eye against dalmia's real wide renders, where the
            # generic gap-centring path used to put it as tiny, oddly
            # -positioned text between panels instead (GATE feedback). Only
            # the generic single-rigid-panel/brand-ruled-groups schemes use
            # the gap; panel_sequence lets it fall through to the ordinary
            # proportional scale+centre placement below, same as any other
            # text.
            if axis and n_tiles > 1 and not (brand_rule and brand_rule.get("panel_sequence")):
                continue  # placed after the panel, centred in the gap between tiles
            w, h = o.w * s, o.h * s
            cx = (o.x + o.w / 2) / page_w * new_w
            cy = (o.y + o.h / 2) / page_h * new_h
            x, y = cx - w / 2, cy - h / 2
            x, y = _clamp_and_warn(x, y, w, h, new_w, new_h, safe_margin, s, warns)
            out.append(Placed(o.id, o.name, "shopname", x, y, w, h, orig, warns, text, font))
            continue

        if role == "bg":
            x, y, w, h = 0.0, 0.0, new_w, new_h

        elif role == "frame":
            ml, mb = o.x, o.y
            mr = page_w - (o.x + o.w)
            mt = page_h - (o.y + o.h)
            x, y = ml, mb
            w, h = new_w - ml - mr, new_h - mb - mt
            if w <= 0 or h <= 0:
                warns.append("frame margins larger than new page; scaled instead")
                x, y, w, h = o.x * s, o.y * s, o.w * s, o.h * s

        elif role == "fixed":
            w, h = o.w, o.h
            x = _anchor_axis(o.x, o.w, page_w, new_w)
            y = _anchor_axis(o.y, o.h, page_h, new_h)

        else:  # text / logo -> tiled as part of the panel (logo only), or scaled individually
            # Standalone top-level text that isn't the shop name (e.g. a small-print
            # "authorized dealer" footer line) is never part of the repeated artwork
            # in real designer files, even when the logos beside it get tiled - see
            # CLAUDE.md "Designer dataset analysis" / dalmia validation. Only `logo`
            # (graphics/groups/bitmaps) gets folded into the tiled panel.
            if axis and n_tiles > 1 and role == "logo":
                panel_objs.append(o)
                continue
            w, h = o.w * s, o.h * s
            cx = (o.x + o.w / 2) / page_w * new_w
            cy = (o.y + o.h / 2) / page_h * new_h
            x, y = cx - w / 2, cy - h / 2

        # keep inside the page (respecting the safe margin) unless it is bg/frame
        if role in ("text", "logo", "fixed"):
            x, y = _clamp_and_warn(x, y, w, h, new_w, new_h, safe_margin, s, warns)
            if role in ("text", "logo") and (aspect_ratio_change > 2 or aspect_ratio_change < 0.5):
                warns.append("very different aspect ratio; review layout manually")

        contact_text = _contact_replacement(o, phone, gst, address_lines) if o.id in contact_ids else None
        out.append(Placed(o.id, o.name, role, x, y, w, h, orig, warns, contact_text))

    if axis and n_tiles > 1:
        if brand_rule and brand_rule.get("panel_sequence"):
            panel_placed, panel_scale = _place_panel_sequence(
                panel_objs, roles, page_w, page_h, new_w, new_h, axis, brand_rule["panel_sequence"],
            )
        elif brand_rule and brand_rule.get("groups"):
            panel_placed, panel_scale = _place_brand_ruled_panel(
                panel_objs, roles, page_w, page_h, new_w, new_h, axis, brand_rule,
            )
        else:
            panel_placed, panel_scale = _place_tiled_panel(panel_objs, roles, new_w, new_h, axis, n_tiles)
        out.extend(panel_placed)
        if not (brand_rule and brand_rule.get("panel_sequence")):
            # panel_sequence already placed the shop name in its normal
            # bottom-bar spot in the main loop above - see that branch's
            # comment for why the gap-centring path doesn't apply here.
            shopname_objs = [o for o in objects if o.id in shopname_ids]
            out.extend(_place_shopname_in_gap(
                shopname_objs, new_w, new_h, panel_scale, shop_name, shop_name_local,
            ))

    return out


def _clamp_and_warn(x, y, w, h, new_w, new_h, safe_margin, s, warns: list[str]):
    x2, y2 = _clamp(x, y, w, h, new_w, new_h, safe_margin)
    if (x2, y2) != (x, y):
        warns.append("moved to stay inside page")
    if w > new_w - 2 * safe_margin or h > new_h - 2 * safe_margin:
        warns.append("object larger than page")
    if s < 0.25:
        warns.append(f"scaled down to {s:.0%}; check legibility")
    return x2, y2


def _tile_plan(page_w: float, page_h: float, new_w: float, new_h: float) -> tuple[str | None, int]:
    """Decide whether to tile the panel horizontally or vertically.

    Derived from studying real designer resizes of production masters (see
    backend/tools/analyze_designs.py): wide targets get the panel repeated
    side by side, tall targets get it stacked, and the panel count is a
    round of how many master-page-widths/heights fit in the target. This
    slightly overshoots on extreme ratios (a 35x4ft board off a 12x4ft
    master produced 2 real panels but this predicts 3) - a known,
    documented approximation, not an exact match to manual designer choices.
    """
    rx, ry = new_w / page_w, new_h / page_h
    if rx > ry and rx > TILE_ASPECT_THRESHOLD:
        return "x", max(1, round(rx))
    if ry > rx and ry > TILE_ASPECT_THRESHOLD:
        return "y", max(1, round(ry))
    return None, 1


def _bbox(objs: list[Obj]) -> tuple[float, float, float, float]:
    xs0 = [o.x for o in objs]
    ys0 = [o.y for o in objs]
    xs1 = [o.x + o.w for o in objs]
    ys1 = [o.y + o.h for o in objs]
    return min(xs0), min(ys0), max(xs1), max(ys1)


def _place_tiled_panel(panel_objs, roles, new_w, new_h, axis, n) -> tuple[list[Placed], float]:
    """Repeat panel_objs n times along `axis`, each copy contain-fit into its
    own even cell of the new page. Returns (placed_shapes, scale_used) - the
    scale is reused to size the shop-name block consistently.
    """
    if not panel_objs:
        return [], 1.0
    bx0, by0, bx1, by1 = _bbox(panel_objs)
    panel_w, panel_h = bx1 - bx0, by1 - by0

    cell_w, cell_h = (new_w / n, new_h) if axis == "x" else (new_w, new_h / n)
    scale = min(cell_w / panel_w, cell_h / panel_h)  # contain-fit each copy in its cell
    panel_w_s, panel_h_s = panel_w * scale, panel_h * scale

    out: list[Placed] = []
    for i in range(n):
        if axis == "x":
            cell_left, cell_bottom = i * cell_w, 0.0
        else:
            cell_left, cell_bottom = 0.0, (n - 1 - i) * cell_h  # reading order: last tile on top
        x_offset = cell_left + (cell_w - panel_w_s) / 2 - bx0 * scale
        y_offset = cell_bottom + (cell_h - panel_h_s) / 2 - by0 * scale
        out.extend(_place_tile_copy(panel_objs, roles, scale, x_offset, y_offset, i))
    return out, scale


def _nearest_group(cx: float, cy: float, groups: list[dict]) -> dict:
    """The rule group whose master bounding-box centre is closest - reliable
    because it's always matched against the same master file's own geometry.
    """
    def dist(g):
        b = g["bbox_mm"]
        gcx, gcy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
        return (cx - gcx) ** 2 + (cy - gcy) ** 2

    return min(groups, key=dist)


def _repeat_count_for(group: dict, aspect: float) -> int:
    if group.get("repeat") != "by_aspect":
        return 1
    table = group["repeat_table"]
    # nearest-aspect lookup, not interpolation - see repeat_table_note in the rule file
    return min(table, key=lambda t: abs(t["aspect"] - aspect))["count"]


def _place_brand_ruled_panel(panel_objs, roles, page_w, page_h, new_w, new_h, axis, brand_rule) -> tuple[list[Placed], float]:
    """Like _place_tiled_panel, but splits panel_objs into the brand rule's
    named groups (nearest bounding-box match) and gives each its own repeat
    count for this target aspect ratio, instead of tiling everything as one
    rigid panel.

    Only groups that actually repeat ("by_aspect") compete for tile cells,
    laid out left-to-right (or bottom-to-top) in the same order their
    centres had in the master, sharing the cell count among themselves.
    A "never repeats" group (e.g. a small badge) does NOT also claim a
    whole cell for its single copy - an earlier version did, and giving a
    tiny badge the same 1/Nth of the page as a big repeating logo forced
    everything else to shrink far more than necessary (validated: this
    made every tiled dalmia board's diff worse, not better - see
    CLAUDE.md). Instead it keeps its original proportional position on the
    page (like an ordinary un-tiled `logo`), scaled by the plain uniform
    fit factor, so a badge that was top-right of the master stays top-right
    of the new page regardless of how the repeating groups are arranged.
    """
    if not panel_objs:
        return [], 1.0
    groups_cfg = brand_rule["groups"]
    target_aspect = new_w / new_h
    fit_scale = min(new_w / page_w, new_h / page_h)

    buckets: dict[int, list] = {}
    for o in panel_objs:
        g = _nearest_group(o.x + o.w / 2, o.y + o.h / 2, groups_cfg)
        buckets.setdefault(g["cluster_id"], []).append(o)

    repeating = []  # (master_centre, objs, count)
    fixed = []  # objs that keep their proportional position, unscaled by tiling
    for cid, objs in buckets.items():
        cfg = next(g for g in groups_cfg if g["cluster_id"] == cid)
        if cfg.get("repeat") == "by_aspect":
            count = _repeat_count_for(cfg, target_aspect)
            centre = sum(o.x + o.w / 2 for o in objs) / len(objs)
            repeating.append((centre, objs, count))
        else:
            fixed.extend(objs)
    repeating.sort(key=lambda e: e[0])

    out: list[Placed] = []
    scales: list[float] = []

    total_cells = max(1, sum(e[2] for e in repeating))
    cell_w, cell_h = (new_w / total_cells, new_h) if axis == "x" else (new_w, new_h / total_cells)
    cell_i = 0
    for _, objs, count in repeating:
        bx0, by0, bx1, by1 = _bbox(objs)
        gw, gh = bx1 - bx0, by1 - by0
        scale = min(cell_w / gw, cell_h / gh) if gw > 0 and gh > 0 else 1.0
        scales.append(scale)
        gw_s, gh_s = gw * scale, gh * scale
        for _copy in range(count):
            if axis == "x":
                cell_left, cell_bottom = cell_i * cell_w, 0.0
            else:
                cell_left, cell_bottom = 0.0, (total_cells - 1 - cell_i) * cell_h
            x_offset = cell_left + (cell_w - gw_s) / 2 - bx0 * scale
            y_offset = cell_bottom + (cell_h - gh_s) / 2 - by0 * scale
            out.extend(_place_tile_copy(objs, roles, scale, x_offset, y_offset, cell_i))
            cell_i += 1

    if fixed:
        scales.append(fit_scale)
        bx0, by0, bx1, by1 = _bbox(fixed)
        gw, gh = bx1 - bx0, by1 - by0
        cx = (bx0 + gw / 2) / page_w * new_w
        cy = (by0 + gh / 2) / page_h * new_h
        w, h = gw * fit_scale, gh * fit_scale
        x_offset = cx - w / 2 - bx0 * fit_scale
        y_offset = cy - h / 2 - by0 * fit_scale
        out.extend(_place_tile_copy(fixed, roles, fit_scale, x_offset, y_offset, cell_i))

    return out, (min(scales) if scales else 1.0)


_SIZE_TABLE_KEYS = ("target_h_frac", "target_w_frac", "target_cy_frac")


def _interp_cx_frac(cfg: dict, aspect: float) -> float | None:
    """Optional per-group horizontal-centering fraction (of `new_w`),
    measured the same way `target_cy_frac` is - stored as
    `cfg["cx_table"]: [{"aspect", "target_cx_frac"}, ...]`.

    Added after measuring all 4 real wide dalmia boards directly (not
    inferred from the diff-percentage numbers): every one of the 3
    panel_sequence groups landed 840-1100mm too far LEFT of its real
    position on every board, while their vertical position (`target_cy_frac`)
    was already accurate to within ~16mm. Root cause was
    `_place_panel_sequence`'s horizontal placement for axis="x": it centred
    each group inside an evenly-divided `new_w / n` cell (`cell_left = i *
    cell_w`), which was never measured against a real file the way
    `target_cy_frac`/`target_h_frac`/`target_w_frac` were - it was a
    plausible-looking assumption, not verified data. The real files instead
    keep each group's horizontal centre at a roughly constant, measured
    fraction of the page width per aspect (closely tracking the master's
    own proportional x-position for the first group), not "cell i of n."

    Returns None if a group's config has no `cx_table` at all (older or
    synthetic configs, including every hand-built fixture in
    test_layout.py) - callers then fall back to the original naive
    even-cell horizontal centering exactly as before, so nothing that
    already passed regresses just because this field exists.
    """
    table = cfg.get("cx_table")
    if not table:
        return None
    rows = sorted(table, key=lambda r: r["aspect"])
    if aspect <= rows[0]["aspect"]:
        return rows[0]["target_cx_frac"]
    if aspect >= rows[-1]["aspect"]:
        return rows[-1]["target_cx_frac"]
    for a, b in zip(rows, rows[1:]):
        if a["aspect"] <= aspect <= b["aspect"]:
            t = (aspect - a["aspect"]) / (b["aspect"] - a["aspect"])
            return a["target_cx_frac"] + (b["target_cx_frac"] - a["target_cx_frac"]) * t
    return rows[-1]["target_cx_frac"]  # unreachable given the bounds checks above, kept as a safe fallback


def _interp_size_table(table: list[dict], aspect: float) -> tuple[float, float, float]:
    """Linearly interpolate (target_h_frac, target_w_frac, target_cy_frac)
    between the two `size_table` entries nearest `aspect` - clamped to the
    nearest entry if `aspect` falls outside the table's range, never
    extrapolated past it. A flat average across every sample was tried
    first and measurably didn't fit every sampled board (see CLAUDE.md
    "Wide-board panel sequence" - 06 180x60's enlarged badge overflowed its
    cell). `target_w_frac` is stored explicitly rather than derived from
    the group's own master aspect ratio: real designer files show the
    enlarged badge card's actual width/height ratio *doesn't* match the
    master badge shape's own ratio (it's redesigned to roughly match the
    other card's proportions, not algebraically scaled up) - deriving
    width from height via the master aspect ratio overflowed the target
    cell; using the real measured width directly does not.
    """
    rows = sorted(table, key=lambda r: r["aspect"])
    if aspect <= rows[0]["aspect"]:
        r = rows[0]
    elif aspect >= rows[-1]["aspect"]:
        r = rows[-1]
    else:
        r = None
        for a, b in zip(rows, rows[1:]):
            if a["aspect"] <= aspect <= b["aspect"]:
                t = (aspect - a["aspect"]) / (b["aspect"] - a["aspect"])
                return tuple(a[k] + (b[k] - a[k]) * t for k in _SIZE_TABLE_KEYS)
        r = rows[-1]  # unreachable given the bounds checks above, kept as a safe fallback
    return tuple(r[k] for k in _SIZE_TABLE_KEYS)


def _place_panel_sequence(panel_objs, roles, page_w, page_h, new_w, new_h, axis, seq_rule) -> tuple[list[Placed], float]:
    """Tile panel_objs as a SEQUENCE of named slots evenly spaced across the
    tiling axis, each with its own target size/vertical position - not a
    single rigid panel duplicated (`_place_tiled_panel`) and not a
    per-group "repeat count competes for shared cells" split either
    (`_place_brand_ruled_panel`).

    Built from CLAUDE.md "Wide-board panel sequence": studying dalmia's 4
    real wide boards by eye against their rendered PNGs found the master's
    3 logo groups play distinct, *fixed* roles rather than "the same thing,
    repeated" - one graphic (`sequence_3`/`sequence_4` name it) stays at
    roughly its master size, one (the small top-right badge) gets
    *enlarged* to match it and substituted in as a second "card", and a
    third repeats as a filler between them, with an extra filler copy
    appended once the target is wide enough (`aspect_split`). Two
    independent geometry-only matching attempts
    (`backend/tools/derive_brand_rules.py`, and Phase 2's example engine -
    see CLAUDE.md "Example-based layout engine") both mistook the enlarged
    badge for a duplicate of the other card; this rule exists because that
    mistake can't be fixed by better matching alone without a colour/content
    signal neither approach extracts - the correct assignment came from
    looking at the images, so it's captured here as verified data instead.

    `seq_rule` (see `brand_rules/dalmia.json`'s `panel_sequence` key):
    `{"aspect_split": float, "groups": [{"group_id", "bbox_mm", "size_table":
    [{"aspect", "target_h_frac", "target_cy_frac"}, ...]}, ...], "sequence_3":
    [group_id, ...], "sequence_4": [...]}`. `size_table` entries are fractions
    of `new_h` regardless of tiling axis (matching how they were measured -
    see CLAUDE.md), so this is currently only validated for horizontal
    tiling (all 4 sampled wide boards tile on x); untested for vertical.
    A group's size at the target aspect ratio is linearly interpolated
    between its two nearest `size_table` aspects (clamped to the nearest
    entry outside the table's range, never extrapolated past it) - not a
    single flat average across every sample, which was tried first and
    measurably didn't fit every sampled board (06 180x60's enlarged badge
    overflowed its cell - see CLAUDE.md).

    A shape only joins a named group if its centre falls inside that
    group's master bbox (expanded by `GROUP_MEMBERSHIP_MARGIN`) - NOT
    "whichever named group happens to be nearest," which was tried first
    and silently swept the master's full-width decorative accent strip
    (see CLAUDE.md "Designer dataset analysis") into the `tamil_card`
    bucket, since its bbox centre is geometrically closer to that group
    than to the other two even though it isn't part of any of them. That
    single wrongly-included shape (2345mm wide, spanning most of the page)
    blew up the whole bucket's bounding box, corrupting both its scale and
    its centring offset - visually obvious once rendered (the "roof"
    graphic appeared duplicated and the enlarged badge overflowed the
    page), not something the diff-percentage numbers alone made obvious.
    Anything outside every group's expanded bbox falls back to `fixed_objs`,
    kept at its own proportional position/size like an ordinary un-tiled
    logo - never dropped silently.
    """
    if not panel_objs:
        return [], 1.0
    groups_cfg = {g["group_id"]: g for g in seq_rule["groups"]}
    GROUP_MEMBERSHIP_MARGIN = 50.0
    BG_AREA_RATIO = 0.5  # a shape covering >= half its group's own bbox area is card background (a white card + drop shadow), not logo/text content

    def _in_group(o, g):
        b = g["bbox_mm"]
        ocx, ocy = o.x + o.w / 2, o.y + o.h / 2
        m = GROUP_MEMBERSHIP_MARGIN
        return (b["x"] - m) <= ocx <= (b["x"] + b["w"] + m) and (b["y"] - m) <= ocy <= (b["y"] + b["h"] + m)

    def _dist(o, g):
        b = g["bbox_mm"]
        gcx, gcy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
        return (o.x + o.w / 2 - gcx) ** 2 + (o.y + o.h / 2 - gcy) ** 2

    def _split_bg_and_content(objs):
        """A group's "card background" (a white card + drop shadow, each
        spanning most of the group's own bbox) vs. its actual logo/text
        "content" (much smaller shapes sitting on top of it) - see
        `card_from` below.
        """
        if not objs:
            return [], []
        bx0, by0, bx1, by1 = _bbox(objs)
        area = (bx1 - bx0) * (by1 - by0)
        bg, content = [], []
        for o in objs:
            ratio = (o.w * o.h) / area if area > 0 else 0
            (bg if ratio >= BG_AREA_RATIO else content).append(o)
        return bg, content

    buckets: dict[str, list] = {}
    fixed_objs: list[Obj] = []
    for o in panel_objs:
        candidates = [gid for gid in groups_cfg if _in_group(o, groups_cfg[gid])]
        if not candidates:
            fixed_objs.append(o)
            continue
        gid = min(candidates, key=lambda gid: _dist(o, groups_cfg[gid]))
        buckets.setdefault(gid, []).append(o)

    target_aspect = new_w / new_h
    sequence = seq_rule["sequence_4"] if target_aspect > seq_rule["aspect_split"] else seq_rule["sequence_3"]
    n = len(sequence)
    cell_w = new_w / n if axis == "x" else new_w
    cell_h = new_h if axis == "x" else new_h / n

    out: list[Placed] = []
    scales: list[float] = []
    for i, group_id in enumerate(sequence):
        objs = buckets.get(group_id)
        if not objs:
            continue
        cfg = groups_cfg[group_id]

        card_from = cfg.get("card_from")
        if card_from:
            # This group has no card background of its own in the master
            # (e.g. dalmia's small top-right badge is bare icon+text on the
            # blue page) but the real design puts it inside a white card
            # matching another group's card (see CLAUDE.md "Wide-board
            # panel sequence") - not just enlarged bare logo/text, which is
            # what a naive uniform scale-up of the master's badge shapes
            # produces. Borrows (duplicates) the template group's OWN card
            # -background shapes, sized/positioned via the TEMPLATE's size
            # table (both are measured to be nearly identical anyway - see
            # CLAUDE.md), then places this group's own content centred
            # inside at the master-measured content:card proportion
            # (`card_content_frac`), rather than stretching the content to
            # fill the whole card.
            template_cfg = groups_cfg[card_from]
            template_objs = buckets.get(card_from)
            if not template_objs:
                continue  # template wasn't placed this run either - nothing to borrow
            bg_objs, template_content = _split_bg_and_content(template_objs)
            if not bg_objs:
                continue
            bx0, by0, bx1, by1 = _bbox(bg_objs)
            gw, gh = bx1 - bx0, by1 - by0
            if gh <= 0:
                continue
            t_h_frac, t_w_frac, t_cy_frac = _interp_size_table(template_cfg["size_table"], target_aspect)
            scale = min((t_h_frac * new_h) / gh, (t_w_frac * new_w) / gw) if gw > 0 else (t_h_frac * new_h) / gh
            scales.append(scale)
            gw_s, gh_s = gw * scale, gh * scale

            # cx is THIS (borrowing) group's own measured horizontal slot,
            # not the template's - each named group sits at its own distinct
            # page position (see _interp_cx_frac), only h/w/cy are borrowed
            # from the template since the two cards measure out nearly
            # identical in size.
            cx_frac = _interp_cx_frac(cfg, target_aspect)
            if axis == "x":
                if cx_frac is not None:
                    x_offset = cx_frac * new_w - gw_s / 2 - bx0 * scale
                else:
                    cell_left = i * cell_w
                    x_offset = cell_left + (cell_w - gw_s) / 2 - bx0 * scale
            else:
                x_offset = (new_w - gw_s) / 2 - bx0 * scale
            y_offset = t_cy_frac * new_h - gh_s / 2 - by0 * scale
            if axis == "y":
                cell_bottom = (n - 1 - i) * cell_h
                y_offset = cell_bottom + (cell_h - gh_s) / 2 - by0 * scale

            out.extend(_place_tile_copy(bg_objs, roles, scale, x_offset, y_offset, i))

            frac = cfg["card_content_frac"]
            cbx0, cby0, cbx1, cby1 = _bbox(objs)
            c_gw, c_gh = cbx1 - cbx0, cby1 - cby0
            if c_gh <= 0:
                continue
            content_scale = min((frac["h"] * gh_s) / c_gh, (frac["w"] * gw_s) / c_gw) if c_gw > 0 else (frac["h"] * gh_s) / c_gh
            c_gw_s, c_gh_s = c_gw * content_scale, c_gh * content_scale
            card_left, card_bottom = x_offset + bx0 * scale, y_offset + by0 * scale
            content_cx = card_left + frac["cx"] * gw_s
            content_cy = card_bottom + frac["cy"] * gh_s
            content_x_offset = content_cx - c_gw_s / 2 - cbx0 * content_scale
            content_y_offset = content_cy - c_gh_s / 2 - cby0 * content_scale
            content_placed = _place_tile_copy(objs, roles, content_scale, content_x_offset, content_y_offset, i)

            # Some of this group's own content is styled for the master's
            # ORIGINAL background, not the new white card it's being moved
            # onto - confirmed live: the badge's English wordmark curves are
            # pure white (CMYK 0,0,0,0), meant to read against the master's
            # dark blue page, and render invisibly on the new white card.
            # The Tamil card's own text is a consistent dark blue (CMYK
            # 95,80,4,0 in the master) - recolour any pure-white content
            # shape to that same colour (never touches the icon's own
            # multi-coloured curves, which aren't white) so the borrowed
            # -card content actually matches the template's own card style,
            # not just its size and position.
            template_text_cmyk = next((o.fill_cmyk for o in template_content
                                        if o.fill_cmyk and o.fill_cmyk != (0, 0, 0, 0)), None)
            if template_text_cmyk:
                white_ids = {p.id for o, p in zip(objs, content_placed) if o.fill_cmyk == (0, 0, 0, 0)}
                for p in content_placed:
                    if p.id in white_ids:
                        p.recolor_cmyk = template_text_cmyk

            # The borrowed card background above is a COM Shape.Duplicate() of
            # the template's own card, which CorelDRAW stacks directly above
            # the template in z-order - not above this group's own content,
            # which keeps whatever z-order position it already had in the
            # master (often well below the template's card). Left alone, the
            # new card visually covers the content it's supposed to frame -
            # confirmed live: the badge's card rendered completely blank.
            # CorelEngine brings each of these to the front of its layer
            # after positioning it, so the content always ends up on top of
            # its own newly-placed card.
            for p in content_placed:
                p.bring_to_front = True
            out.extend(content_placed)
            continue

        bx0, by0, bx1, by1 = _bbox(objs)
        gw, gh = bx1 - bx0, by1 - by0
        if gh <= 0:
            continue
        target_h_frac, target_w_frac, target_cy_frac = _interp_size_table(cfg["size_table"], target_aspect)
        if gw <= 0:
            scale = (target_h_frac * new_h) / gh
        else:
            # constrained by whichever dimension is tighter, so a group whose
            # own master aspect ratio doesn't match its real target width/height
            # (the enlarged badge card - see _interp_size_table) never overflows
            # its cell, even though it means its shape isn't scaled perfectly
            # uniformly to the measured target in that case.
            scale = min((target_h_frac * new_h) / gh, (target_w_frac * new_w) / gw)
        scales.append(scale)
        gw_s, gh_s = gw * scale, gh * scale

        cx_frac = _interp_cx_frac(cfg, target_aspect)
        if axis == "x":
            if cx_frac is not None:
                x_offset = cx_frac * new_w - gw_s / 2 - bx0 * scale
            else:
                cell_left = i * cell_w
                x_offset = cell_left + (cell_w - gw_s) / 2 - bx0 * scale
        else:
            x_offset = (new_w - gw_s) / 2 - bx0 * scale
        y_offset = target_cy_frac * new_h - gh_s / 2 - by0 * scale
        if axis == "y":
            cell_bottom = (n - 1 - i) * cell_h
            y_offset = cell_bottom + (cell_h - gh_s) / 2 - by0 * scale

        out.extend(_place_tile_copy(objs, roles, scale, x_offset, y_offset, i))

    if fixed_objs:
        fit_scale = min(new_w / page_w, new_h / page_h)
        scales.append(fit_scale)
        bx0, by0, bx1, by1 = _bbox(fixed_objs)
        gw, gh = bx1 - bx0, by1 - by0
        cx = (bx0 + gw / 2) / page_w * new_w
        cy = (by0 + gh / 2) / page_h * new_h
        w, h = gw * fit_scale, gh * fit_scale
        x_offset = cx - w / 2 - bx0 * fit_scale
        y_offset = cy - h / 2 - by0 * fit_scale
        out.extend(_place_tile_copy(fixed_objs, roles, fit_scale, x_offset, y_offset, n))

    return out, (min(scales) if scales else 1.0)


def load_brand_rule(brand: str | None) -> dict | None:
    """Load backend/app/brand_rules/<brand>.json if it exists, else None -
    callers should treat a missing rule as "use the generic single-panel
    tiling", not an error (most brands won't have one).
    """
    if not brand:
        return None
    import json as _json
    from pathlib import Path as _Path

    path = _Path(__file__).resolve().parent / "brand_rules" / f"{brand.strip().lower()}.json"
    if not path.exists():
        return None
    try:
        return _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _place_tile_copy(panel_objs, roles, scale, x_offset, y_offset, tile_index) -> list[Placed]:
    placed = []
    for o in panel_objs:
        w, h = o.w * scale, o.h * scale
        x, y = o.x * scale + x_offset, o.y * scale + y_offset
        warns = [f"scaled down to {scale:.0%}; check legibility"] if scale < 0.25 else []
        orig = {"x": o.x, "y": o.y, "w": o.w, "h": o.h}
        placed.append(Placed(f"{o.id}_tile{tile_index}", o.name, roles[o.id], x, y, w, h, orig, warns))
    return placed


def _place_shopname_in_gap(shopname_objs, new_w, new_h, panel_scale, shop_name, shop_name_local) -> list[Placed]:
    if not shopname_objs:
        return []
    bx0, by0, bx1, by1 = _bbox(shopname_objs)
    block_w, block_h = bx1 - bx0, by1 - by0
    # match the panel copies' scale, but never enlarge past the master's own size
    scale = min(1.0, panel_scale)
    dest_cx, dest_cy = new_w / 2, new_h / 2
    x_offset = dest_cx - (bx0 + block_w / 2) * scale
    y_offset = dest_cy - (by0 + block_h / 2) * scale

    out = []
    for o in shopname_objs:
        text, font = _shopname_replacement(o, shop_name, shop_name_local)
        w, h = o.w * scale, o.h * scale
        x, y = o.x * scale + x_offset, o.y * scale + y_offset
        orig = {"x": o.x, "y": o.y, "w": o.w, "h": o.h}
        out.append(Placed(o.id, o.name, "shopname", x, y, w, h, orig, [], text, font))
    return out


def _shopname_replacement(o: Obj, shop_name, shop_name_local) -> tuple[str | None, str | None]:
    """Pick which replacement string (and font) fits this text object's script."""
    if is_tamil(o.text) and shop_name_local:
        return shop_name_local, TAMIL_FONT
    if shop_name:
        return shop_name, (TAMIL_FONT if is_tamil(shop_name) else None)
    if shop_name_local:
        return shop_name_local, TAMIL_FONT
    return None, None


_PHONE_RE = re.compile(r"(phone\s*no\.?\s*[:.]?\s*)([\d][\d +-]*)", re.IGNORECASE)
_GST_RE = re.compile(r"(gst\s*no\.?\s*[:.]?\s*)([A-Za-z0-9]*)", re.IGNORECASE)


def find_contact_ids(objects: list[Obj]) -> set[str]:
    """Content-based match for the phone/GST footer text.

    Unlike the shop name, real masters keep this label stable ("Phone No."
    / "GST NO.") across every shop - only the value after it changes - so
    it's found by that label, not by comparing against an old value (which
    is exactly what's different per shop, the opposite of the shop-name
    case). All 13 real dalmia files combine phone+GST into one text shape,
    two lines separated by "\\r" (see CLAUDE.md "Per-shop content
    replacement"); this matches that shape whichever of the two labels (or
    both) it contains.
    """
    matches = set()
    for o in objects:
        if o.kind != "text" or not o.text:
            continue
        if _PHONE_RE.search(o.text) or _GST_RE.search(o.text):
            matches.add(o.id)
    return matches


def _contact_replacement(o: Obj, phone: str | None, gst: str | None,
                          address_lines: list[str] | None) -> str | None:
    """Rebuild the phone/GST footer text for one shop.

    Preserves whatever label formatting the master's own text already uses
    (e.g. "Phone No. " vs "Phone No:") by substituting only the value after
    a matched label, rather than hardcoding an English label that might not
    match every master. If a label isn't present yet but a value is given,
    appends a new "Phone No. <value>" / "GST NO. <value>" line - lets a
    shop that needs a GST line get one even if this particular master
    happened not to have one already (e.g. dalmia's M Pandi file has no GST
    line at all). `address_lines`, if given, are appended as further lines
    in the same text object - there's no separate address shape in any
    sampled master, so this is the only place free-text per-shop content
    like an address has anywhere to go.
    """
    if phone is None and gst is None and not address_lines:
        return None
    text = o.text or ""
    if phone is not None:
        if _PHONE_RE.search(text):
            text = _PHONE_RE.sub(lambda m: m.group(1) + phone, text, count=1)
        else:
            text = (text + "\r" if text else "") + f"Phone No. {phone}"
    if gst is not None:
        if _GST_RE.search(text):
            text = _GST_RE.sub(lambda m: m.group(1) + gst, text, count=1)
        else:
            text = (text + "\r" if text else "") + f"GST NO. {gst}"
    if address_lines:
        text += "".join(f"\r{line}" for line in address_lines if line)
    return text


def find_shopname_ids(objects: list[Obj], *old_names: str | None) -> set[str]:
    """Best-effort match for untagged masters: find top-level text objects
    whose content matches one of the master's known old shop-name strings
    (case-insensitive, either containing the other). Returns an id set to
    pass to `compute_layout(..., shopname_ids=...)`.
    """
    hints = [h.strip().lower() for h in old_names if h and h.strip()]
    if not hints:
        return set()
    matches = set()
    for o in objects:
        if o.kind != "text" or not o.text:
            continue
        t = o.text.strip().lower()
        if any(t and h and (h in t or t in h) for h in hints):
            matches.add(o.id)
    return matches


def _anchor_axis(pos: float, size: float, old: float, new: float) -> float:
    """Keep the distance to the nearest edge; centre if it sits in the middle."""
    lo = pos
    hi = old - (pos + size)
    centre_off = abs((pos + size / 2) - old / 2)
    if centre_off < old * 0.02:
        return (new - size) / 2
    if lo <= hi:
        return lo
    return new - hi - size


def _clamp(x, y, w, h, new_w, new_h, m):
    x = max(m, min(x, new_w - m - w)) if w <= new_w - 2 * m else (new_w - w) / 2
    y = max(m, min(y, new_h - m - h)) if h <= new_h - 2 * m else (new_h - h) / 2
    return x, y


UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "ft": 304.8, "m": 1000.0}


def to_mm(value: float, unit: str) -> float:
    try:
        return float(value) * UNIT_TO_MM[unit]
    except KeyError:
        raise ValueError(f"Unsupported unit: {unit}")
