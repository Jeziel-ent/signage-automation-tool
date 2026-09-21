"""Product slots: which shapes of a master template are the *variable content* of a board.

A product-based board is a master whose product photo(s), brand/product titles and
contact block change per job while everything else stays. This module is the
registry that maps scene shapes (see scene_ops.py for the scene model) to those
semantic slots, and the geometry for swapping an image into a slot:

    slot kind        target shape
    product_image    a bitmap - top-level, or nested inside a PowerClip
    brand_title      a text shape
    product_title    a text shape
    address          a text shape
    contact          a text shape (phone / GST block)

Like layout.py's roles, a slot is decided by a name-prefix tag set in CorelDRAW's
Object Manager (case-insensitive: `product_image_1`, `brand_title`, `Address`...).
Untagged shapes fall back to heuristics only where a reliable signal exists: a
bitmap that is not a page-sized background is an image slot, and text carrying a
"Phone No." / "GST NO." label (the same label layout.find_contact_ids keys on) is
the contact slot. Titles and addresses have NO untagged signal - a master must
tag them, exactly as with `shopname` (see CLAUDE.md "Shop name replacement").

Image geometry: the image is fitted into a *frame* - the PowerClip frame when the
bitmap sits inside one (the clip is what the designer sees), otherwise the slot's
own box, remembered as `slot_frame` on the node at the first swap so repeated
swaps do not shrink into the previous swap's result. `contain` shows the whole
image centred in the frame; `cover` fills the frame and lets the PowerClip clip the
overflow (so it needs a PowerClip). This is a scene-level model only: `swap_image`/
`update_product_slot` update the editor's scene (and so the canvas and the saved op
list), exactly like every other op, but `export_replay.Replayer` has no handler for
either yet - replaying an image swap through COM (importing the new bitmap into
CorelDRAW) is future work, not built as part of this module.

scene_ops.py imports this module lazily inside its two slot operations (this module
needs scene_ops' index helpers, so a top-level import there would be circular);
frontend/src/editor/ops.js mirrors the constants, `aspectFit`, `resolveFrame` and
`checkAsset` so both implementations run the same golden cases.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from . import scene_ops
from .scene_ops import OpError

SLOT_PRODUCT_IMAGE = "product_image"
SLOT_BRAND_TITLE = "brand_title"
SLOT_PRODUCT_TITLE = "product_title"
SLOT_ADDRESS = "address"
SLOT_CONTACT = "contact"

IMAGE_KINDS = (SLOT_PRODUCT_IMAGE,)
TEXT_KINDS = (SLOT_BRAND_TITLE, SLOT_PRODUCT_TITLE, SLOT_ADDRESS, SLOT_CONTACT)
SLOT_KINDS = IMAGE_KINDS + TEXT_KINDS

# name prefixes (lower case), checked in this order
TAGS: dict[str, tuple[str, ...]] = {
    SLOT_PRODUCT_IMAGE: ("product_image", "product_img", "productimage"),
    SLOT_BRAND_TITLE: ("brand_title", "brandtitle"),
    SLOT_PRODUCT_TITLE: ("product_title", "producttitle"),
    SLOT_ADDRESS: ("address",),
    SLOT_CONTACT: ("contact",),
}

FITS = ("contain", "cover")
BG_AREA_RATIO = 0.9            # a bitmap covering this much of the page is a background, not a product photo
FRAME_TOL_MM = 0.5             # how far a supplied frame may differ from the real PowerClip frame
CONTACT_RE = re.compile(r"phone\s*no|gst\s*no", re.IGNORECASE)


def _r(v: float) -> float:
    return round(float(v), scene_ops.ROUND)


def kind_from_name(name: str | None) -> str | None:
    n = (name or "").strip().lower()
    for kind, prefixes in TAGS.items():
        if any(n.startswith(p) for p in prefixes):
            return kind
    return None


def is_bitmap(node: dict) -> bool:
    return node.get("kind") == "shape" and node.get("type") == "bitmap"


# ------------------------------------------------------------------ geometry

def check_asset(asset) -> dict:
    """Normalises an asset descriptor: {"name": str, "w": px, "h": px} (natural pixel size)."""
    try:
        name = str(asset["name"]).strip()
        w, h = float(asset["w"]), float(asset["h"])
    except (KeyError, TypeError, ValueError):
        name, w, h = "", 0.0, 0.0
    if not name or not (w > 0 and h > 0):
        raise OpError("asset must be an object with a name and positive numeric w, h (pixels)")
    return {"name": name, "w": w, "h": h}


def aspect_fit(asset_w: float, asset_h: float, frame: dict, fit: str = "contain", padding: float = 0.0) -> dict:
    """Box (mm) for an image of `asset_w` x `asset_h` (any unit - only the ratio counts) inside `frame`,
    centred; `contain` = whole image visible, `cover` = frame completely filled (overflow is clipped by
    the PowerClip). `padding` (mm) insets the frame on every side first."""
    if fit not in FITS:
        raise OpError("fit must be 'contain' or 'cover'")
    if not (asset_w > 0 and asset_h > 0):
        raise OpError("asset must be an object with a name and positive numeric w, h (pixels)")
    if not padding >= 0:
        raise OpError("padding must be zero or positive")
    fx, fy = frame["x"] + padding, frame["y"] + padding
    fw, fh = frame["w"] - 2 * padding, frame["h"] - 2 * padding
    if fw < scene_ops.MIN_SIZE or fh < scene_ops.MIN_SIZE:
        raise OpError("padding leaves no room inside the frame")
    scale = min(fw / asset_w, fh / asset_h) if fit == "contain" else max(fw / asset_w, fh / asset_h)
    w, h = asset_w * scale, asset_h * scale
    return {"x": _r(fx + (fw - w) / 2), "y": _r(fy + (fh - h) / 2), "w": _r(w), "h": _r(h)}


def _box(node: dict) -> dict:
    return {k: float(node[k]) for k in ("x", "y", "w", "h")}


def clip_ancestor(idx: dict, node_id: str) -> dict | None:
    """Nearest PowerClip container above `node_id`, or None."""
    p = idx[node_id]["parent"]
    while p is not None:
        if p.get("kind") == "powerclip":
            return p
        p = idx[p["id"]]["parent"]
    return None


def resolve_frame(idx: dict, node_id: str, fit: str = "contain", explicit: dict | None = None) -> tuple[dict, str | None]:
    """(frame, powerclip id) an image is fitted into. Inside a PowerClip the frame is always the
    container's box - a supplied `explicit` frame that disagrees is refused rather than silently
    ignored (a stale client would otherwise fit into the wrong box). Otherwise: `explicit`, else the
    frame remembered from an earlier swap, else the node's own box. `cover` needs a PowerClip."""
    node = idx[node_id]["node"]
    clip = clip_ancestor(idx, node_id)
    given = None
    if explicit is not None:
        given = scene_ops._bbox_arg(explicit, "frame")
        if given["w"] < scene_ops.MIN_SIZE or given["h"] < scene_ops.MIN_SIZE:
            raise OpError("frame width/height must be positive")
    if clip is not None:
        frame = _box(clip)
        if given is not None and any(abs(given[k] - frame[k]) > FRAME_TOL_MM for k in frame):
            raise OpError("frame differs from the PowerClip frame the image is inside")
        return frame, clip["id"]
    if fit == "cover":
        raise OpError("'cover' needs the image to be inside a PowerClip so the overflow is clipped")
    frame = given or (dict(node["slot_frame"]) if node.get("slot_frame") else _box(node))
    return frame, None


# ---------------------------------------------------------------- operations

def swap_image_op(scene: dict, node_id: str, asset: dict, fit: str = "contain", padding: float = 0.0) -> dict:
    """A self-contained `swap_image` op for `scene`: the resolved frame is embedded for a top-level
    image (so replaying the op later does not depend on what the node looked like then); inside a
    PowerClip the container is the frame and none is stored."""
    idx = scene_ops._index(scene)
    scene_ops._need(idx, node_id)
    frame, clip_id = resolve_frame(idx, node_id, fit)
    op: dict = {"op": "swap_image", "id": node_id, "asset": check_asset(asset), "fit": fit}
    if padding:
        op["padding"] = padding
    if clip_id is None:
        op["frame"] = {k: _r(v) for k, v in frame.items()}
    return op


def plan_image_swap(scene: dict, node_id: str, asset: dict, fit: str = "contain", padding: float = 0.0) -> dict:
    """What a swap would do, without applying it: {"id", "frame", "container_id", "box"}."""
    idx = scene_ops._index(scene)
    scene_ops._need(idx, node_id)
    a = check_asset(asset)
    frame, clip_id = resolve_frame(idx, node_id, fit)
    return {"id": node_id, "frame": frame, "container_id": clip_id,
            "box": aspect_fit(a["w"], a["h"], frame, fit, padding)}


# ------------------------------------------------------------------ registry

@dataclass
class ProductSlot:
    slot_id: str                    # "<kind>:<node id>" - stable for a given scene
    kind: str
    node_id: str                    # the shape whose image / text changes
    container_id: str | None        # PowerClip the shape sits in (image slots only)
    frame: dict | None              # box an image is fitted into (image slots only)
    source: str                     # "tag" | "heuristic"
    name: str

    def to_dict(self) -> dict:
        return asdict(self)


def _visible_chain(idx: dict, node_id: str) -> bool:
    e = idx[node_id]
    if e["layer"].get("visible") is False:
        return False
    n = e["node"]
    while n is not None:
        if n.get("visible") is False:
            return False
        p = idx[n["id"]]["parent"]
        n = p
    return True


def map_slots(scene: dict) -> tuple[list[ProductSlot], list[str]]:
    """Maps a scene's shapes to product slots, in drawing order (bottom -> top).
    Returns (slots, warnings); a warning is a tagged shape that cannot be a slot."""
    idx = scene_ops._index(scene)
    page_area = float(scene["page"]["width"]) * float(scene["page"]["height"])
    slots: list[ProductSlot] = []
    warnings: list[str] = []
    claimed: set[str] = set()

    def image_slot(target: dict, source: str, name: str) -> None:
        if target["id"] in claimed:
            return
        claimed.add(target["id"])
        frame, clip_id = None, None
        try:
            frame, clip_id = resolve_frame(idx, target["id"])
        except OpError:
            pass
        if frame is not None:
            frame = {k: _r(v) for k, v in frame.items()}
        slots.append(ProductSlot(f"{SLOT_PRODUCT_IMAGE}:{target['id']}", SLOT_PRODUCT_IMAGE, target["id"], clip_id, frame, source, name))

    def text_slot(node: dict, kind: str, source: str) -> None:
        if node["id"] in claimed:
            return
        claimed.add(node["id"])
        slots.append(ProductSlot(f"{kind}:{node['id']}", kind, node["id"], None, None, source, node.get("name") or ""))

    nodes = list(scene_ops.iter_nodes(scene))

    # 1. tags win over heuristics
    for n in nodes:
        kind = kind_from_name(n.get("name"))
        if kind is None:
            continue
        if kind in IMAGE_KINDS:
            if is_bitmap(n):
                image_slot(n, "tag", n.get("name") or "")
            elif n.get("kind") in ("powerclip", "group"):
                bitmaps = [c for c in _descendants(n) if is_bitmap(c) and c.get("visible") is not False]
                if not bitmaps:
                    warnings.append(f"{n['id']} is tagged {kind} but holds no bitmap to swap")
                    continue
                if len(bitmaps) > 1:
                    warnings.append(f"{n['id']} is tagged {kind} and holds {len(bitmaps)} bitmaps - using the largest")
                target = max(bitmaps, key=lambda b: b["w"] * b["h"])
                image_slot(target, "tag", n.get("name") or "")
            else:
                warnings.append(f"{n['id']} is tagged {kind} but is a {n.get('type')}, not a bitmap")
        else:
            if n.get("text"):
                text_slot(n, kind, "tag")
            else:
                warnings.append(f"{n['id']} is tagged {kind} but is not a text shape")

    # 2. heuristics for what is still unclaimed
    for n in nodes:
        if n["id"] in claimed or n.get("visible") is False or not _visible_chain(idx, n["id"]):
            continue
        if is_bitmap(n) and kind_from_name(n.get("name")) is None:
            if page_area > 0 and n["w"] * n["h"] >= BG_AREA_RATIO * page_area:
                continue                                            # a page-sized photo is the background
            image_slot(n, "heuristic", n.get("name") or "")
        elif n.get("text") and CONTACT_RE.search(str(n["text"].get("content") or "")) and kind_from_name(n.get("name")) is None:
            text_slot(n, SLOT_CONTACT, "heuristic")

    for s in slots:
        clip = idx[s.node_id]["parent"]
        if s.kind in IMAGE_KINDS and s.container_id and idx[s.container_id]["node"].get("frame_rect") is False:
            warnings.append(f"{s.slot_id}: its PowerClip frame is not a plain rectangle - images are fitted to the frame's bounding box")
    order = {n["id"]: i for i, n in enumerate(nodes)}
    slots.sort(key=lambda s: order[s.node_id])
    return slots, warnings


def _descendants(node: dict):
    for c in node.get("children") or []:
        yield c
        yield from _descendants(c)


def slot_for_node(scene: dict, node_id: str) -> ProductSlot | None:
    slots, _ = map_slots(scene)
    return next((s for s in slots if s.node_id == node_id), None)


def update_slot_op(scene: dict, slot_id: str, *, asset: dict | None = None, text: str | None = None,
                   fit: str = "contain", padding: float = 0.0) -> dict:
    """An `update_product_slot` op for a slot found by map_slots: an image slot takes `asset`, the
    text slots take `text`."""
    slots, _ = map_slots(scene)
    slot = next((s for s in slots if s.slot_id == slot_id), None)
    if slot is None:
        raise OpError(f"unknown product slot {slot_id!r}")
    op: dict = {"op": "update_product_slot", "id": slot.node_id, "kind": slot.kind}
    if slot.kind in IMAGE_KINDS:
        if asset is None or text is not None:
            raise OpError(f"a {slot.kind} slot takes 'asset', not 'text'")
        swap = swap_image_op(scene, slot.node_id, asset, fit, padding)
        op.update({k: v for k, v in swap.items() if k not in ("op", "id")})
    else:
        if text is None or asset is not None:
            raise OpError(f"a {slot.kind} slot takes 'text', not 'asset'")
        op["text"] = str(text)
    return op
