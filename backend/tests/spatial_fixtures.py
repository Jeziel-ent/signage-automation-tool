"""Synthetic masters for the spatial-intelligence tests (extraction dicts in master_analysis' format)."""
from __future__ import annotations

from app import master_model as mm


def obj(i, name, type_, x, y, w, h, *, text=None, fill_cmyk=None, parent=None, children=None, z=None, visible=True, kind=None):
    d = {"id": str(i), "scene_id": f"s{100 + int(str(i).split('.')[0])}", "parent": parent, "children": children or [], "z": i if z is None else z,
         "name": name, "type": type_, "kind": kind or ("group" if type_ == "group" else "shape"), "x": x, "y": y, "w": w, "h": h,
         "rotation": 0.0, "visible": visible, "locked": False}
    if text is not None:
        d["text"] = {"kind": "artistic", "content": text, "font": "Arial", "size_pt": 120.0}
    if fill_cmyk is not None:
        d["fill"] = {"type": "uniform", "type_code": 1, "color": {"cmyk": list(fill_cmyk)}}
    return d


def extraction(page_w, page_h, objects, **extra):
    d = {"extractor_version": 1, "corel_version": "test", "page": {"width_mm": page_w, "height_mm": page_h, "unit": "mm", "origin": "bottom-left"},
         "layers": [], "shape_sets": {"equal": True}, "objects": objects, "truncated": False, "errors": {}}
    d.update(extra)
    return d


def sign_10x8():
    """A 10 x 8 'ft' sign in mm: blue background, a logo group top-left, a big shop-name line over a white card, a product bitmap, a phone line
    and a small corner badge touching the top-right edge."""
    W, H = 3048.0, 2438.4
    objs = [
        obj(0, "bg_wall", "rectangle", 0, 0, W, H, fill_cmyk=(95, 80, 4, 0)),
        obj(1, "logo_main", "group", 150, 1700, 700, 500),
        obj(2, "card", "rectangle", 900, 1100, 1800, 500, fill_cmyk=(0, 0, 0, 0)),
        obj(3, "Shop name", "text", 1000, 1200, 1600, 300, text="OLD SHOP"),
        obj(4, "photo", "bitmap", 1100, 300, 900, 700),
        obj(5, "fixed_phone", "text", 2300, 80, 600, 80, text="Phone No. 111"),
        obj(6, "badge", "group", 2900, 2290, 120, 140),
    ]
    return extraction(W, H, objs)


def build(extr=None, **kw) -> mm.MasterModel:
    return mm.build_model(extr or sign_10x8(), master_hash="h" * 8, **kw)
