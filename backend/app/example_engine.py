"""Phase 2: example-based layout, built from real designer resizes instead
of geometric rules alone.

Pure Python, no CorelDRAW - takes already-dumped shape data (the same
dumps `validate_all.py`/`metrics.py` already use) and a target page size,
and returns a per-entity transform predicted from the *nearest real
examples* of how a human resized this exact master, falling back to the
rule-based `compute_layout` when there's no usable example data (a new
master with zero history, or a brand with no `brand_data/<brand>/examples.json`).

Terminology, to avoid the mistake `derive_brand_rules.py` made (see
CLAUDE.md "Per-brand tiling rule attempt"): an "entity" is a *named*,
identity-preserving piece of the master - not just "whatever bbox-clustering
produces this time." Four kinds:

- `bg`   - the one background fill, if any (always maps 1:1, never learned)
- `shopname` - the one shop-name text shape (always maps 1:1)
- `text` - every other individual text shape (footer, phone/GST, ...) -
  matched to the same *individual leaf shape* across boards by nearest
  size, never clustered with anything else (clustering it with adjacent
  text - which happens by proximity if you're not careful, e.g. the
  shopname sitting 2mm from the phone/GST line - would blend two entities
  that must vary independently into one, exactly the failure mode this
  design avoids)
- `logo_cluster` - a `derive_brand_rules.cluster()`-grouped blob of
  logo-role leaf shapes (a hand-drawn graphic's ~50-130 individual curves,
  collapsed to one comparable unit) - matched across boards by nearest
  relative height (found to be the most stable identity signal by hand
  -inspection - see CLAUDE.md "Wide-board panel sequence")

See CLAUDE.md "Example-based layout engine (Phase 2)" for the full design
and its honestly-scoped limitations.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .layout import Obj, detect_role, find_shopname_ids
from tools.derive_brand_rules import cluster as _cluster_objs, bbox_of  # noqa: E402  (backend/ on sys.path)

BRAND_DATA_ROOT = Path(__file__).resolve().parents[1] / "brand_data"
LOGO_CLUSTER_MARGIN = 20.0  # same value derive_brand_rules.py tuned against real dalmia logos
MIN_CLUSTER_SHAPES = 2  # ignore stray 1-shape "clusters" (noise, not a real graphic)

# Aspect ratio beyond which a target is considered "wide/tall" rather than a
# plain resize - same threshold layout.py's generic tiling uses, so the two
# systems agree on which regime a target falls into.
TILE_ASPECT_THRESHOLD = 1.4


@dataclass
class Entity:
    kind: str  # "bg" | "shopname" | "text" | "logo_cluster"
    key: str  # stable id within one master (e.g. "text_0", "logo_cluster_1")
    bbox: dict  # {x, y, w, h} in the SOURCE page's own mm
    shape_count: int = 1
    text: str | None = None  # for "text" entities, the master's own placeholder content


def master_entities(objects: list[Obj], page_w: float, page_h: float, shopname_hints: list[str]) -> list[Entity]:
    """The master's own named entities - the fixed vocabulary every real
    board's shapes get matched against.
    """
    shopname_ids = find_shopname_ids(objects, *shopname_hints)
    roles = {o.id: ("shopname" if o.id in shopname_ids else detect_role(o, page_w, page_h)) for o in objects}

    entities: list[Entity] = []
    text_i = 0
    for o in objects:
        role = roles[o.id]
        if role == "bg":
            entities.append(Entity("bg", "bg", {"x": o.x, "y": o.y, "w": o.w, "h": o.h}))
        elif role == "shopname":
            entities.append(Entity("shopname", "shopname", {"x": o.x, "y": o.y, "w": o.w, "h": o.h}, text=o.text))
        elif role == "text":
            entities.append(Entity("text", f"text_{text_i}", {"x": o.x, "y": o.y, "w": o.w, "h": o.h}, text=o.text))
            text_i += 1
        # role == "logo" handled below via clustering, "frame"/"fixed" not
        # present in this master - out of scope until a master that has them
        # is validated this way (see CLAUDE.md "Remaining limitations")

    logo_objs = [o for o in objects if roles[o.id] == "logo"]
    clusters = [c for c in _cluster_objs(logo_objs, LOGO_CLUSTER_MARGIN) if len(c) >= MIN_CLUSTER_SHAPES]
    clusters.sort(key=lambda c: bbox_of(c)[0])  # stable left-to-right order -> stable ids
    for i, c in enumerate(clusters):
        x, y, w, h = bbox_of(c)
        entities.append(Entity("logo_cluster", f"logo_cluster_{i}", {"x": x, "y": y, "w": w, "h": h}, len(c)))

    return entities


def _board_entities(objects: list[Obj], page_w: float, page_h: float, shopname_hints: list[str]) -> list[Entity]:
    """Same extraction, applied to a real board's own (already-resized)
    shapes - used both to build examples and, in leave-one-out validation,
    as ground truth to score a prediction against.
    """
    return master_entities(objects, page_w, page_h, shopname_hints)


def _size_ratio_close(a: dict, b: dict, page_a_h: float, page_b_h: float, tol: float = 0.35) -> bool:
    """Relative-height match, page-size independent - found by hand to be
    the most stable identity signal across resizes (a cluster's height as a
    fraction of page height barely moves for "keeps its size" entities, and
    moves in a very recognisable, large way for the one entity that gets
    deliberately enlarged - see CLAUDE.md "Wide-board panel sequence").
    """
    ra, rb = a["h"] / page_a_h, b["h"] / page_b_h
    if ra <= 0 or rb <= 0:
        return False
    ratio = ra / rb
    return (1 - tol) <= ratio <= (1 + tol)


def match_entities(master_ents: list[Entity], master_w: float, master_h: float,
                    board_ents: list[Entity], board_w: float, board_h: float) -> dict[str, list[Entity]]:
    """Match a real board's entities back to the master's, by kind first
    (bg always matches 1:1 if both sides have one; text/shopname and
    logo_cluster by nearest relative-height, order as a tiebreaker).
    Returns {master_entity_key: [matched board entities]} - a list because
    a `logo_cluster` may be matched by more than one board entity (a
    repeat) or an enlarged/substituted one (still a list of length 1, just
    a different size - see the badge case in "Wide-board panel sequence").
    """
    out: dict[str, list[Entity]] = {e.key: [] for e in master_ents}
    remaining_board = list(board_ents)

    m_bg = next((e for e in master_ents if e.kind == "bg"), None)
    b_bg = next((e for e in remaining_board if e.kind == "bg"), None)
    if m_bg and b_bg:
        out[m_bg.key] = [b_bg]
        remaining_board.remove(b_bg)

    # shopname/text entities matched together by nearest relative height,
    # NOT restricted to matching kind==kind: find_shopname_ids only
    # recognizes a board's own shopname text when it happens to still say
    # the *master's* old shop name (e.g. a same-shop resize) - for every
    # other real shop, its shopname text gets the generic "text" role
    # fallback on the board side even though the master's own copy is
    # correctly tagged "shopname". Relative height reliably tells the three
    # apart regardless (shopname text is consistently the smallest
    # fraction of page height, phone/GST the largest, footer in between -
    # confirmed across all 13 cached dalmia real files).
    for m in [e for e in master_ents if e.kind in ("text", "shopname")]:
        candidates = [b for b in remaining_board if b.kind in ("text", "shopname")]
        if not candidates:
            continue
        best = min(candidates, key=lambda b: abs(b.bbox["h"] / board_h - m.bbox["h"] / master_h))
        out[m.key] = [best]
        remaining_board.remove(best)

    # logo_cluster entities: every remaining logo_cluster board entity gets
    # assigned to whichever master cluster it's closest to by relative
    # height - naturally producing repeats (several board clusters -> one
    # master entity) or a "never matched" master entity (dropped for this
    # board) without hardcoding either case.
    master_logo = [e for e in master_ents if e.kind == "logo_cluster"]
    for b in [e for e in remaining_board if e.kind == "logo_cluster"]:
        if not master_logo:
            break
        best = min(master_logo, key=lambda m: abs(b.bbox["h"] / board_h - m.bbox["h"] / master_h))
        out[best.key].append(b)

    return out


def build_examples(master_ents: list[Entity], master_w: float, master_h: float,
                    boards: list[tuple[float, float, list[Entity]]]) -> dict:
    """boards: list of (board_w_mm, board_h_mm, board_entities). Returns the
    examples.json-shaped dict: one record per board, each entity's transform
    relative to the master (proportional centre position, proportional
    size, repeat count) rather than absolute mm - so a transform can be
    replayed against any master of the same layout regardless of its own
    page size.
    """
    records = []
    for board_w, board_h, board_ents in boards:
        matched = match_entities(master_ents, master_w, master_h, board_ents, board_w, board_h)
        entities_out = {}
        for key, matches in matched.items():
            if not matches:
                entities_out[key] = {"present": False}
                continue
            # for a repeated logo_cluster, record every copy's transform;
            # for everything else there's exactly one match
            copies = []
            for b in matches:
                cx, cy = b.bbox["x"] + b.bbox["w"] / 2, b.bbox["y"] + b.bbox["h"] / 2
                copies.append({
                    "cx_frac": cx / board_w, "cy_frac": cy / board_h,
                    "w_frac": b.bbox["w"] / board_w, "h_frac": b.bbox["h"] / board_h,
                })
            entities_out[key] = {"present": True, "repeat_count": len(copies), "copies": copies}
        records.append({
            "width_mm": board_w, "height_mm": board_h, "aspect": round(board_w / board_h, 4),
            "entities": entities_out,
        })
    return {"master_w_mm": master_w, "master_h_mm": master_h, "boards": records}


def save_examples(brand: str, examples: dict) -> Path:
    out_dir = BRAND_DATA_ROOT / brand
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "examples.json"
    path.write_text(json.dumps(examples, indent=2), encoding="utf-8")
    return path


def load_examples(brand: str) -> dict | None:
    path = BRAND_DATA_ROOT / brand / "examples.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------
# Dalmia wide-panel rule (see CLAUDE.md "Wide-board panel sequence"):
# hand-verified against real rendered PNGs, NOT learned by the generic
# nearest-relative-height matcher above. That matcher was tried first and
# demonstrably fails here - the master's small top-right badge, once
# enlarged into a card on a wide board, has a height fraction close to the
# Tamil card's, not its own un-enlarged master size, so height-only
# matching mistakes "the enlarged badge" for "a second copy of the Tamil
# card" (confirmed by inspecting build_examples.py's output: logo_cluster_0
# got matched twice per wide board, logo_cluster_2 - the real badge -
# matched to an unrelated full-width decorative strip instead). This is
# the same failure mode `derive_brand_rules.py` hit (see "Per-brand tiling
# rule attempt") via a different algorithm - strong, convergent evidence
# it's a fundamental limit of geometry-only matching for this master, not
# a bug worth continuing to patch algorithmically without a content/colour
# signal this codebase doesn't extract.
#
# Height fractions and vertical centres below are averaged from the 4
# sampled wide boards' CORRECTLY-identified entities (read by hand from
# their real rendered PNGs - dataset_analysis/compare/*/real.png - not
# from the buggy automatic match). Panel count: 3 up to aspect ~4.25, 4
# beyond - the exact threshold is bounded only to "somewhere in (4.0, 4.5]"
# by a single sample at 4.5; not fitted further without more real data.
DALMIA_WIDE_SEQUENCE_3 = ["logo_cluster_0", "logo_cluster_1", "logo_cluster_2"]  # Tamil, Roof, enlarged-badge
DALMIA_WIDE_SEQUENCE_4 = ["logo_cluster_0", "logo_cluster_1", "logo_cluster_2", "logo_cluster_1"]
DALMIA_WIDE_PANEL_ASPECT_SPLIT = 4.25
DALMIA_WIDE_PANEL_H_FRAC = {"logo_cluster_0": 0.64, "logo_cluster_1": 0.47, "logo_cluster_2": 0.62}
DALMIA_WIDE_PANEL_CY_FRAC = {"logo_cluster_0": 0.53, "logo_cluster_1": 0.57, "logo_cluster_2": 0.53}


def dalmia_wide_panel_entities(master_ents: list[Entity], target_w: float, target_h: float) -> dict[str, dict]:
    """Predict logo_cluster entities for a wide dalmia target using the
    hand-verified rule above, instead of the generic example matcher.
    Non-logo entities (bg/text/shopname) aren't handled here - the caller
    combines this with the generic nearest-example prediction for those.
    """
    aspect = target_w / target_h
    sequence = DALMIA_WIDE_SEQUENCE_4 if aspect > DALMIA_WIDE_PANEL_ASPECT_SPLIT else DALMIA_WIDE_SEQUENCE_3
    by_key = {e.key: e for e in master_ents if e.kind == "logo_cluster"}
    n = len(sequence)

    copies_by_key: dict[str, list[dict]] = {}
    for i, key in enumerate(sequence):
        m = by_key.get(key)
        if m is None:
            continue
        h_frac = DALMIA_WIDE_PANEL_H_FRAC[key]
        master_aspect = m.bbox["w"] / m.bbox["h"] if m.bbox["h"] else 1.0
        w_frac = h_frac * target_h * master_aspect / target_w
        cx_frac = (i + 0.5) / n
        copies_by_key.setdefault(key, []).append({
            "cx_frac": cx_frac, "cy_frac": DALMIA_WIDE_PANEL_CY_FRAC[key],
            "w_frac": w_frac, "h_frac": h_frac,
        })

    out = {}
    for key in by_key:
        copies = copies_by_key.get(key, [])
        out[key] = {"present": bool(copies), "repeat_count": len(copies), "copies": copies} if copies else {"present": False}
    return out


def _nearest_boards(examples: dict, target_w: float, target_h: float, k: int = 2) -> list[dict]:
    target_aspect = target_w / target_h
    boards = sorted(examples["boards"], key=lambda b: abs(b["aspect"] - target_aspect))
    return boards[:k]


def predict_entities(examples: dict, target_w: float, target_h: float,
                      exact_tolerance: float = 0.03) -> dict[str, dict] | None:
    """Predict every entity's transform for a target size from the nearest
    example(s), by aspect ratio then interpolating if no example is close.

    Returns {entity_key: {"present": bool, "copies": [{"cx_frac", ...}, ...]}}
    or None if there are no examples to predict from at all (caller should
    fall back to the rule-based `compute_layout`).
    """
    if not examples or not examples.get("boards"):
        return None

    target_aspect = target_w / target_h
    nearest = _nearest_boards(examples, target_w, target_h, k=2)
    if not nearest:
        return None

    closest = nearest[0]
    if abs(closest["aspect"] - target_aspect) / target_aspect <= exact_tolerance:
        return closest["entities"]

    if len(nearest) < 2 or nearest[1]["aspect"] == closest["aspect"]:
        return closest["entities"]  # nothing to interpolate against

    a, b = nearest[0], nearest[1]
    if a["aspect"] == b["aspect"]:
        return a["entities"]
    t = (target_aspect - a["aspect"]) / (b["aspect"] - a["aspect"])
    t = max(0.0, min(1.0, t))  # extrapolation is clamped to the nearer sample, not projected past it

    out: dict[str, dict] = {}
    keys = set(a["entities"]) | set(b["entities"])
    for key in keys:
        ea, eb = a["entities"].get(key, {"present": False}), b["entities"].get(key, {"present": False})
        if not ea.get("present") and not eb.get("present"):
            out[key] = {"present": False}
            continue
        if not ea.get("present") or not eb.get("present"):
            out[key] = ea if ea.get("present") else eb  # only one side has it - use that, don't invent an interpolation
            continue
        # interpolate copy-by-copy where counts match; otherwise use the nearer side's copies unchanged
        if len(ea["copies"]) != len(eb["copies"]):
            out[key] = ea if t < 0.5 else eb
            continue
        copies = []
        for ca, cb in zip(ea["copies"], eb["copies"]):
            copies.append({k: ca[k] + (cb[k] - ca[k]) * t for k in ca})
        out[key] = {"present": True, "repeat_count": len(copies), "copies": copies}
    return out


# Brand-specific overrides for the generic matcher's known-bad regime (see
# dalmia_wide_panel_entities' docstring). Keyed by brand name; a brand with
# no entry here just uses the generic nearest/interpolated prediction for
# every entity, including logo_cluster, for better or worse.
WIDE_PANEL_OVERRIDES = {"dalmia": dalmia_wide_panel_entities}


def predict_layout(brand: str, master_ents: list[Entity], master_w: float, master_h: float,
                    examples: dict, target_w: float, target_h: float) -> dict[str, dict] | None:
    """Full entity prediction for a target size: the generic nearest
    -example/interpolated prediction for every entity, with logo_cluster
    entities overridden by a brand-specific hand-verified rule (if one is
    registered in WIDE_PANEL_OVERRIDES) whenever the target is wide/tall
    enough to trigger tiling - the regime the generic matcher is known to
    get wrong for dalmia (see above). Returns None if there's no example
    data to predict from at all (caller should fall back to the rule-based
    `compute_layout`).
    """
    generic = predict_entities(examples, target_w, target_h)
    if generic is None:
        return None

    rx, ry = target_w / master_w, target_h / master_h
    is_wide_or_tall = max(rx, ry) > TILE_ASPECT_THRESHOLD
    override_fn = WIDE_PANEL_OVERRIDES.get(brand)
    if is_wide_or_tall and override_fn is not None:
        override = override_fn(master_ents, target_w, target_h)
        for key, value in override.items():
            generic[key] = value
    return generic
