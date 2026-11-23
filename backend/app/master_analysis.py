"""Phase 1 of master spatial intelligence: a rich, defensive read of a master .cdr through CorelDRAW COM.

`extract_master(doc, app)` walks the open document and returns a plain-JSON "extraction" (see `EXTRACTOR_VERSION`): the page, every
object at every depth with its geometry in millimetres (origin bottom-left, as CorelDRAW reports it), appearance (fill, outline,
transparency, bitmap), text properties, hierarchy and z-order, plus a record of every property CorelDRAW would not give.

Rules this module follows:
- one inaccessible property never stops the walk: every read goes through `_Reader.get`, which records the failure per property
  (count + one sample message) and returns None. Nothing is invented: a property that could not be read is absent / None.
- object ids keep the engine convention (`engines._resize_and_tile`: the index of the shape in `page.Shapes`, as a string) for TOP-LEVEL
  shapes; nested shapes are "<parent id>.<n>" (n = 0-based position in the parent's `Shapes`, as `scene_export` walks them). Every object
  also carries `scene_id` = "s<StaticID>", the id the editor scene uses, so the two id spaces can be mapped.
- `page.Shapes` and the layer walk are both read and compared (`shape_sets`): CorelDRAW's `Page.Shapes` is not documented here as equal
  to the union of the layers' shapes, so the module reports the difference instead of assuming either.

Verified live (CorelDRAW 27.0.0.121, 10 X 3.cdr): page/layer walk, geometry, rotation, uniform fill CMYK + `Color.HexValue` RGB, `Outline.Width/Type/
Color`, `Transparency.Type` (and `.Uniform` only for type 1), PowerClip children. NOT verified live: `Fill.Fountain.*`, `Bitmap.ResolutionX/Y` (no
bitmap / fountain shape in the probed master) - read defensively, `errors` shows what failed on a given machine. See docs/master-spatial-intelligence.md.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from . import scene_export

EXTRACTOR_VERSION = 1
MAX_OBJECTS = 6000            # a runaway document stops here (`truncated: true`) instead of taking CorelDRAW for minutes
CDR_MILLIMETER = 3

# cdrFillType (CorelDRAW typelib). Only 1 (uniform) is confirmed by existing code (engines._shape_fill_cmyk); the other names follow the
# typelib's enum and are reported as "other_<n>" for anything not listed.
FILL_TYPES = {0: "none", 1: "uniform", 2: "fountain", 3: "postscript", 4: "pattern_two_color", 5: "pattern_full_color",
              6: "pattern_bitmap", 7: "texture"}


class _Reader:
    """Defensive property access that remembers what failed."""

    def __init__(self) -> None:
        self.errors: dict[str, dict] = {}

    def get(self, name: str, fn: Callable, default=None):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - COM raises many types; the message is what matters
            rec = self.errors.setdefault(name, {"count": 0, "sample": str(e)[:160]})
            rec["count"] += 1
            return default


def _hex_rgb(color, rd: _Reader, name: str) -> list | None:
    """RGB as CorelDRAW itself converts it: `Color.HexValue` ("#D41D6C") works for any colour model (verified live on CorelDRAW 27: a CMYK
    (11,100,25,1) gave #D41D6C = 212,29,108), where RGBRed/Green/Blue raise "Incompatible color model" on a CMYK colour."""
    h = rd.get(f"{name}.hex", lambda: str(color.HexValue))
    if h and len(h) == 7 and h[0] == "#":
        try:
            return [int(h[i:i + 2], 16) for i in (1, 3, 5)]
        except ValueError:
            return None
    return None


def _color(color, rd: _Reader, name: str) -> dict | None:
    """The colour's own CMYK (only when it IS a CMYK colour), an RGB conversion (CorelDRAW's), and whether it is a spot colour."""
    if color is None:
        return None
    out: dict = {"cmyk": None, "rgb": _hex_rgb(color, rd, name), "rgb_source": "Color.HexValue (CorelDRAW conversion)"}
    if rd.get(f"{name}.is_cmyk", lambda: bool(color.IsCMYK), False):
        vals = [rd.get(f"{name}.cmyk", lambda a=a: round(float(getattr(color, a)))) for a in ("CMYKCyan", "CMYKMagenta", "CMYKYellow", "CMYKBlack")]
        out["cmyk"] = vals if all(v is not None for v in vals) else None
    ctype = rd.get(f"{name}.type", lambda: int(color.Type))
    if ctype is not None:
        out["color_type"] = ctype
    if rd.get(f"{name}.is_spot", lambda: bool(color.IsSpot), False):
        out["spot"] = True
        spot = rd.get(f"{name}.spot_name", lambda: str(color.SpotColorName or ""))
        if spot:
            out["name"] = spot
    return out if (out["cmyk"] or out["rgb"]) else None


def read_fill(shape, rd: _Reader) -> dict | None:
    fill = rd.get("fill", lambda: shape.Fill)
    if fill is None:
        return None
    ftype = rd.get("fill.type", lambda: int(fill.Type))
    if ftype is None:
        return None
    out: dict = {"type": FILL_TYPES.get(ftype, f"other_{ftype}"), "type_code": ftype}
    if ftype == 1:
        out["color"] = _color(rd.get("fill.uniform", lambda: fill.UniformColor), rd, "fill.uniform")
    elif ftype == 2:
        f = rd.get("fill.fountain", lambda: fill.Fountain)
        if f is not None:
            out["fountain"] = {
                "start": _color(rd.get("fountain.start", lambda: f.StartColor), rd, "fountain.start"),
                "end": _color(rd.get("fountain.end", lambda: f.EndColor), rd, "fountain.end"),
                "angle": rd.get("fountain.angle", lambda: float(f.Angle)),
                "kind": rd.get("fountain.kind", lambda: int(f.Type)),
            }
    return out


def read_outline(shape, rd: _Reader) -> dict | None:
    o = rd.get("outline", lambda: shape.Outline)
    if o is None:
        return None
    out = {"type": rd.get("outline.type", lambda: int(o.Type)),
           "width_mm": rd.get("outline.width", lambda: round(float(o.Width), 4)),
           "color": _color(rd.get("outline.color", lambda: o.Color), rd, "outline.color")}
    return out if any(v is not None for v in out.values()) else None


def read_transparency(shape, rd: _Reader) -> dict | None:
    t = rd.get("transparency", lambda: shape.Transparency)
    if t is None:
        return None
    ttype = rd.get("transparency.type", lambda: int(t.Type))
    out = {"type": ttype}
    if ttype == 1:        # cdrUniformTransparency: only then does `Transparency.Uniform` exist (verified live: it raises for type 0)
        out["uniform"] = rd.get("transparency.uniform", lambda: round(float(t.Uniform), 2))
    return out if ttype is not None else None


def read_bitmap(shape, rd: _Reader) -> dict | None:
    b = rd.get("bitmap", lambda: shape.Bitmap)
    if b is None:
        return None
    out = {"px_w": rd.get("bitmap.px_w", lambda: int(b.SizeWidth)), "px_h": rd.get("bitmap.px_h", lambda: int(b.SizeHeight)),
           "dpi_x": rd.get("bitmap.dpi_x", lambda: round(float(b.ResolutionX), 1)),
           "dpi_y": rd.get("bitmap.dpi_y", lambda: round(float(b.ResolutionY), 1))}
    return out if any(v is not None for v in out.values()) else None


def _kind_of(type_: str, has_clip: bool) -> str:
    if has_clip:
        return "powerclip"
    return "group" if type_ == "group" else "shape"


def extract_master(doc, app=None, on_step: Callable[[str], None] | None = None, run: Callable | None = None) -> dict:
    """Walk an open document. `run(fn, name)` wraps COM calls with the hard per-step timeout (tests call straight through)."""
    run = run or (lambda fn, name: fn())
    step = on_step or (lambda _s: None)
    rd = _Reader()
    t0 = time.time()
    scene_export._safe(lambda: setattr(doc, "Unit", CDR_MILLIMETER))
    page = doc.ActivePage
    page_w, page_h = float(page.SizeWidth), float(page.SizeHeight)
    version = rd.get("corel.version", lambda: str(app.Version)) if app is not None else None

    objects: list[dict] = []
    truncated = False

    def add(shape, oid: str, parent: str | None, z: int, layer: str | None) -> dict | None:
        nonlocal truncated
        if len(objects) >= MAX_OBJECTS:
            truncated = True
            return None
        type_ = rd.get("type", lambda: scene_export._shape_type(shape), "unknown")
        clip = rd.get("powerclip", lambda: shape.PowerClip)
        node = {
            "id": oid,
            "scene_id": rd.get("static_id", lambda: f"s{int(shape.StaticID)}"),
            "parent": parent,
            "children": [],
            "z": z,
            "layer": layer,
            "name": rd.get("name", lambda: shape.Name or "", ""),
            "type": type_,
            "kind": _kind_of(type_, clip is not None),
            "x": rd.get("x", lambda: round(float(shape.LeftX), 4)),
            "y": rd.get("y", lambda: round(float(shape.BottomY), 4)),
            "w": rd.get("w", lambda: round(float(shape.SizeWidth), 4)),
            "h": rd.get("h", lambda: round(float(shape.SizeHeight), 4)),
            "rotation": rd.get("rotation", lambda: round(float(shape.RotationAngle), 4), 0.0),
            "visible": bool(rd.get("visible", lambda: shape.Visible, True)),
            "locked": bool(rd.get("locked", lambda: shape.Locked, False)),
        }
        if None in (node["x"], node["y"], node["w"], node["h"]):
            rec = rd.errors.setdefault("geometry", {"count": 0, "sample": "box unreadable - object left out"})
            rec["count"] += 1
            return None                               # an object with no box cannot be placed: left out, counted in errors
        if type_ not in ("group",):
            node["fill"] = read_fill(shape, rd)
            node["outline"] = read_outline(shape, rd)
            node["transparency"] = read_transparency(shape, rd)
        if type_ == "bitmap":
            node["bitmap"] = read_bitmap(shape, rd)
        if type_ == "text":
            info = rd.get("text", lambda: scene_export._text_info(shape))
            if info is not None:
                node["text"] = info
        objects.append(node)
        kids: list = []
        if clip is not None:
            kids = [("clip", c) for c in reversed(scene_export._children(clip))]
            node["powerclip"] = {"frame_rect": bool(rd.get("frame_rect", lambda: scene_export._frame_is_rect(shape, type_), False))}
        elif type_ == "group":
            kids = [("group", c) for c in reversed(scene_export._children(shape))]
        for n, (_how, c) in enumerate(kids):
            child = add(c, f"{oid}.{n}", oid, n, layer)
            if child is not None:
                node["children"].append(child["id"])
        return node

    step("page_shapes")
    # engine convention: top-level ids are the index in page.Shapes
    page_shapes = run(lambda: scene_export._children(page), "master_page_shapes")
    page_static: dict[str, str] = {}
    for i, s in enumerate(page_shapes):
        node = add(s, str(i), None, i, None)
        if node is not None:
            page_static[node["scene_id"] or f"?{i}"] = node["id"]

    step("layers")
    layer_info: list[dict] = []
    layer_static: set[str] = set()
    n_layers = rd.get("layers", lambda: int(page.Layers.Count), 0)
    for li in range(1, (n_layers or 0) + 1):
        layer = rd.get("layer", lambda li=li: page.Layers.Item(li))
        if layer is None:
            continue
        shapes = scene_export._children(layer) if rd.get("layer.shapes", lambda: layer.Shapes) is not None else []
        ids = [x for x in (rd.get("layer.static_id", lambda s=s: f"s{int(s.StaticID)}") for s in shapes) if x]
        layer_static.update(ids)
        layer_info.append({"name": rd.get("layer.name", lambda layer=layer: layer.Name or "", ""), "index": li,
                           "special": bool(rd.get("layer.special", lambda layer=layer: layer.IsSpecialLayer, False)),
                           "visible": bool(rd.get("layer.visible", lambda layer=layer: layer.Visible, True)),
                           "locked": not bool(rd.get("layer.editable", lambda layer=layer: layer.Editable, True)),
                           "shape_count": len(ids)})
    in_page, in_layers = set(page_static), layer_static
    shape_sets = {"page_shapes": len(page_shapes), "layer_shapes": len(layer_static),
                  "only_in_page_shapes": sorted(in_page - in_layers)[:20], "only_in_layers": sorted(in_layers - in_page)[:20],
                  "equal": in_page == in_layers}

    return {
        "extractor_version": EXTRACTOR_VERSION,
        "corel_version": version,
        "pages": rd.get("pages", lambda: int(doc.Pages.Count)),        # additive (Stage 1 audit): how many pages the file has; only the active page is read
        "page": {"width_mm": round(page_w, 4), "height_mm": round(page_h, 4), "unit": "mm", "origin": "bottom-left"},
        "layers": layer_info,
        "shape_sets": shape_sets,
        "objects": objects,
        "truncated": truncated,
        "errors": rd.errors,
        "seconds": round(time.time() - t0, 2),
    }


def render_page_png(doc, out_png: Path, max_px: int = 1600, run: Callable | None = None) -> dict | None:
    """The master page as one PNG for render-based background analysis (same ExportBitmap call as scene_export's page.png). None on failure."""
    run = run or (lambda fn, name: fn())
    page = doc.ActivePage
    w, h = float(page.SizeWidth), float(page.SizeHeight)
    dpi = max(10.0, max_px / (max(w, h) / 25.4))

    def _export():
        flt = doc.ExportBitmap(str(out_png), scene_export.CDR_PNG, scene_export.CDR_CURRENT_PAGE, scene_export.CDR_RGB_IMAGE,
                               0, 0, dpi, dpi, 1, False, False, True, False, 0, None, None)
        flt.Finish()

    try:
        run(_export, "master_page_png")
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "CorelTimeout":
            raise
        return {"error": str(e)[:200]}
    return {"file": out_png.name, "dpi": round(dpi, 1)}


def analyze_master_file(cdr_path: Path, png_out: Path | None = None, on_step: Callable[[str], None] | None = None,
                        render_px: int = 1600) -> dict:
    """Open `cdr_path` read-only in a CorelDRAW instance of the shared pool, extract, optionally render the page, close without saving."""
    from . import corel_util

    corel_util.ensure_com()
    app, _, pid = corel_util.acquire_instance()
    success = False
    doc = None
    try:
        from . import engines

        engines.close_master_session_on(pid)
        doc = corel_util.run_with_timeout(lambda: app.OpenDocument(str(Path(cdr_path).resolve())), pid, "OpenDocument")
        run = lambda fn, name: corel_util.run_with_timeout(fn, pid, name)  # noqa: E731
        result = extract_master(doc, app, on_step=on_step, run=run)
        if png_out is not None:
            (on_step or (lambda _s: None))("render")
            result["page_render"] = render_page_png(doc, Path(png_out), max_px=int(render_px), run=run)
        success = True
        return result
    finally:
        if doc is not None:
            try:
                doc.Dirty = False
            except Exception:
                pass
            try:
                doc.Close()
            except Exception:
                pass
        corel_util.release_instance(pid, success)
