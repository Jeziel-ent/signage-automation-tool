"""Scene export (scene_export.py) against fake COM objects - no CorelDRAW.

The real thing was verified live against generated dalmia boards (see
CLAUDE.md "Phase C"); these tests pin the logic that is easy to regress:
z-order direction, special-layer skipping, group/PowerClip structure, the
SVG-vs-PNG choice and its fallbacks, hidden-shape handling, and that one bad
shape cannot sink a whole scene.
"""
from __future__ import annotations

import json

import pytest

from app import scene_export
from app.scene_ops import apply_ops

_next_id = iter(range(1, 10_000))


class Coll:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i - 1]


class Story:
    def __init__(self, text="", font="Arial", size=12.0):
        self.Text, self.Font, self.Size = text, font, size


class Shape:
    def __init__(self, type_, x=0, y=0, w=10, h=10, name="", children=None, powerclip=None, story=None,
                 visible=True, locked=False):
        self.Type = type_
        self.StaticID = next(_next_id)
        self.LeftX, self.BottomY, self.SizeWidth, self.SizeHeight = x, y, w, h
        self.Name, self.RotationAngle, self.Visible, self.Locked = name, 0.0, visible, locked
        self.Shapes = Coll(children or [])
        self.PowerClip = type("PC", (), {"Shapes": Coll(powerclip)})() if powerclip is not None else None
        self.Text = type("T", (), {"Story": story})() if story is not None else None


CURVE, RECT, TEXT, GROUP = 3, 1, 6, 7


class Layer:
    def __init__(self, name, shapes, special=False, visible=True, editable=True):
        self.Name, self.IsSpecialLayer, self.Visible, self.Editable = name, special, visible, editable
        self.Shapes = Coll(shapes)


class Page:
    def __init__(self, layers, w=1000.0, h=500.0):
        self.Layers = Coll(layers)
        self.SizeWidth, self.SizeHeight = w, h


class FakeFilter:
    def __init__(self, path):
        self.path = path

    def Finish(self):
        self.path.write_bytes(b"\x89PNG fake")


class FakeDoc:
    def __init__(self, page, svg_body=b"<svg/>", fail_on=()):
        self.ActivePage, self.Unit = page, 0
        self.calls, self.svg_body, self.fail_on = [], svg_body, set(fail_on)
        self.selection = []
        self.visible_at_export = {}

    def ClearSelection(self):
        self.selection = []

    def Export(self, path, filt, rng, opts, pal):
        from pathlib import Path
        sel = self._selected_shape()
        self._record("svg", Path(path), sel)
        Path(path).write_bytes(self.svg_body)

    def ExportBitmap(self, path, filt, rng, img_type, w, h, rx, ry, aa, dith, transp, prof, layers, comp, pal, area):
        from pathlib import Path
        sel = self._selected_shape()
        self._record("png", Path(path), sel, size=(w, h), transparent=transp, range_=rng)
        return FakeFilter(Path(path))

    def _selected_shape(self):
        return self.selection[0] if self.selection else None

    def _record(self, fmt, path, sel, **kw):
        if sel is not None and sel.StaticID in self.fail_on:
            raise RuntimeError("boom")
        if sel is not None:
            self.visible_at_export[sel.StaticID] = sel.Visible
        self.calls.append({"fmt": fmt, "name": path.name, **kw})


def _select_hook(shape, doc):
    shape.AddToSelection = lambda: doc.selection.append(shape)


def _attach(doc):
    def walk(shapes):
        for i in range(1, shapes.Count + 1):
            s = shapes.Item(i)
            _select_hook(s, doc)
            walk(s.Shapes)
            if s.PowerClip is not None:
                walk(s.PowerClip.Shapes)

    for i in range(1, doc.ActivePage.Layers.Count + 1):
        walk(doc.ActivePage.Layers.Item(i).Shapes)
    return doc


def _sample_doc(**kw):
    # Corel order: Item(1) is the TOPMOST shape
    top_text = Shape(TEXT, 10, 10, 200, 40, story=Story("SHOP NAME", "Nirmala UI", 120.0))
    child_a, child_b = Shape(CURVE, 100, 100, 50, 50), Shape(CURVE, 160, 100, 40, 40)
    group = Shape(GROUP, 100, 100, 100, 50, children=[child_b, child_a])
    clip_child = Shape(CURVE, 300, 300, 30, 30)
    clip = Shape(RECT, 300, 300, 60, 60, powerclip=[clip_child])
    bg = Shape(RECT, 0, 0, 1000, 500, name="bg")
    hidden = Shape(CURVE, 500, 50, 20, 20, visible=False)
    layer = Layer("Layer 1", [top_text, group, clip, hidden, bg])
    guides = Layer("Guides", [], special=True)
    doc = FakeDoc(Page([guides, layer]), **kw)
    return _attach(doc), dict(top_text=top_text, group=group, clip=clip, bg=bg, hidden=hidden,
                              child_a=child_a, child_b=child_b, clip_child=clip_child)


def test_plan_png_size_clamps_long_side():
    assert max(scene_export.plan_png_size(3000, 1200)) == scene_export.IMG_MAX_LONG_PX
    tiny = scene_export.plan_png_size(1, 0.5)
    assert max(tiny) == scene_export.IMG_MIN_LONG_PX and tiny[0] == 2 * tiny[1]
    mid = scene_export.plan_png_size(100, 50)
    assert mid == (400, 200)


def test_walk_page_reverses_corel_order_and_skips_special_layers():
    doc, s = _sample_doc()
    layers, leaves = scene_export.walk_page(doc.ActivePage)
    assert len(layers) == 1 and layers[0]["name"] == "Layer 1"
    ids = [n["id"] for n in layers[0]["children"]]
    # bottom -> top: bg first (was last in Corel's list), text last
    assert ids == [f"s{s[k].StaticID}" for k in ("bg", "hidden", "clip", "group", "top_text")]


def test_walk_page_structure_kinds_and_leaves():
    doc, s = _sample_doc()
    layers, leaves = scene_export.walk_page(doc.ActivePage)
    by_id = {n["id"]: n for n in layers[0]["children"]}
    group = by_id[f"s{s['group'].StaticID}"]
    assert group["kind"] == "group" and group["image"] is None
    assert [c["id"] for c in group["children"]] == [f"s{s['child_a'].StaticID}", f"s{s['child_b'].StaticID}"]  # reversed too
    clip = by_id[f"s{s['clip'].StaticID}"]
    assert clip["kind"] == "powerclip" and len(clip["children"]) == 1
    text = by_id[f"s{s['top_text'].StaticID}"]
    assert text["text"] == {"content": "SHOP NAME", "font": "Nirmala UI", "size_pt": 120.0}
    leaf_ids = {n["id"] for n, _ in leaves}
    # group is structure only; the powerclip is one image; its content is not a separate leaf
    assert f"s{s['group'].StaticID}" not in leaf_ids
    assert f"s{s['clip'].StaticID}" in leaf_ids and f"s{s['clip_child'].StaticID}" not in leaf_ids
    assert {f"s{s['child_a'].StaticID}", f"s{s['child_b'].StaticID}"} <= leaf_ids
    assert by_id[f"s{s['hidden'].StaticID}"]["visible"] is False


def test_export_scene_writes_scene_and_picks_svg_or_png(tmp_path):
    doc, s = _sample_doc()
    steps = []
    scene = scene_export.export_scene(doc, tmp_path, on_step=steps.append)
    assert (tmp_path / "scene.json").is_file()
    assert json.loads((tmp_path / "scene.json").read_text(encoding="utf-8"))["page"] == {"width": 1000.0, "height": 500.0}
    assert scene["page_image"] == {"file": "page.png", "format": "png"}

    fmt = {c["name"]: c["fmt"] for c in doc.calls}
    assert fmt[f"s{s['bg'].StaticID}.svg"] == "svg"
    assert fmt[f"s{s['child_a'].StaticID}.svg"] == "svg"
    assert fmt[f"s{s['top_text'].StaticID}.png"] == "png"       # text never SVG (Corel emits <font>)
    assert fmt[f"s{s['clip'].StaticID}.png"] == "png"           # PowerClip container: PNG of the clipped result
    assert steps[0] == "walk" and steps[-2] == f"images {scene['stats']['leaves']}/{scene['stats']['leaves']}"
    assert steps[-1] == "page_image"
    # every leaf got an image reference and it exists on disk
    def leaves(nodes):
        for n in nodes:
            if n["kind"] == "group":
                yield from leaves(n["children"])
            else:
                yield n
    for n in leaves(scene["layers"][0]["children"]):
        assert n["image"], n["id"]
        assert (tmp_path / "img" / n["image"]["file"]).is_file()


def test_png_exports_are_selection_only_and_transparent(tmp_path):
    doc, s = _sample_doc()
    scene_export.export_scene(doc, tmp_path)
    text_call = next(c for c in doc.calls if c["name"] == f"s{s['top_text'].StaticID}.png")
    assert text_call["transparent"] is True and text_call["range_"] == scene_export.CDR_SELECTION
    assert text_call["size"] == scene_export.plan_png_size(200, 40)
    page_call = next(c for c in doc.calls if c["name"] == "page.png")
    assert page_call["range_"] == scene_export.CDR_CURRENT_PAGE and page_call["transparent"] is False


def test_svg_containing_a_font_falls_back_to_png(tmp_path):
    doc, s = _sample_doc(svg_body=b'<svg><defs><font id="FontID0"/></defs></svg>')
    scene = scene_export.export_scene(doc, tmp_path)
    bg = next(n for n in scene["layers"][0]["children"] if n["id"] == f"s{s['bg'].StaticID}")
    assert bg["image"]["format"] == "png"
    assert not (tmp_path / "img" / f"s{s['bg'].StaticID}.svg").exists()


def test_hidden_shape_is_made_visible_for_export_and_restored(tmp_path):
    doc, s = _sample_doc()
    scene_export.export_scene(doc, tmp_path)
    assert doc.visible_at_export[s["hidden"].StaticID] is True
    assert s["hidden"].Visible is False


def test_one_failing_shape_does_not_sink_the_scene(tmp_path):
    doc, s = _sample_doc()
    doc.fail_on = {s["child_a"].StaticID}
    scene = scene_export.export_scene(doc, tmp_path)
    assert any(str(s["child_a"].StaticID) in f for f in scene["stats"]["image_failures"])
    assert scene["stats"]["leaf_images"] == scene["stats"]["leaves"] - 1


def test_corel_timeout_aborts_the_export(tmp_path):
    class CorelTimeout(Exception):
        pass

    doc, _ = _sample_doc()

    def run(fn, name):
        if name == "scene_image":
            raise CorelTimeout("killed")
        return fn()

    with pytest.raises(CorelTimeout):
        scene_export.export_scene(doc, tmp_path, run=run)
    assert not (tmp_path / "scene.json").exists()


def test_exported_scene_is_replayable(tmp_path):
    doc, s = _sample_doc()
    scene = scene_export.export_scene(doc, tmp_path)
    gid = f"s{s['group'].StaticID}"
    out = apply_ops(scene, [{"op": "move", "ids": [gid], "dx": 10, "dy": 0}])
    grp = next(n for n in out["layers"][0]["children"] if n["id"] == gid)
    assert grp["x"] == 110 and all(c["x"] >= 110 for c in grp["children"])


def test_mock_scene_is_valid_and_replayable(tmp_path):
    scene = scene_export.mock_scene(tmp_path, 3000, 1200)
    assert scene["page"] == {"width": 3000.0, "height": 1200.0}
    for n in scene["layers"][0]["children"]:
        if n["kind"] == "shape":
            assert (tmp_path / "img" / n["image"]["file"]).is_file()
    out = apply_ops(scene, [{"op": "ungroup", "id": "s5"}])
    assert [n["id"] for n in out["layers"][0]["children"]] == ["s1", "s2", "s3", "s4", "s6"]
