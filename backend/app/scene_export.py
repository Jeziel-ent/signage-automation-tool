"""Export a converted .cdr as an editor scene (see scene_ops.py for the model).

Everything the editor draws is rendered by CorelDRAW itself: every leaf shape
(curve, text, bitmap, ...) is exported on its own, selection-only with a
transparent background, so the browser can move/resize objects independently.
Groups have no image of their own - they are structure, and the canvas draws
their children. A PowerClip is exported as ONE image of the container (its
clipped result), because per-child images would lose the clipping.

Vector leaves are exported as SVG (crisp at any zoom); text and bitmaps as PNG.
Corel's SVG text uses <font> elements which browsers do not render, so any
text leaf - and any SVG that turns out to contain one - falls back to PNG.

The pure planning/walking code takes duck-typed shapes so tests can drive it
with fakes; only `export_scene` touches a real CorelDRAW (through the same
corel_util plumbing as everything else, and only ever inside the worker
subprocess - see corel_worker.py's "scene_export" job).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable

SCENE_VERSION = 4
CDR_PNG = 802
CDR_SVG = 1345
CDR_CURRENT_PAGE = 1
CDR_SELECTION = 2
CDR_RGB_IMAGE = 4
CDR_MILLIMETER = 3

SHAPE_TYPES = {
    0: "none", 1: "rectangle", 2: "ellipse", 3: "curve", 4: "polygon",
    5: "bitmap", 6: "text", 7: "group", 8: "selection", 9: "guideline",
    10: "blend_group", 11: "extrude_group", 12: "ole_object", 13: "contour_group",
    14: "linear_dimension", 15: "bevel_group", 16: "drop_shadow_group",
    17: "3d_object", 18: "artistic_media_group", 19: "connector",
    20: "mesh_fill", 21: "custom", 22: "custom_effect_group", 23: "symbol",
    24: "html_form_object", 25: "html_active_object", 26: "perfect_shape", 27: "eps",
}
VECTOR_TYPES = {"rectangle", "ellipse", "curve", "polygon", "perfect_shape"}

IMG_PX_PER_MM = 4.0       # ~100 dpi: crisp enough to zoom in on, small enough to keep 300 shapes cheap
IMG_MIN_LONG_PX = 96
IMG_MAX_LONG_PX = 2048    # 2048x2048 RGBA is 16 MB decoded - the worst case for one leaf
PAGE_IMAGE_MAX_PX = 2400


def plan_png_size(w_mm: float, h_mm: float) -> tuple[int, int]:
    """Pixel size for a leaf's PNG: ~IMG_PX_PER_MM, longest side clamped to [MIN, MAX]."""
    longest = max(w_mm, h_mm, 0.001)
    long_px = max(IMG_MIN_LONG_PX, min(IMG_MAX_LONG_PX, longest * IMG_PX_PER_MM))
    k = long_px / longest
    return max(1, round(w_mm * k)), max(1, round(h_mm * k))


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _shape_type(shape) -> str:
    return SHAPE_TYPES.get(int(shape.Type), f"unknown_{shape.Type}")


def _children(container) -> list:
    shapes = _safe(lambda: container.Shapes)
    if shapes is None:
        return []
    return [shapes.Item(i) for i in range(1, int(shapes.Count) + 1)]


# cdrAlignment, from the CorelDRAW typelib (note: Right is 2, Center 3): 0 none (= left), 1 left, 2 right, 3 center,
# 4 full justify, 5 force justify, 6 mixed (runs differ - reported as unknown)
ALIGN_FROM_COREL = {0: "left", 1: "left", 2: "right", 3: "center", 4: "justify", 5: "justify"}
ALIGN_TO_COREL = {"left": 1, "right": 2, "center": 3, "justify": 4}
LINE_SPACING_PERCENT_OF_CHAR_HEIGHT = 0      # cdrLineSpacingType.cdrPercentOfCharacterHeightLineSpacing


def text_format(story) -> dict:
    """The paragraph formatting the editor's Text tab shows: align, bold, italic, underline, line_spacing (% of character
    height), char_spacing (% of a space). A value CorelDRAW cannot give as one number (mixed runs, another line-spacing
    unit) is left out, so the panel shows it as unknown instead of a wrong default."""
    out = {}
    align = ALIGN_FROM_COREL.get(_safe(lambda: int(story.Alignment)))
    if align:
        out["align"] = align
    for key, prop in (("bold", "Bold"), ("italic", "Italic")):
        v = _safe(lambda prop=prop: getattr(story, prop))
        if isinstance(v, bool):
            out[key] = v
    ul = _safe(lambda: int(story.Underline))           # cdrFontLine: 0 none, 1+ a line style
    if ul is not None and ul >= 0:
        out["underline"] = ul != 0
    if _safe(lambda: int(story.LineSpacingType)) == LINE_SPACING_PERCENT_OF_CHAR_HEIGHT:
        v = _safe(lambda: float(story.LineSpacing))
        if v is not None:
            out["line_spacing"] = round(v, 2)
    v = _safe(lambda: float(story.CharSpacing))
    if v is not None:
        out["char_spacing"] = round(v, 2)
    return out


def _text_info(shape) -> dict:
    story = shape.Text.Story
    return {
        # cdrTextType: 0 artistic, 1 paragraph, 2 artistic fitted, 3 paragraph fitted
        "kind": {0: "artistic", 1: "paragraph", 2: "artistic", 3: "paragraph"}.get(_safe(lambda: int(shape.Text.Type)), "artistic"),
        "content": _safe(lambda: story.Text, ""),
        "font": _safe(lambda: story.Font),       # None when a run mixes fonts
        "size_pt": _safe(lambda: float(story.Size)),
        **text_format(story),
    }


def _node(shape, kind: str, type_: str) -> dict:
    n = {
        "id": f"s{int(shape.StaticID)}",
        "kind": kind,
        "type": type_,
        "name": _safe(lambda: shape.Name, "") or "",
        "x": round(float(shape.LeftX), 4),
        "y": round(float(shape.BottomY), 4),
        "w": round(float(shape.SizeWidth), 4),
        "h": round(float(shape.SizeHeight), 4),
        "rotation": round(_safe(lambda: float(shape.RotationAngle), 0.0), 4),
        "visible": bool(_safe(lambda: shape.Visible, True)),
        "locked": bool(_safe(lambda: shape.Locked, False)),
        "image": None,
    }
    return n


def _frame_is_rect(shape, type_: str) -> bool:
    """True when the PowerClip frame is an axis-aligned rectangle, i.e. clipping to its bounding box
    is exact - the editor draws the contents live only then. A rectangle drawn as a curve counts:
    every node sits on a corner of the bounding box."""
    if abs(_safe(lambda: float(shape.RotationAngle), 0.0)) > 0.01:
        return False
    if type_ == "rectangle":
        return True
    if type_ != "curve":
        return False

    def check() -> bool:
        nodes = shape.Curve.Nodes
        n = int(nodes.Count)
        if n < 4 or n > 5:
            return False
        x0, y0 = float(shape.LeftX), float(shape.BottomY)
        x1, y1 = x0 + float(shape.SizeWidth), y0 + float(shape.SizeHeight)
        tol = 0.05
        corners = set()
        for i in range(1, n + 1):
            nd = nodes.Item(i)
            x, y = float(nd.PositionX), float(nd.PositionY)
            cx = 0 if abs(x - x0) < tol else 1 if abs(x - x1) < tol else None
            cy = 0 if abs(y - y0) < tol else 1 if abs(y - y1) < tol else None
            if cx is None or cy is None:
                return False
            corners.add((cx, cy))
        return len(corners) == 4

    return bool(_safe(check, False))


def walk_shape(shape, leaves: list, in_clip: bool = False) -> dict:
    """Builds the node for `shape` (recursively). Appends (node, shape) to `leaves`
    for every shape that needs its own rendered image."""
    type_ = _shape_type(shape)
    powerclip = _safe(lambda: shape.PowerClip)
    if powerclip is not None:
        node = _node(shape, "powerclip", type_)
        node["frame_rect"] = _frame_is_rect(shape, type_)
        # Each child is a leaf with its own (unclipped) image so the editor can draw them live
        # inside a client-side clip and move/resize them without a CorelDRAW round trip. The
        # container keeps its single flat image of the clipped result as the fallback (non-rect
        # frames, rotated frames, or a scene whose children failed to render). Child order in
        # `leaves` is bottom -> top, container last - the id-assignment order (see
        # export_replay.index_doc) is unchanged because ids are read in walk order, not export order.
        node["children"] = [walk_shape(c, leaves, True) for c in reversed(_children(powerclip))]
        if in_clip:
            node["_in_clip"] = True
        leaves.append((node, shape))          # ... and one image for the whole clipped result
        return node
    if type_ == "group":
        node = _node(shape, "group", type_)
        node["children"] = [walk_shape(c, leaves, in_clip) for c in reversed(_children(shape))]
        return node
    node = _node(shape, "shape", type_)
    if type_ == "text":
        info = _safe(lambda: _text_info(shape))
        if info is not None:
            node["text"] = info
    if in_clip:
        node["_in_clip"] = True                # popped by _export_leaf - export-only bookkeeping
    leaves.append((node, shape))
    return node


def walk_page(page) -> tuple[list[dict], list]:
    """Layer tree (bottom -> top, like scene_ops expects) + the leaves needing images.

    CorelDRAW's Shapes.Item(1) is the TOPMOST shape (verified: OrderToFront moves a
    shape to index 1), so each child list is reversed. Special layers (Guides,
    Desktop, Grid) are not part of the drawing and are skipped.
    """
    layers: list[dict] = []
    leaves: list = []
    for i in range(1, int(page.Layers.Count) + 1):
        layer = page.Layers.Item(i)
        if _safe(lambda: layer.IsSpecialLayer, False):
            continue
        top = _children(layer)
        kids = [walk_shape(s, leaves) for s in top]
        kids.reverse()
        layers.append({
            "id": f"L{i}",
            "name": _safe(lambda: layer.Name, "") or f"Layer {i}",
            "visible": bool(_safe(lambda: layer.Visible, True)),
            "locked": not bool(_safe(lambda: layer.Editable, True)),
            "children": kids,
        })
    # page.Layers.Item(1) is the topmost layer in the Object Manager, so reverse to bottom -> top
    layers.reverse()
    return layers, leaves


def _select_only(doc, shape) -> None:
    doc.ClearSelection()
    shape.AddToSelection()


_SVG_DRAWING_TAGS = (b"<path", b"<rect", b"<polygon", b"<ellipse", b"<circle", b"<image", b"<line", b"<polyline", b"<use")
_EMPTY_VIEWBOX = re.compile(rb'viewBox="[^"]*nan')


def _svg_has_drawing(p: Path) -> bool:
    """False for an SVG that CorelDRAW wrote empty (`viewBox="0 0 nan nan"`, no elements) or that
    uses <font> text (browsers cannot render it). A selection export of a vector shape that sits
    inside a PowerClip produces exactly such an empty file - accepting it drew the shape as nothing
    (a whole maroon banner vanished from the canvas) - so it must be treated as a failure."""
    try:
        data = p.read_bytes()
    except OSError:
        return False
    if b"FontID" in data[:4000] or _EMPTY_VIEWBOX.search(data[:4000]):
        return False
    return any(t in data for t in _SVG_DRAWING_TAGS)


def _export_png(doc, path: Path, px_w: int, px_h: int) -> None:
    flt = doc.ExportBitmap(
        str(path), CDR_PNG, CDR_SELECTION, CDR_RGB_IMAGE,
        px_w, px_h, 96, 96, 1, False, True, True, False, 0, None, None,
    )
    flt.Finish()


def _duplicate_out_of_clip(doc, shape):
    """A copy of `shape` on the layer, outside any PowerClip. Exporting a shape that is INSIDE a
    PowerClip by selection is unreliable (verified live: a bitmap raises E_FAIL, a vector shape
    writes an empty SVG), while a plain top-level copy exports like any other shape. The caller
    deletes the copy straight after, so the document is unchanged."""
    dup = shape.Duplicate()
    try:
        layer = _safe(lambda: shape.Layer) or _safe(lambda: doc.ActivePage.ActiveLayer)
        dup.MoveToLayer(layer)
        _safe(lambda: setattr(dup, "Visible", True))
    except Exception:
        _safe(lambda: dup.Delete())
        raise
    return dup


def _render_leaf(doc, node: dict, target, img_dir: Path) -> None:
    _select_only(doc, target)
    if node["type"] in VECTOR_TYPES and node["kind"] == "shape":
        p = img_dir / f"{node['id']}.svg"
        try:
            doc.Export(str(p), CDR_SVG, CDR_SELECTION, None, None)
            if _svg_has_drawing(p):
                node["image"] = {"file": p.name, "format": "svg"}
                return
        except Exception:
            pass
        p.unlink(missing_ok=True)
    px_w, px_h = plan_png_size(node["w"], node["h"])
    p = img_dir / f"{node['id']}.png"
    _export_png(doc, p, px_w, px_h)
    node["image"] = {"file": p.name, "format": "png"}


def _export_leaf(doc, node: dict, shape, img_dir: Path) -> None:
    """Renders one leaf to img_dir and sets node["image"]. Temporarily makes a hidden
    shape visible (a hidden shape exports as nothing) and restores it afterwards.
    A leaf inside a PowerClip is rendered from a duplicate moved out of the clip (see
    _duplicate_out_of_clip); any other leaf that fails a direct export gets one retry that way."""
    in_clip = bool(node.pop("_in_clip", False))
    was_visible = _safe(lambda: shape.Visible, True)
    if not was_visible:
        _safe(lambda: setattr(shape, "Visible", True))
    dup = None
    try:
        if in_clip:
            dup = _duplicate_out_of_clip(doc, shape)
            _render_leaf(doc, node, dup, img_dir)
        else:
            try:
                _render_leaf(doc, node, shape, img_dir)
            except Exception:
                if node["type"] != "bitmap":
                    raise
                dup = _duplicate_out_of_clip(doc, shape)
                _render_leaf(doc, node, dup, img_dir)
    finally:
        if dup is not None:
            _safe(lambda: dup.Delete())
        if not was_visible:
            _safe(lambda: setattr(shape, "Visible", False))
        _safe(lambda: doc.ClearSelection())
    if node.get("kind") == "powerclip":
        _export_frame(doc, node, shape, img_dir)


def _export_frame(doc, node: dict, shape, img_dir: Path) -> None:
    """The PowerClip FRAME on its own - its fill and outline without the contents - as node["frame_image"]. The editor draws
    a live PowerClip as its contents inside a clip path; without this the frame's own fill was missing there (the Hangyo
    board's pink side panels showed as white, and the white "ICE CREAM" / "ADINN/06/26" text vanished on white). Rendered
    from a duplicate whose contents are deleted; the duplicate is deleted afterwards. A failure only leaves the frame
    image out - the editor then draws as before."""
    dup = None
    try:
        dup = _duplicate_out_of_clip(doc, shape)
        clip = _safe(lambda: dup.PowerClip)
        for c in (_children(clip) if clip is not None else []):
            _safe(lambda c=c: c.Delete())
        frame = {"id": f"{node['id']}_frame", "kind": "shape", "type": node.get("type"), "w": node["w"], "h": node["h"]}
        _render_leaf(doc, frame, dup, img_dir)
        if frame.get("image"):
            node["frame_image"] = frame["image"]
    except Exception:
        pass
    finally:
        if dup is not None:
            _safe(lambda: dup.Delete())
        _safe(lambda: doc.ClearSelection())


# COM errors that mean CorelDRAW itself is gone (crashed, killed, or disconnected) - not that one shape could not be
# exported. Every later call fails the same way, so the build must stop instead of caching a scene whose remaining objects
# have no images (they would be listed in Layers but draw nothing on the canvas).
SERVER_GONE_HRESULTS = (
    -2147023174,  # RPC_S_SERVER_UNAVAILABLE: "The RPC server is unavailable."
    -2147023170,  # RPC_S_CALL_FAILED: "The remote procedure call failed."
    -2147417848,  # RPC_E_DISCONNECTED: "The object invoked has disconnected from its clients."
    -2147418111,  # RPC_E_CALL_REJECTED issued by a dying server
)


class CorelGone(RuntimeError):
    """CorelDRAW stopped during a scene export; nothing was written."""


def server_gone(err) -> bool:
    """True when `err` (an exception, or a recorded failure string) is one of SERVER_GONE_HRESULTS."""
    if isinstance(err, BaseException):
        code = err.args[0] if err.args else None
        if isinstance(code, int) and code in SERVER_GONE_HRESULTS:
            return True
        err = str(err)
    return any(str(code) in str(err) for code in SERVER_GONE_HRESULTS)


def export_scene(doc, out_dir: Path, on_step: Callable[[str], None] | None = None,
                 run: Callable | None = None) -> dict:
    """Walks an open document, renders every leaf image + a full-page reference
    render into `out_dir`, writes and returns scene.json.

    `run(fn, op_name)` wraps each COM call with the hard per-step timeout (the
    worker passes corel_util.run_with_timeout bound to its instance); tests pass
    nothing and call straight through.
    """
    run = run or (lambda fn, name: fn())
    step = on_step or (lambda _s: None)
    out_dir = Path(out_dir).resolve()
    img_dir = out_dir / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    _safe(lambda: setattr(doc, "Unit", CDR_MILLIMETER))
    page = doc.ActivePage
    page_w, page_h = float(page.SizeWidth), float(page.SizeHeight)

    step("walk")
    layers, leaves = run(lambda: walk_page(page), "scene_walk")

    total = len(leaves)
    failed: list[str] = []
    last_step_at = 0.0
    for n, (node, shape) in enumerate(leaves, start=1):
        # One heartbeat per image would be ~15 file writes a second; a few a second is plenty.
        if n == 1 or n == total or time.time() - last_step_at >= 0.4:
            step(f"images {n}/{total}")
            last_step_at = time.time()
        try:
            run(lambda node=node, shape=shape: _export_leaf(doc, node, shape, img_dir), "scene_image")
        except Exception as e:  # one bad shape must not sink the whole scene
            if type(e).__name__ == "CorelTimeout":
                raise
            if server_gone(e):   # ...but CorelDRAW disappearing does: stop, write nothing, let the editor retry
                raise CorelGone(f"CorelDRAW stopped while exporting the board (at object {n} of {total}: {e}). "
                                "Nothing was saved - open the editor again to rebuild it.") from e
            failed.append(f"{node['id']}: {e}")

    step("page_image")
    page_png = out_dir / "page.png"
    longest_in = max(page_w, page_h) / 25.4
    dpi = max(20.0, PAGE_IMAGE_MAX_PX / longest_in)

    def _page_export():
        flt = doc.ExportBitmap(
            str(page_png), CDR_PNG, CDR_CURRENT_PAGE, CDR_RGB_IMAGE,
            0, 0, dpi, dpi, 1, False, False, True, False, 0, None, None,
        )
        flt.Finish()

    page_image = None
    try:
        run(_page_export, "scene_page_image")
        page_image = {"file": page_png.name, "format": "png"}
    except Exception as e:
        if type(e).__name__ == "CorelTimeout":
            raise
        if server_gone(e):
            raise CorelGone(f"CorelDRAW stopped while rendering the page preview ({e}). Nothing was saved - "
                            "open the editor again to rebuild it.") from e
        failed.append(f"page image: {e}")

    scene = {
        "version": SCENE_VERSION,          # 2: PowerClip children have own images; 3: ...and are exported from duplicates (v2 wrote empty SVGs for them); 4: + the frame's own fill (frame_image)
        "unit": "mm",
        "page": {"width": round(page_w, 4), "height": round(page_h, 4)},
        "page_image": page_image,
        "layers": layers,
        "stats": {
            "leaf_images": total - len([f for f in failed if not f.startswith("page image")]),
            "leaves": total,
            "image_failures": failed,
            "seconds": round(time.time() - t0, 1),
        },
    }
    tmp = out_dir / "scene.json.tmp"
    tmp.write_text(json.dumps(scene, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_dir / "scene.json")
    return scene


def export_scene_from_file(cdr_path: Path, out_dir: Path, on_step: Callable[[str], None] | None = None) -> dict:
    """Opens `cdr_path` read-only in a fresh CorelDRAW, exports the scene, closes without saving."""
    import pythoncom

    from . import corel_util

    pythoncom.CoInitialize()
    corel_util.cleanup_orphaned_instances()
    app, we_launched_it, pid = corel_util.dispatch_corel()
    doc = None
    try:
        doc = corel_util.run_with_timeout(
            lambda: app.OpenDocument(str(Path(cdr_path).resolve())), pid, "OpenDocument",
        )
        return export_scene(
            doc, out_dir, on_step=on_step,
            run=lambda fn, name: corel_util.run_with_timeout(fn, pid, name),
        )
    finally:
        if doc is not None:
            try:
                doc.Close()
            except Exception:
                pass
        corel_util.quit_corel(app, we_launched_it, pid)
        pythoncom.CoUninitialize()


# ------------------------------------------------------------- mock engine

def mock_scene(out_dir: Path, page_w: float, page_h: float) -> dict:
    """A small hand-built scene with SVG images, for MockEngine (no CorelDRAW):
    lets the whole editor UI/API be exercised on any OS and in CI. Not a render
    of the shop's real design."""
    out_dir = Path(out_dir)
    img_dir = out_dir / "img"
    img_dir.mkdir(parents=True, exist_ok=True)

    def svg(name: str, w: float, h: float, body: str) -> dict:
        (img_dir / f"{name}.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" preserveAspectRatio="none">{body}</svg>',
            encoding="utf-8",
        )
        return {"file": f"{name}.svg", "format": "svg"}

    def leaf(id_, name, type_, x, y, w, h, body, text=None):
        n = {"id": id_, "kind": "shape", "type": type_, "name": name, "x": x, "y": y, "w": w, "h": h,
             "rotation": 0.0, "visible": True, "locked": False, "image": svg(id_, w, h, body)}
        if text:
            n["text"] = text
        return n

    pw, ph = float(page_w), float(page_h)
    bg = leaf("s1", "background", "rectangle", 0, 0, pw, ph, f'<rect width="{pw}" height="{ph}" fill="#1c3f94"/>')
    card = leaf("s2", "card", "rectangle", pw * 0.05, ph * 0.3, pw * 0.4, ph * 0.5,
                f'<rect width="{pw*0.4}" height="{ph*0.5}" rx="{ph*0.03}" fill="#ffffff"/>')
    logo_a = leaf("s3", "logo circle", "ellipse", pw * 0.08, ph * 0.4, ph * 0.3, ph * 0.3,
                  f'<circle cx="{ph*0.15}" cy="{ph*0.15}" r="{ph*0.15}" fill="#e0182f"/>')
    logo_b = leaf("s4", "logo bar", "rectangle", pw * 0.08 + ph * 0.35, ph * 0.45, pw * 0.25, ph * 0.1,
                  f'<rect width="{pw*0.25}" height="{ph*0.1}" fill="#111111"/>')
    grp = {"id": "s5", "kind": "group", "type": "group", "name": "logo group",
           "x": logo_a["x"], "y": logo_a["y"],
           "w": logo_b["x"] + logo_b["w"] - logo_a["x"], "h": logo_a["h"],
           "rotation": 0.0, "visible": True, "locked": False, "image": None, "children": [logo_a, logo_b]}
    shop = leaf("s6", "shop name", "text", pw * 0.5, ph * 0.1, pw * 0.4, ph * 0.18,
                f'<text x="0" y="{ph*0.13}" font-size="{ph*0.12}" font-family="Arial" fill="#ffffff">SHOP NAME</text>',
                text={"content": "SHOP NAME", "font": "Arial", "size_pt": 120.0})
    scene = {
        "version": 1, "unit": "mm", "page": {"width": pw, "height": ph}, "page_image": None,
        "layers": [
            {"id": "L1", "name": "Layer 1", "visible": True, "locked": False, "children": [bg, card]},
            {"id": "L2", "name": "Layer 2", "visible": True, "locked": False, "children": [grp, shop]},
        ],
        "stats": {"leaves": 5, "leaf_images": 5, "image_failures": [], "seconds": 0.0, "mock": True},
    }
    (out_dir / "scene.json").write_text(json.dumps(scene), encoding="utf-8")
    return scene
