"""Phase D: replay the editor's operation list on the converted .cdr through
CorelDRAW COM, then export the chosen formats. Every output file is written by
CorelDRAW itself - the browser (and this module) never renders or re-encodes.

How the replay stays honest
---------------------------
`scene_ops.apply_op` is the contract for what each operation means. The
`Replayer` keeps a *shadow scene* and applies every operation to it first
(so an operation that cannot be applied raises the same OpError as in the
editor), then performs the matching CorelDRAW action. After the last
operation the real document is walked again (`scene_export.walk_page`) and
compared with the shadow scene: structure, z-order, positions, visibility and
text. Mismatches are reported, never hidden.

CorelDRAW behaviours this relies on (all verified live, see CLAUDE.md
"Phase D"): `Shape.LeftX/BottomY` are assignable; `OrderForwardOne/BackOne`
swap with the adjacent sibling; `MoveToLayer` also pulls a shape OUT of a
group; there is no API to add a shape to an existing group, so that is done by
ungrouping and regrouping (the group is re-registered under the same id);
`Shape.Ungroup()` returns nothing; JPEG quality is NOT controllable through
COM, so it is not offered.

Objects that a later `paste` copies (cut + paste) are hidden instead of
deleted until the end, because a deleted COM shape cannot be duplicated.
"""
from __future__ import annotations

import copy
import re
import time
from pathlib import Path
from typing import Callable

from . import corel_util, scene_export, scene_ops
from .scene_ops import OpError

ALL_FORMATS = ("cdr", "pdf", "png", "jpeg")
EXTENSION = {"cdr": "cdr", "pdf": "pdf", "png": "png", "jpeg": "jpg"}
FILTER = {"png": 802, "jpeg": 774}

MAX_SIDE_PX = 20000
MAX_MEGAPIXELS = 200.0
MIN_DPI, MAX_DPI = 1, 1200
PDF_COLOR = {"native": 3, "rgb": 0, "cmyk": 1}     # pdfColorMode
PDF_DPI_CHOICES = (72, 100, 150, 200, 300, 600)
RASTER_FORMATS = ("png", "jpeg")
IMAGE_TYPE = {"rgb": scene_export.CDR_RGB_IMAGE, "cmyk": 5}   # cdrImageType: cdrRGBColorImage / cdrCMYKColorImage
CDR_VERSION_CHOICES = (21, 0, 17)       # cdrFileVersion: 2019 (the designers' version), current (native), X7
MAX_PADDING_MM = 500.0
POS_TOL_MM = 0.5
TEXT_TOL_MM = 5.0        # position tolerance for text whose content was edited


class ExportOptionError(ValueError):
    """The requested export options are invalid or exceed a safety limit."""


class ReplayError(Exception):
    """An operation is valid in the editor but cannot be reproduced in CorelDRAW."""


# ------------------------------------------------------------------ options

def _raster_options(base: dict, fmt: str, override: dict) -> dict:
    """One raster format's settings: the shared `raster` block with that format's own block laid over it."""
    merged = {**base, **{k: v for k, v in override.items() if v is not None}}
    mode = merged.get("mode", "max_px")
    if mode not in ("dpi", "max_px"):
        raise ExportOptionError("raster size mode must be 'dpi' or 'max_px'")
    background = merged.get("png_background", "transparent")
    if background not in ("transparent", "white"):
        raise ExportOptionError("PNG background must be 'transparent' or 'white'")
    out = {
        "mode": mode,
        "dpi": float(merged.get("dpi", 96)),
        "max_px": int(merged.get("max_px", 4000)),
        "png_background": background,
        "antialias": bool(merged.get("antialias", True)),
    }
    if fmt == "png":
        pad = float(merged.get("padding_mm", 0) or 0)
        if not 0 <= pad <= MAX_PADDING_MM:
            raise ExportOptionError(f"PNG padding must be between 0 and {MAX_PADDING_MM:g} mm")
        out["padding_mm"] = pad
    if fmt == "jpeg":
        color = merged.get("color", "rgb")
        if color not in IMAGE_TYPE:
            raise ExportOptionError("JPEG colour mode must be 'rgb' or 'cmyk'")
        out["color"] = color
    return out


def raster_sizes(page_w_mm: float, page_h_mm: float, opts: dict, formats: list[str]) -> dict:
    """Pixel size per requested raster format (a PNG's padding widens the exported area on every side)."""
    out = {}
    for f in RASTER_FORMATS:
        if f in formats:
            pad = 2 * opts[f].get("padding_mm", 0)
            out[f] = resolve_raster(page_w_mm + pad, page_h_mm + pad, opts[f])
    return out


def normalize_options(formats: list[str], options: dict | None) -> dict:
    """Validated export options. `raster` is the block shared by PNG and JPEG (the editor's original popup sent only
    that); `png` / `jpeg` override it per format (the row-level export modal sends those), and the result carries a
    fully resolved dict for each of the two formats."""
    options = options or {}
    if not formats:
        raise ExportOptionError("choose at least one format")
    bad = [f for f in formats if f not in ALL_FORMATS]
    if bad:
        raise ExportOptionError(f"unknown format(s): {', '.join(bad)}")
    pdf = options.get("pdf") or {}
    color = pdf.get("color_mode", "native")
    text = pdf.get("text", "embed")
    dpi = int(pdf.get("bitmap_dpi", 200))
    if color not in PDF_COLOR:
        raise ExportOptionError(f"PDF colour mode must be one of {sorted(PDF_COLOR)}")
    if text not in ("embed", "curves"):
        raise ExportOptionError("PDF text must be 'embed' or 'curves'")
    if dpi not in PDF_DPI_CHOICES:
        raise ExportOptionError(f"PDF image resolution must be one of {PDF_DPI_CHOICES}")
    cdr = options.get("cdr") or {}
    version = cdr.get("version")
    if version is not None:
        version = int(version)
        if version not in CDR_VERSION_CHOICES:
            raise ExportOptionError(f"CDR version must be one of {CDR_VERSION_CHOICES}")
    cdr_text = cdr.get("text", "editable")
    if cdr_text not in ("editable", "curves"):
        raise ExportOptionError("CDR text must be 'editable' or 'curves'")
    raster = options.get("raster") or {}
    return {
        "pdf": {"color_mode": color, "text": text, "bitmap_dpi": dpi,
                "crop_marks": bool(pdf.get("crop_marks", False)), "bleed": bool(pdf.get("bleed", False))},
        "cdr": {"version": version, "text": cdr_text},
        "raster": _raster_options(raster, "raster", {}),
        **{f: _raster_options(raster, f, options.get(f) or {}) for f in RASTER_FORMATS},
    }


def resolve_raster(page_w_mm: float, page_h_mm: float, raster: dict) -> dict:
    """dpi + pixel size for PNG/JPEG, or ExportOptionError when it would be unsafely large."""
    longest_in = max(page_w_mm, page_h_mm) / 25.4
    if raster["mode"] == "max_px":
        if raster["max_px"] < 16:
            raise ExportOptionError("longest side must be at least 16 px")
        dpi = raster["max_px"] / longest_in
    else:
        dpi = raster["dpi"]
    dpi = max(MIN_DPI, min(MAX_DPI, dpi))
    w_px = max(1, round(page_w_mm / 25.4 * dpi))
    h_px = max(1, round(page_h_mm / 25.4 * dpi))
    mp = w_px * h_px / 1e6
    if max(w_px, h_px) > MAX_SIDE_PX:
        raise ExportOptionError(
            f"{w_px} x {h_px} px is too large (limit {MAX_SIDE_PX} px on the longest side) - lower the resolution")
    if mp > MAX_MEGAPIXELS:
        raise ExportOptionError(f"{mp:.0f} megapixels is too large (limit {MAX_MEGAPIXELS:.0f}) - lower the resolution")
    return {"dpi": round(dpi, 2), "w_px": w_px, "h_px": h_px, "megapixels": round(mp, 2)}


DEFAULT_SECONDS = {"launch": 8, "open": 6, "replay": 3, "verify": 3, "cdr": 4, "pdf": 8, "png": 10, "jpeg": 8}


def export_order(formats: list[str], opts: dict | None = None) -> list[str]:
    """The order formats are written in. A CDR with text converted to curves is written LAST: converting changes the
    open document, and the PDF/PNG/JPEG must still be made from the editable text."""
    order = [f for f in ALL_FORMATS if f in formats]
    if "cdr" in order and opts and (opts.get("cdr") or {}).get("text") == "curves":
        order.remove("cdr")
        order.append("cdr")
    return order


def plan_steps(formats: list[str], n_ops: int, estimates: dict | None = None, opts: dict | None = None) -> list[dict]:
    """Ordered steps with the cumulative percent reached when each is confirmed complete.
    Widths follow measured average durations of earlier exports when there are any."""
    keys = ["launch", "open"]
    if n_ops:
        keys += ["replay", "verify"]
    keys += export_order(formats, opts)
    est = estimates or {}
    secs = [float(est.get(k) or DEFAULT_SECONDS[k]) for k in keys]
    total = sum(secs)
    steps, acc = [], 0.0
    for k, s in zip(keys, secs):
        acc += s
        steps.append({"key": k, "endPct": min(100, round(acc / total * 100))})
    steps[-1]["endPct"] = 100
    return steps


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_.") or "board"


# ------------------------------------------------------------ COM helpers

_safe = scene_export._safe
_children = scene_export._children


def index_doc(page) -> tuple[dict, dict]:
    """node id -> COM shape and layer id -> COM layer, using exactly the ids scene_export assigns.

    The traversal ORDER matters, not just the id format: CorelDRAW hands out a
    StaticID to a PowerClip's contents the first time each is read, so the id a
    shape gets depends on the order they are visited. scene_export.walk_shape reads a
    container's id, then descends into its children bottom -> top
    (`reversed(_children(...))`); this must visit them in that same order or the
    same shape gets a different id here than in the scene the editor edited.
    Verified live on a real PowerClip: with a top-first walk here, the scene's `s48` (a
    341x339 mm bitmap) was `s208` in the replay document, and the scene's `s47` (a
    772x799 mm photo) was a different, thin group - so a replayed move/resize
    would have hit the wrong shape or none. Replayer._check_scene_matches_doc
    guards against any remaining drift.
    """
    shapes: dict = {}
    layers: dict = {}

    def walk(shape):
        shapes[f"s{int(shape.StaticID)}"] = shape
        pc = _safe(lambda: shape.PowerClip)
        if pc is not None:
            for c in reversed(_children(pc)):
                walk(c)
        elif int(shape.Type) == 7:
            for c in reversed(_children(shape)):
                walk(c)

    for i in range(1, int(page.Layers.Count) + 1):
        layer = page.Layers.Item(i)
        if _safe(lambda: layer.IsSpecialLayer, False):
            continue
        layers[f"L{i}"] = layer
        for s in _children(layer):
            walk(s)
    return shapes, layers


def _set_bbox(shape, box: dict) -> None:
    w, h = max(float(box["w"]), 0.01), max(float(box["h"]), 0.01)
    shape.SetSize(w, h)
    shape.LeftX = float(box["x"])
    shape.BottomY = float(box["y"])


def _subtree_ids(node: dict) -> list[str]:
    out = [node["id"]]
    for c in node.get("children") or []:
        out += _subtree_ids(c)
    return out


def _clip_ancestor_id(idx: dict, node_id: str) -> str | None:
    """The nearest PowerClip container above `node_id` in a scene _index(), however many groups deep -
    the same walk as product_engine.clip_ancestor, needed here because a `swap_image` target's DIRECT
    parent is not always the PowerClip itself (found live: a bitmap can sit inside an ordinary group
    that is in turn inside the PowerClip - see _op_swap_image's docstring)."""
    p = idx[node_id]["parent"]
    while p is not None:
        if p.get("kind") == "powerclip":
            return p["id"]
        p = idx[p["id"]]["parent"]
    return None


class Replayer:
    def __init__(self, doc, scene: dict, ops: list[dict], on_progress: Callable[[int, int], None] | None = None,
                 assets_dir: Path | str | None = None):
        self.doc = doc
        self.page = doc.ActivePage
        self.ops = ops
        self.shadow = copy.deepcopy(scene)
        self.shapes, self.layers = index_doc(self.page)
        self._check_scene_matches_doc(scene)
        self.sid = {int(s.StaticID): nid for nid, s in self.shapes.items()}
        self.ghosts: dict = {}
        self.warnings: list[str] = []
        self.on_progress = on_progress or (lambda i, n: None)
        # where a swap_image/update_product_slot asset's `path` (a bare filename) resolves against -
        # the shop's own product-assets directory, passed through from main.py via corel_worker.py's
        # export_replay spec; None when no swap in this op list needs one (or, for an old spec/job
        # predating this feature, simply absent).
        self.assets_dir = Path(assets_dir) if assets_dir else None
        # objects a later paste copies must survive a delete (cut + paste)
        self.paste_sources = {n.get("src") for op in ops if op.get("op") == "paste"
                              for top in op.get("nodes", []) for n in _walk_nodes(top) if n.get("src")}

    # -------------------------------------------------------------- driver
    def run(self) -> dict:
        n = len(self.ops)
        for i, op in enumerate(self.ops, start=1):
            self.on_progress(i, n)
            try:
                self.apply(op)
            except OpError as e:
                raise ReplayError(f"operation #{i} ({op.get('op')}) is not valid on this document: {e}") from None
            except ReplayError:
                raise
            except Exception as e:  # a COM call failed
                raise ReplayError(f"operation #{i} ({op.get('op')}) failed in CorelDRAW: {e}") from e
        self._finish_deletions()
        return {"applied": n, "warnings": self.warnings}

    def apply(self, op: dict) -> None:
        before = scene_ops._index(self.shadow)
        scene_ops.apply_op(self.shadow, op)
        fn = getattr(self, "_op_" + op["op"], None)
        if fn is None:
            # A safety net for any future scene_ops.py op added without a matching _op_<name> here -
            # refuse clearly rather than silently exporting a document that doesn't reflect the edit.
            raise ReplayError(f"the {op['op']!r} operation cannot be exported to CorelDRAW yet")
        fn(op, before)

    # ------------------------------------------------------------ lookups
    def _check_scene_matches_doc(self, scene: dict) -> None:
        """Refuse to replay when the scene's ids don't line up with this document.

        Every scene node must resolve to a COM shape with the same box. Without this, ids that
        drifted (see index_doc) would silently apply an edit to a DIFFERENT shape - the failure
        mode is a wrong export that still "succeeds", so it must be an error, not a warning.
        Text nodes are only checked for existing and still being text: their measured extent
        legitimately differs between sessions (font substitution/linking), which would make a box
        comparison a false alarm that blocks valid exports.
        """
        bad: list[str] = []
        for node in scene_ops.iter_nodes(scene):
            shape = self.shapes.get(node["id"])
            if shape is None:
                bad.append(f"{node['id']} is not in the document")
            elif node.get("type") == "text":
                if int(shape.Type) != 6:
                    bad.append(f"{node['id']} is a text object in the scene but not in CorelDRAW")
            else:
                got = [float(shape.LeftX), float(shape.BottomY), float(shape.SizeWidth), float(shape.SizeHeight)]
                want = [node["x"], node["y"], node["w"], node["h"]]
                if any(abs(g - w) > max(POS_TOL_MM, 0.001 * max(want[2], want[3])) for g, w in zip(got, want)):
                    bad.append(f"{node['id']} is at {[round(v, 1) for v in got]} in CorelDRAW but {[round(v, 1) for v in want]} in the scene")
            if len(bad) >= 5:
                break
        if bad:
            raise ReplayError("the editor's scene does not match the CorelDRAW document (was the board re-converted "
                              "after the scene was built?): " + "; ".join(bad))

    def _shape(self, node_id: str):
        s = self.shapes.get(node_id) or self.ghosts.get(node_id)
        if s is None:
            raise ReplayError(f"{node_id!r} is not in the CorelDRAW document")
        return s

    def _register(self, node_id: str, shape) -> None:
        self.shapes[node_id] = shape
        self.sid[int(shape.StaticID)] = node_id

    def _forget(self, node: dict) -> None:
        for nid in _subtree_ids(node):
            s = self.shapes.pop(nid, None)
            if s is not None:
                self.sid.pop(int(_safe(lambda: s.StaticID, -1)), None)

    def _container(self, parent_id: str):
        return self.layers.get(parent_id) or self._shape(parent_id)

    # --------------------------------------------------------- operations
    def _op_move(self, op, before):
        # A PowerClip child is moved like any other shape: scene coordinates are the absolute page
        # coordinates CorelDRAW reports for it (scene_export reads LeftX/BottomY the same way), so a
        # dx/dy translation needs no conversion to the container's origin, and the clip frame itself
        # is not touched. Unverified against a real PowerClip - verify() compares the result.
        for i in scene_ops._top_ids(before, op["ids"]):
            self._shape(i).Move(float(op["dx"]), float(op["dy"]))

    def _op_resize(self, op, before):
        after = scene_ops._index(self.shadow)
        for i in scene_ops._top_ids(before, op["ids"]):
            _set_bbox(self._shape(i), after[i]["node"])

    def _op_order(self, op, before):
        s = self._shape(op["id"])
        {"front": s.OrderToFront, "back": s.OrderToBack, "forward": s.OrderForwardOne, "backward": s.OrderBackOne}[op["mode"]]()
        # A PowerClip child: the Order* calls are expected to restack it within the clip's own contents
        # (PowerClip.Shapes), but that is NOT verified live against CorelDRAW - so the clip's order is settled
        # against the shadow scene afterwards (_settle reads PowerClip.Shapes), and verify() compares it.
        parent = before[op["id"]]["parent"]
        if parent is not None and parent.get("kind") == "powerclip":
            self._settle(parent["id"])

    def _op_visibility(self, op, before):
        if op["id"] in self.layers:
            self.layers[op["id"]].Visible = bool(op["visible"])
        else:
            self._shape(op["id"]).Visible = bool(op["visible"])

    def _op_text(self, op, before):
        self._apply_text(self._shape(op["id"]), op)

    def _apply_text(self, shape, patch: dict) -> None:
        story = shape.Text.Story
        if patch.get("content") is not None:
            story.Text = str(patch["content"])
        if patch.get("font") is not None:
            story.Font = str(patch["font"])
            actual = _safe(lambda: story.Font)
            if actual != patch["font"]:
                self.warnings.append(
                    f"font {patch['font']!r} was not applied (is it installed?) - CorelDRAW kept {actual!r}")
        if patch.get("size_pt") is not None:
            story.Size = float(patch["size_pt"])
        # New text content can turn a shape Tamil (or a shape nested inside a
        # PowerClip - scene_ops.py's one narrow exception to PowerClip
        # contents otherwise being read-only - may already be Tamil and never
        # explicitly requested a font change here): same verified fix
        # app.engines.CorelEngine applies during generation, shared via
        # corel_util so both places stay in sync.
        corel_util.ensure_tamil_font_renders(shape, _safe(lambda: story.Text), self.warnings)

    def _op_page(self, op, before):
        self.page.SetSize(float(self.shadow["page"]["width"]), float(self.shadow["page"]["height"]))

    def _resolve_asset_path(self, rel_path: str) -> Path:
        """`rel_path` is a bare filename (never a full path - see product_engine.check_asset), resolved
        against this export's assets directory with the same path-containment guard main.py's own
        asset-serving routes use, so a malformed path can't escape that directory."""
        if self.assets_dir is None:
            raise ReplayError("no product-asset directory is available for this export")
        base = self.assets_dir.resolve()
        p = (base / rel_path).resolve()
        if base not in p.parents or not p.is_file():
            raise ReplayError(f"replacement image not found: {rel_path!r}")
        return p

    def _op_swap_image(self, op, before):
        """Imports the asset onto the target's own layer (`Layer.Import` - verified live: the COM
        typelib declares it VT_VOID, so the new shape is read back via `doc.ActiveShape`, not a
        return value; its optional `Options` argument is VT_DISPATCH and must get `None`, never the
        Python default `0`, same bug class as every other optional-VT_DISPATCH argument in this
        codebase - see CLAUDE.md's CorelEngine COM notes), fits it to the box scene_ops.py already
        computed on the shadow scene, and swaps it in for the old bitmap under the SAME node id, so
        later ops in this op list that reference it keep working. A target nested inside a PowerClip
        (however many groups deep - `_clip_ancestor_id` walks the whole chain, mirroring
        product_engine.clip_ancestor) is moved into the clip with `Shape.AddToPowerClip(container)`
        before the old bitmap is pulled out with `Shape.RemoveFromContainer()` and deleted (both
        verified live against a real generated board's PowerClip - deleting a clipped child directly,
        without removing it from the container first, was not tried and is not relied on).

        A target directly inside a plain (non-PowerClip) group is put back into that SAME group via
        `_merge_into_group` (the proven ungroup+regroup dance `_op_reorder`/`_op_group` already rely
        on - no PowerClip involved, so no new risk). A target inside a GROUP that is itself inside a
        PowerClip is the one case left approximate: found live that `AddToPowerClip`/
        `RemoveFromContainer` operate on the shape's overall clip membership regardless of which
        sub-group it sat in, so re-inserting into that specific sub-group was not attempted (mixing an
        ungrouped-and-regrouped set of shapes that live inside a PowerClip with a brand new shape that
        does not is exactly the kind of COM interaction this codebase only relies on once verified
        live - see CLAUDE.md's CorelEngine COM notes - and it was not tried here); the replacement
        lands as a direct child of the PowerClip, a sibling of that sub-group instead. `_settle` still
        reports this honestly as a z-order/structure mismatch rather than claiming a false match -
        every dalmia/Agarpathi master validated so far only ever nests a bitmap directly in a PowerClip
        or in one plain group inside one, per CLAUDE.md's dataset analysis, so this is a real but
        narrow gap, not the common case."""
        after = scene_ops._index(self.shadow)
        node = after[op["id"]]["node"]
        asset = node.get("image_asset") or {}
        rel_path = asset.get("path")
        if not rel_path:
            raise ReplayError(f"{op['id']!r}: the replacement asset has no uploaded file to import "
                              "(upload it through the product-assets endpoint first)")
        abs_path = self._resolve_asset_path(rel_path)
        old_shape = self._shape(op["id"])
        old_sid = int(old_shape.StaticID)
        parent = before[op["id"]]["parent"]
        container_id = _clip_ancestor_id(before, op["id"])
        layer = old_shape.Layer
        layer.Import(str(abs_path), 0, None)
        new_shape = self.doc.ActiveShape
        if new_shape is None:
            raise ReplayError(f"{op['id']!r}: CorelDRAW did not report an imported shape for {abs_path}")
        settle_id = self._parent_id(op["id"])
        if container_id is not None:
            new_shape.AddToPowerClip(self._shape(container_id), 0)
            if parent is not None and parent.get("kind") != "powerclip":
                settle_id = container_id      # landed as a direct PowerClip child, not back in the sub-group
        elif parent is not None and parent.get("kind") == "group":
            self._merge_into_group(new_shape, parent["id"])
        _set_bbox(new_shape, node)
        if container_id is not None:
            old_shape.RemoveFromContainer()
        old_shape.Delete()
        self.sid.pop(old_sid, None)
        self._register(op["id"], new_shape)
        _safe(lambda: self.doc.ClearSelection())
        self._settle(settle_id)

    def _op_update_product_slot(self, op, before):
        from . import product_engine as pe

        if op.get("kind") in pe.IMAGE_KINDS:
            self._op_swap_image(op, before)
        else:
            after = scene_ops._index(self.shadow)
            self._apply_text(self._shape(op["id"]), after[op["id"]]["node"]["text"])

    def _op_delete(self, op, before):
        for i in scene_ops._top_ids(before, op["ids"]):
            node = before[i]["node"]
            shape = self._shape(i)
            if self.paste_sources & set(_subtree_ids(node)):
                shape.Visible = False           # kept (hidden) so a later paste can still copy it
                self.ghosts[i] = shape
            else:
                shape.Delete()
                self._forget(node)
        # the shadow already dropped groups that became empty; CorelDRAW does the same

    def _finish_deletions(self) -> None:
        for i, shape in list(self.ghosts.items()):
            _safe(lambda: shape.Delete())
            self.ghosts.pop(i)

    def _op_group(self, op, before):
        shapes = [self._shape(i) for i in op["ids"]]
        g = self.doc.CreateShapeRangeFromArray(shapes).Group()
        g.Name = op.get("name") or "Group"
        self._register(op["group_id"], g)
        self._settle(self._parent_id(op["group_id"]))

    def _op_ungroup(self, op, before):
        node = before[op["id"]]["node"]
        parent = before[op["id"]]["parent"]
        layer = before[op["id"]]["layer"]["id"]
        self._shape(op["id"]).Ungroup()
        self.shapes.pop(op["id"], None)
        self._settle(parent["id"] if parent else layer)

    def _op_paste(self, op, before):
        idx = scene_ops._index(self.shadow)
        dst = op["parent"]
        layer_id = idx[dst]["layer"]["id"]
        for node in op["nodes"]:
            src_id = node.get("src")
            src = self.shapes.get(src_id) or self.ghosts.get(src_id)
            if src is None:
                raise ReplayError(f"pasted object {node['id']!r} copies {src_id!r}, which is not in the document")
            dup = src.Duplicate(0.0, 0.0)
            self._register_copy(node, dup)
            dup.Visible = bool(node.get("visible", True))
            _set_bbox(dup, node)
            if node.get("text") and _safe(lambda: dup.Text.Story.Text) != node["text"].get("content"):
                self._apply_text(dup, node["text"])
            dup.MoveToLayer(self.layers[layer_id])
            if dst != layer_id:
                self._merge_into_group(dup, dst)
        self._settle(dst)

    def _register_copy(self, node: dict, dup) -> None:
        self._register(node["id"], dup)
        if node.get("kind") == "group":
            kids = list(reversed(_children(dup)))       # Corel lists top-first
            for nk, dk in zip(node.get("children") or [], kids):
                self._register_copy(nk, dk)

    def _op_reorder(self, op, before):
        idx = scene_ops._index(self.shadow)
        src_before = before[op["id"]]
        shape = self._shape(op["id"])
        dst_id = op["parent"]
        dst_layer_id = idx[dst_id]["layer"]["id"]
        src_parent_id = src_before["parent"]["id"] if src_before["parent"] else src_before["layer"]["id"]
        if dst_id != src_parent_id:
            if dst_id == dst_layer_id:
                shape.MoveToLayer(self.layers[dst_layer_id])
            else:
                shape.MoveToLayer(self.layers[dst_layer_id])    # also pulls it out of any group it is in
                self._merge_into_group(shape, dst_id)
        self._settle(dst_id)
        if src_parent_id != dst_id and src_parent_id in idx:
            self._settle(src_parent_id)

    def _op_layer_order(self, op, before):
        order = [l["id"] for l in self.shadow["layers"]]         # bottom -> top
        i = order.index(op["id"])
        layer = self.layers[op["id"]]
        if i == len(order) - 1:
            layer.MoveAbove(self._topmost_layer(exclude=op["id"]))
        elif i == 0:
            layer.MoveBelow(self._bottommost_layer(exclude=op["id"]))
        else:
            layer.MoveAbove(self.layers[order[i - 1]])

    def _topmost_layer(self, exclude):
        for i in range(1, int(self.page.Layers.Count) + 1):
            l = self.page.Layers.Item(i)
            if not _safe(lambda: l.IsSpecialLayer, False) and l.Name != self.layers[exclude].Name:
                return l
        raise ReplayError("no other layer to order against")

    def _bottommost_layer(self, exclude):
        for i in range(int(self.page.Layers.Count), 0, -1):
            l = self.page.Layers.Item(i)
            if not _safe(lambda: l.IsSpecialLayer, False) and l.Name != self.layers[exclude].Name:
                return l
        raise ReplayError("no other layer to order against")

    # ------------------------------------------------------------ helpers
    def _parent_id(self, node_id: str) -> str:
        e = scene_ops._index(self.shadow)[node_id]
        return e["parent"]["id"] if e["parent"] else e["layer"]["id"]

    def _merge_into_group(self, shape, group_id: str) -> None:
        """No COM call adds a shape to an existing group: ungroup, then regroup with the newcomer."""
        g = self._shape(group_id)
        name, visible, locked = _safe(lambda: g.Name, ""), _safe(lambda: g.Visible, True), _safe(lambda: g.Locked, False)
        members = list(_children(g))
        old_sid = int(g.StaticID)
        g.Ungroup()
        self.sid.pop(old_sid, None)
        ng = self.doc.CreateShapeRangeFromArray(members + [shape]).Group()
        if name:
            ng.Name = name
        _safe(lambda: setattr(ng, "Visible", visible))
        _safe(lambda: setattr(ng, "Locked", locked))
        self._register(group_id, ng)
        parent = self._parent_id(group_id)
        self._settle(parent)

    def _actual_order(self, parent_id: str) -> list[str | None]:
        # A PowerClip container's own `.Shapes` is empty (or absent) like any non-group shape's - its
        # contents live on `.PowerClip.Shapes` instead (same distinction scene_export.walk_shape
        # already makes). Used after swap_image, and after an `order` or same-parent `reorder`
        # (layers-panel drag) on a PowerClip child - group/ungroup/delete and moving into or out
        # of a clip are still refused on a PowerClip's contents by scene_ops.
        ghost_sids = {int(s.StaticID) for s in self.ghosts.values()}
        out = []
        container = self._container(parent_id)
        pc = _safe(lambda: container.PowerClip)
        kids = _children(pc) if pc is not None else _children(container)
        for s in reversed(kids):     # bottom -> top
            sid = int(s.StaticID)
            if sid in ghost_sids:
                continue
            out.append(self.sid.get(sid))
        return out

    def _settle(self, parent_id: str) -> None:
        """Make CorelDRAW's z-order inside `parent_id` equal the shadow scene's."""
        idx = scene_ops._index(self.shadow)
        if parent_id not in idx:
            return
        desired = [c["id"] for c in idx[parent_id]["node"]["children"]]
        actual = self._actual_order(parent_id)
        first = next((i for i, (a, d) in enumerate(zip(actual, desired)) if a != d), None)
        if first is None and len(actual) == len(desired):
            return
        first = first if first is not None else min(len(actual), len(desired))
        for nid in desired[first:]:
            self._shape(nid).OrderToFront()      # bringing the tail to the front in order rebuilds it exactly
        left = self._actual_order(parent_id)
        if left != desired:
            self.warnings.append(f"z-order inside {parent_id} could not be matched exactly")


def _walk_nodes(node: dict):
    yield node
    for c in node.get("children") or []:
        yield from _walk_nodes(c)


# ------------------------------------------------------------ verification

def _canon(node: dict) -> dict:
    return {
        "kind": node["kind"], "type": node["type"],
        "box": (node["x"], node["y"], node["w"], node["h"]),
        "visible": node.get("visible", True),
        "text": (node.get("text") or {}).get("content"),
        "stale": bool(node.get("stale")),
        "rotation": node.get("rotation", 0),
        "children": [_canon(c) for c in node.get("children") or []],
    }


def verify(page, expected: dict, limit: int = 20) -> dict:
    """Compare the real document with the shadow scene the replay was supposed to produce."""
    layers, _ = scene_export.walk_page(page)
    mism: list[str] = []
    compared = 0

    def note(msg):
        if len(mism) < limit:
            mism.append(msg)

    exp_w, exp_h = expected["page"]["width"], expected["page"]["height"]
    got_w, got_h = float(page.SizeWidth), float(page.SizeHeight)
    if abs(exp_w - got_w) > POS_TOL_MM or abs(exp_h - got_h) > POS_TOL_MM:
        note(f"page size {got_w:.1f} x {got_h:.1f} mm, expected {exp_w:.1f} x {exp_h:.1f}")

    def cmp(got: list, want: list, path: str):
        nonlocal compared
        if len(got) != len(want):
            note(f"{path}: {len(got)} objects in CorelDRAW, expected {len(want)}")
            return
        for i, (g, w) in enumerate(zip(got, want)):
            compared += 1
            here = f"{path}[{i}]"
            if (g["kind"], g["type"]) != (w["kind"], w["type"]):
                note(f"{here}: is {g['type']}, expected {w['type']} (z-order or structure differs)")
                continue
            tol = max(POS_TOL_MM, 0.001 * max(w["box"][2], w["box"][3]))
            names = ("x", "y", "w", "h")
            if w["stale"] and w["type"] == "text":
                # New text content changes the object's size, and CorelDRAW re-anchors it by its alignment
                # (left / centre / right, bottom / middle / top): accept any consistent anchor.
                gx, gy, gw, gh = g["box"]
                wx, wy, ww, wh = w["box"]
                # glyph metrics of the new content (side bearings, descenders) shift the box by a few mm
                tol = max(TEXT_TOL_MM, 0.03 * max(gw, ww))
                ok_x = any(abs(a - b) <= tol for a, b in ((gx, wx), (gx + gw / 2, wx + ww / 2), (gx + gw, wx + ww)))
                ok_y = any(abs(a - b) <= tol for a, b in ((gy, wy), (gy + gh / 2, wy + wh / 2), (gy + gh, wy + wh)))
                if not (ok_x and ok_y):
                    note(f"{here} (text): now at {gx:.1f},{gy:.1f} mm, expected near {wx:.1f},{wy:.1f} (no left/centre/right anchor matches)")
            else:
                for k in range(4):
                    if abs(g["box"][k] - w["box"][k]) > tol:
                        note(f"{here} ({w['type']}): {names[k]} is {g['box'][k]:.2f} mm, expected {w['box'][k]:.2f}")
                        break
            if bool(g["visible"]) != bool(w["visible"]):
                note(f"{here}: visibility differs")
            if w["type"] == "text" and (g["text"] or "").strip() != (w["text"] or "").strip():
                note(f"{here}: text differs")
            if w["kind"] in ("group", "powerclip"):
                # PowerClip contents are compared too: nested text/move/resize edits (scene_ops.py's
                # PowerClip exception) are the least-verified COM behaviour here, so a mismatch
                # must surface in report.verification rather than pass silently.
                cmp(g["children"], w["children"], here)

    got_layers = [{"name": l["name"], "children": [_canon(c) for c in l["children"]]} for l in layers]
    want_layers = [{"name": l["name"], "children": [_canon(c) for c in l["children"]]} for l in expected["layers"]]
    if [l["name"] for l in got_layers] != [l["name"] for l in want_layers]:
        note(f"layer order is {[l['name'] for l in got_layers]}, expected {[l['name'] for l in want_layers]}")
    else:
        for gl, wl in zip(got_layers, want_layers):
            cmp(gl["children"], wl["children"], wl["name"])
    return {"ok": not mism, "compared": compared, "mismatches": mism}


# --------------------------------------------------------------- exports

def export_pdf(doc, path: Path, opts: dict, warnings: list[str]) -> dict:
    s = doc.PDFSettings
    wanted = {
        "PublishRange": 1,                                   # pdfCurrentPage
        "ColorMode": PDF_COLOR[opts["color_mode"]],
        "TextAsCurves": opts["text"] == "curves",
        "EmbedFonts": opts["text"] == "embed",
        "SubsetFonts": True,
        "DownsampleColor": True,
        "ColorResolution": opts["bitmap_dpi"],
        "GrayResolution": opts["bitmap_dpi"],
        # printer's marks - PDFSettings properties in the v27 typelib. The bleed LIMIT (`Bleed`) stays at CorelDRAW's
        # own default and is read back into the report below.
        "CropMarks": bool(opts.get("crop_marks", False)),
        "IncludeBleed": bool(opts.get("bleed", False)),
    }
    applied = {}
    for k, v in wanted.items():
        try:
            setattr(s, k, v)
            applied[k] = getattr(s, k)
        except Exception as e:
            warnings.append(f"PDF setting {k} could not be applied: {e}")
            continue
        if isinstance(v, bool) and bool(applied[k]) != v:
            warnings.append(f"PDF setting {k} was set to {v} but CorelDRAW reports {applied[k]!r}")
    if opts.get("bleed"):
        limit = _safe(lambda: s.Bleed)
        if limit is not None:
            applied["Bleed"] = limit
    doc.PublishToPDF(str(path))
    return applied


def _page_export_area(doc, pad_mm: float = 0.0):
    """A Rect covering exactly the active page, in the document's own units, or None if the document cannot
    provide one. Needed because `ExportBitmap(cdrCurrentPage, ExportArea=None)` was verified live (DARSHAN converted
    to 4:1) to render the whole DRAWING extent, not the page, whenever objects lie outside the page: the cover-fit
    background of an orientation conversion deliberately overflows it (5.3x the page height in that case), and the
    JPEG came out with every foreground shape squeezed to ~1/5 of its height around the centre while the .cdr and
    CorelDRAW's own verification were both correct. An explicit page-sized area renders the page as designed."""
    try:
        page = doc.ActivePage
        # `pad_mm` widens the area on every side (PNG background padding); the document unit is mm during an export
        return doc.Application.CreateRect(float(page.LeftX) - pad_mm, float(page.BottomY) - pad_mm,
                                          float(page.SizeWidth) + 2 * pad_mm, float(page.SizeHeight) + 2 * pad_mm)
    except Exception:
        return None


def export_raster(doc, path: Path, fmt: str, size: dict, raster: dict) -> None:
    transparent = fmt == "png" and raster["png_background"] == "transparent"
    image_type = IMAGE_TYPE["cmyk"] if fmt == "jpeg" and raster.get("color") == "cmyk" else scene_export.CDR_RGB_IMAGE
    flt = doc.ExportBitmap(
        str(path), FILTER[fmt], scene_export.CDR_CURRENT_PAGE, image_type,
        # explicit pixel size: CorelDRAW rounds a dpi to a whole number, which is up to 10% off at low resolutions
        size["w_px"], size["h_px"], size["dpi"], size["dpi"], 1 if raster["antialias"] else 0, False, transparent, True, False, 0, None,
        _page_export_area(doc, raster.get("padding_mm", 0.0)),
    )
    flt.Finish()


def apply_font_substitutions(page, subs: dict[str, str] | None, warnings: list[str]) -> dict:
    """Permanent substitutions saved for the shop ({missing font: installed font}, matched case-insensitively): every
    text object on the page (inside groups and PowerClips too) whose font is a missing one gets the substitute, read back
    like every other font write here - CorelDRAW ignores a font it does not have without saying so. Returns
    {original: {"to", "changed", "not_applied"}}. A text object mixing fonts inside one run reports no single font and is
    left alone (counted in "mixed")."""
    subs = {k.strip().lower(): (k, v) for k, v in (subs or {}).items() if k and v}
    if not subs:
        return {}
    out = {orig: {"to": to, "changed": 0, "not_applied": 0} for orig, to in subs.values()}
    mixed = 0
    shapes, _ = index_doc(page)
    for sh in shapes.values():
        if _safe(lambda sh=sh: int(sh.Type)) != 6:
            continue
        story = _safe(lambda sh=sh: sh.Text.Story)
        font = _safe(lambda: story.Font) if story is not None else None
        if not font:
            mixed += 1
            continue
        hit = subs.get(str(font).strip().lower())
        if hit is None:
            continue
        orig, to = hit
        try:
            story.Font = to
        except Exception:
            pass
        if str(_safe(lambda: story.Font) or "").lower() == to.lower():
            out[orig]["changed"] += 1
        else:
            out[orig]["not_applied"] += 1
    for orig, r in out.items():
        if r["not_applied"]:
            warnings.append(f"font substitution {orig!r} -> {r['to']!r} did not stick on {r['not_applied']} text object(s) - "
                            f"is {r['to']!r} installed on this server?")
    if mixed:
        out["_mixed_font_texts_skipped"] = mixed
    return out


def image_mode(path: Path) -> str | None:
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            return im.mode
    except Exception:
        return None


def text_to_curves(page) -> tuple[int, int]:
    """Convert every text object on the page (inside groups and PowerClips too) to curves. Returns (converted, left);
    `left` is how many objects are still text afterwards, read back rather than assumed."""
    shapes, _ = index_doc(page)
    done = 0
    for sh in shapes.values():
        if _safe(lambda sh=sh: int(sh.Type)) == 6:
            try:
                sh.ConvertToCurves()
                done += 1
            except Exception:
                pass
    shapes, _ = index_doc(page)
    left = sum(1 for sh in shapes.values() if _safe(lambda sh=sh: int(sh.Type)) == 6)
    return done, left


def image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def export_from_file(cdr_path: Path, scene_path: Path, ops: list[dict], formats: list[str], options: dict,
                     out_dir: Path, base_name: str, on_step: Callable[[str], None] | None = None,
                     assets_dir: Path | str | None = None, font_subs: dict[str, str] | None = None) -> dict:
    """Opens the converted .cdr in a fresh CorelDRAW, replays `ops`, verifies, exports, closes WITHOUT saving
    over the original. Returns the job report (files, timings, replay + verification results, warnings).
    `assets_dir` is where a `swap_image`/`update_product_slot` op's asset `path` resolves against - see
    Replayer._resolve_asset_path; omit it for an op list with no such op."""
    import json

    import pythoncom

    from . import corel_util

    step = on_step or (lambda s: None)
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    scene = json.loads(Path(scene_path).read_text(encoding="utf-8"))
    opts = normalize_options(formats, options)
    expected = scene_ops.apply_ops(scene, ops) if ops else scene            # also re-validates the list
    sizes = raster_sizes(expected["page"]["width"], expected["page"]["height"], opts, formats)

    warnings: list[str] = []
    timings: dict[str, float] = {}
    files: dict[str, str] = {}
    report: dict = {"formats": formats, "options": opts, "ops": len(ops), "warnings": warnings, "timings_s": timings}
    base = safe_name(base_name)

    pythoncom.CoInitialize()
    corel_util.cleanup_orphaned_instances()
    report["free_ram_gb"] = round(corel_util.check_memory(), 2)
    t0 = time.time()
    step("launch")
    app, launched, pid = corel_util.dispatch_corel()
    timings["launch"] = round(time.time() - t0, 2)
    doc = None

    def timed(name: str, fn, label: str | None = None):
        step(label or name)
        t = time.time()
        try:
            return corel_util.run_with_timeout(fn, pid, name)
        finally:
            timings[name] = round(time.time() - t, 2)

    try:
        doc = timed("open", lambda: app.OpenDocument(str(Path(cdr_path).resolve())))
        _safe(lambda: setattr(doc, "Unit", scene_export.CDR_MILLIMETER))
        if ops:
            last = {"t": 0.0}

            def progress(i, n):
                if i == 1 or i == n or time.time() - last["t"] >= 0.3:
                    step(f"replay {i}/{n}")
                    last["t"] = time.time()

            def replay():
                r = Replayer(doc, scene, ops, on_progress=progress, assets_dir=assets_dir)
                out = r.run()
                warnings.extend(r.warnings)
                return out

            t = time.time()
            step("replay")
            report["replay"] = corel_util.run_with_timeout(replay, pid, "replay", timeout=max(300.0, 2.0 * len(ops)))
            timings["replay"] = round(time.time() - t, 2)
            report["verification"] = timed("verify", lambda: verify(doc.ActivePage, expected))
            if not report["verification"]["ok"]:
                warnings.append("The exported document differs from the editor in "
                                f"{len(report['verification']['mismatches'])} place(s) - see verification.mismatches")
        if font_subs:
            # after the replay is verified against the shadow scene (which still has the original font names)
            report["font_substitutions"] = corel_util.run_with_timeout(
                lambda: apply_font_substitutions(doc.ActivePage, font_subs, warnings), pid, "fonts")
        for fmt in export_order(formats, opts):
            if fmt == "cdr":
                p = out_dir / f"{base}.cdr"
                if opts["cdr"]["text"] == "curves":
                    # the step heartbeat stays "cdr" (it is part of writing the CDR); the timeout is its own
                    step("cdr")
                    done, left = corel_util.run_with_timeout(lambda: text_to_curves(doc.ActivePage), pid, "curves")
                    report["cdr_text_to_curves"] = {"converted": done, "left": left}
                    if left:
                        warnings.append(f"{left} text object(s) could not be converted to curves and stay editable in the CDR")
                timed("cdr", lambda p=p: corel_util.save_cdr(doc, p, opts["cdr"]["version"]))
                files["cdr"] = p.name
                report["cdr_format"] = corel_util.check_cdr_format(p, warnings, opts["cdr"]["version"])
            elif fmt == "pdf":
                p = out_dir / f"{base}.pdf"
                report["pdf_settings"] = timed("pdf", lambda p=p: export_pdf(doc, p, opts["pdf"], warnings))
                files["pdf"] = p.name
            else:
                p = out_dir / f"{base}.{EXTENSION[fmt]}"
                timed(fmt, lambda p=p, fmt=fmt: export_raster(doc, p, fmt, sizes[fmt], opts[fmt]))
                files[fmt] = p.name
                px = image_size(p)
                report.setdefault("pixels", {})[fmt] = list(px) if px else None
                if fmt == "jpeg" and opts["jpeg"]["color"] == "cmyk":
                    mode = image_mode(p)
                    report["jpeg_mode"] = mode
                    if mode != "CMYK":
                        warnings.append(f"A CMYK JPEG was requested but CorelDRAW wrote a {mode or 'unreadable'} image")
        report["files"] = files
        report["file_bytes"] = {k: (out_dir / v).stat().st_size for k, v in files.items() if (out_dir / v).exists()}
        missing = [k for k, v in files.items() if not (out_dir / v).exists()]
        if missing:
            raise RuntimeError(f"CorelDRAW reported success but did not write: {', '.join(missing)}")
        return report
    finally:
        if doc is not None:
            _safe(lambda: setattr(doc, "Dirty", False))      # never a "save changes?" dialog, never overwrite the source
            _safe(lambda: doc.Close())
        corel_util.quit_corel(app, launched, pid)
        pythoncom.CoUninitialize()
