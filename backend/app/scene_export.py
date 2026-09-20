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
import time
from pathlib import Path
from typing import Callable

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


def _text_info(shape) -> dict:
    story = shape.Text.Story
    return {
        "content": _safe(lambda: story.Text, ""),
        "font": _safe(lambda: story.Font),       # None when a run mixes fonts
        "size_pt": _safe(lambda: float(story.Size)),
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


def walk_shape(shape, leaves: list) -> dict:
    """Builds the node for `shape` (recursively). Appends (node, shape) to `leaves`
    for every shape that needs its own rendered image."""
    type_ = _shape_type(shape)
    powerclip = _safe(lambda: shape.PowerClip)
    if powerclip is not None:
        node = _node(shape, "powerclip", type_)
        node["children"] = [walk_shape(c, []) for c in reversed(_children(powerclip))]
        leaves.append((node, shape))          # one image for the whole clipped result
        return node
    if type_ == "group":
        node = _node(shape, "group", type_)
        node["children"] = [walk_shape(c, leaves) for c in reversed(_children(shape))]
        return node
    node = _node(shape, "shape", type_)
    if type_ == "text":
        info = _safe(lambda: _text_info(shape))
        if info is not None:
            node["text"] = info
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


def _export_leaf(doc, node: dict, shape, img_dir: Path) -> None:
    """Renders one leaf to img_dir and sets node["image"]. Temporarily makes a hidden
    shape visible (a hidden shape exports as nothing) and restores it afterwards."""
    was_visible = _safe(lambda: shape.Visible, True)
    if not was_visible:
        _safe(lambda: setattr(shape, "Visible", True))
    try:
        _select_only(doc, shape)
        vector = node["type"] in VECTOR_TYPES and node["kind"] == "shape"
        if vector:
            p = img_dir / f"{node['id']}.svg"
            try:
                doc.Export(str(p), CDR_SVG, CDR_SELECTION, None, None)
                if p.exists() and b"FontID" not in p.read_bytes()[:4000]:
                    node["image"] = {"file": p.name, "format": "svg"}
                    return
            except Exception:
                pass
            p.unlink(missing_ok=True)
        px_w, px_h = plan_png_size(node["w"], node["h"])
        p = img_dir / f"{node['id']}.png"
        flt = doc.ExportBitmap(
            str(p), CDR_PNG, CDR_SELECTION, CDR_RGB_IMAGE,
            px_w, px_h, 96, 96, 1, False, True, True, False, 0, None, None,
        )
        flt.Finish()
        node["image"] = {"file": p.name, "format": "png"}
    finally:
        if not was_visible:
            _safe(lambda: setattr(shape, "Visible", False))
        _safe(lambda: doc.ClearSelection())


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
        failed.append(f"page image: {e}")

    scene = {
        "version": 1,
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
        "layers": [{"id": "L2", "name": "Layer 1", "visible": True, "locked": False,
                    "children": [bg, card, grp, shop]}],
        "stats": {"leaves": 5, "leaf_images": 5, "image_failures": [], "seconds": 0.0, "mock": True},
    }
    (out_dir / "scene.json").write_text(json.dumps(scene), encoding="utf-8")
    return scene
