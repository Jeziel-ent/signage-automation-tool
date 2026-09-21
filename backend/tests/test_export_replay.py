"""Phase D: replay + export logic against a small fake CorelDRAW document.

The fake models exactly the CorelDRAW behaviours that were verified live (see
export_replay.py's docstring): top-first shape lists, order methods, MoveToLayer
extracting a shape from a group, Ungroup returning nothing, Duplicate, Delete
removing emptied groups, LeftX/BottomY assignable, groups whose bbox follows
their children. The real thing was exercised against a generated dalmia board
(14 mixed operations, 142 objects compared) - these tests protect the logic
(id mapping, z-order settling, ghost handling, verification) from regressions.
"""
from __future__ import annotations

import copy

import pytest

from app import export_replay as er
from app import scene_export, scene_ops

_ids = iter(range(1000, 100000))


class Coll:
    def __init__(self, items):
        self._items = items

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i - 1]


class Story:
    def __init__(self, text, font="Arial", size=20.0, installed=("Arial", "Nirmala UI")):
        self.Text, self._font, self.Size, self._installed = text, font, size, installed

    @property
    def Font(self):
        return self._font

    @Font.setter
    def Font(self, v):
        if v in self._installed:        # CorelDRAW silently ignores fonts it does not have
            self._font = v


class FShape:
    def __init__(self, doc, type_=3, x=0.0, y=0.0, w=10.0, h=10.0, text=None):
        self.doc, self.Type, self.StaticID = doc, type_, next(_ids)
        self._x, self._y, self._w, self._h = x, y, w, h
        self.Visible, self.Locked, self.Name, self.RotationAngle = True, False, "", 0.0
        self._kids: list[FShape] = []        # top-first, like Shapes.Item(1) = topmost
        self.parent = None                   # FLayer | FShape (group)
        self.PowerClip = None
        self.Text = type("T", (), {"Story": Story(text), "Type": 0})() if text is not None else None

    # geometry (a group's box follows its children)
    def _box(self):
        if self.Type == 7 and self._kids:
            xs = [k.LeftX for k in self._kids]; ys = [k.BottomY for k in self._kids]
            xe = [k.LeftX + k.SizeWidth for k in self._kids]; ye = [k.BottomY + k.SizeHeight for k in self._kids]
            return min(xs), min(ys), max(xe) - min(xs), max(ye) - min(ys)
        return self._x, self._y, self._w, self._h

    LeftX = property(lambda s: s._box()[0], lambda s, v: s._shift(v - s._box()[0], 0))
    BottomY = property(lambda s: s._box()[1], lambda s, v: s._shift(0, v - s._box()[1]))
    SizeWidth = property(lambda s: s._box()[2])
    SizeHeight = property(lambda s: s._box()[3])

    def _shift(self, dx, dy):
        self._x += dx; self._y += dy
        for k in self._kids:
            k._shift(dx, dy)

    def Move(self, dx, dy):
        self._shift(dx, dy)

    def SetSize(self, w, h):
        x, y, ow, oh = self._box()
        if self.Type == 7:
            for k in self._kids:
                k._scale(x, y, w / ow, h / oh)
        else:
            self._w, self._h = w, h

    def _scale(self, ox, oy, sx, sy):
        if self.Type == 7:
            for k in self._kids:
                k._scale(ox, oy, sx, sy)
        else:
            self._x = ox + (self._x - ox) * sx; self._y = oy + (self._y - oy) * sy
            self._w *= sx; self._h *= sy

    @property
    def Shapes(self):
        return Coll(self._kids)

    def _siblings(self):
        return self.parent._kids

    def OrderToFront(self):
        s = self._siblings(); s.remove(self); s.insert(0, self)

    def OrderToBack(self):
        s = self._siblings(); s.remove(self); s.append(self)

    def OrderForwardOne(self):
        s = self._siblings(); i = s.index(self)
        if i > 0:
            s[i], s[i - 1] = s[i - 1], s[i]

    def OrderBackOne(self):
        s = self._siblings(); i = s.index(self)
        if i < len(s) - 1:
            s[i], s[i + 1] = s[i + 1], s[i]

    def _detach(self):
        p = self.parent
        p._kids.remove(self)
        self.parent = None
        if isinstance(p, FShape) and not p._kids:       # an emptied group disappears
            p._detach()

    def Delete(self):
        self._detach()

    def Duplicate(self, dx=0.0, dy=0.0):
        d = self._copy()
        d.parent = self.parent
        self.parent._kids.insert(0, d)
        return d

    def _copy(self):
        d = FShape(self.doc, self.Type, self._x, self._y, self._w, self._h)
        d.Visible, d.Name = self.Visible, self.Name
        if self.Text is not None:
            d.Text = type("T", (), {"Story": Story(self.Text.Story.Text, self.Text.Story._font, self.Text.Story.Size), "Type": 0})()
        for k in self._kids:
            kc = k._copy(); kc.parent = d; d._kids.append(kc)
        return d

    def MoveToLayer(self, layer):
        self._detach()
        self.parent = layer
        layer._kids.insert(0, self)

    def Ungroup(self):
        p, i = self.parent, self.parent._kids.index(self)
        kids = list(self._kids)
        p._kids[i:i + 1] = kids
        for k in kids:
            k.parent = p
        self.parent = None
        return None

    @property
    def ParentGroup(self):
        return self.parent if isinstance(self.parent, FShape) else None


class FLayer:
    def __init__(self, name, special=False):
        self.Name, self.IsSpecialLayer, self.Visible, self.Editable = name, special, True, True
        self._kids: list[FShape] = []
        self.page = None

    @property
    def Shapes(self):
        return Coll(self._kids)

    def MoveAbove(self, other):
        L = self.page._layers; L.remove(self); L.insert(L.index(other), self)

    def MoveBelow(self, other):
        L = self.page._layers; L.remove(self); L.insert(L.index(other) + 1, self)


class FPage:
    def __init__(self, layers, w, h):
        self._layers, self.SizeWidth, self.SizeHeight = layers, w, h
        for l in layers:
            l.page = self

    @property
    def Layers(self):
        return Coll(self._layers)

    def SetSize(self, w, h):
        self.SizeWidth, self.SizeHeight = w, h


class FDoc:
    def __init__(self, page):
        self.ActivePage, self.Unit = page, 3

    def CreateShapeRangeFromArray(self, shapes):
        doc = self

        class R:
            def Group(_):
                parent = shapes[0].parent
                idx = min(parent._kids.index(s) for s in shapes)
                ordered = sorted(shapes, key=lambda s: s.parent._kids.index(s))       # top-first
                g = FShape(doc, 7)
                for s in ordered:
                    s.parent._kids.remove(s)
                    s.parent = g
                    g._kids.append(s)
                g.parent = parent
                parent._kids.insert(idx, g)
                return g
        return R()


def build_doc():
    doc = FDoc(FPage([FLayer("Guides", special=True), FLayer("Top"), FLayer("Bottom")], 1000.0, 500.0))
    top, bottom = doc.ActivePage._layers[1], doc.ActivePage._layers[2]

    def add(layer, *a, **k):
        s = FShape(doc, *a, **k); s.parent = layer; layer._kids.append(s); return s   # appended = bottom-most

    # Item order is top-first, so append from the TOP object down to the bottom one
    add(bottom, 3, 100, 100, 50, 50)                       # c3 (top of Bottom layer)
    add(bottom, 3, 30, 30, 40, 40)                         # c2
    add(bottom, 1, 0, 0, 1000, 500)                        # bg (bottom-most)
    add(top, 6, 500, 20, 200, 40, text="SHOP")             # text (top-most)
    g = FShape(doc, 7); g.parent = top; top._kids.append(g)
    for (x, y) in ((300, 300), (360, 300), (420, 300)):    # group children, top-first
        k = FShape(doc, 3, x, y, 40, 40); k.parent = g; g._kids.append(k)
    add(top, 3, 700, 300, 60, 60)                          # loose curve
    return doc


def fresh():
    doc = build_doc()
    scene = {"page": {"width": 1000.0, "height": 500.0}, "layers": scene_export.walk_page(doc.ActivePage)[0]}
    return doc, scene


def run(ops, doc=None, scene=None):
    if doc is None:
        doc, scene = fresh()
    r = er.Replayer(doc, scene, ops)
    r.run()
    expected = scene_ops.apply_ops(scene, ops)
    return doc, r, expected, er.verify(doc.ActivePage, expected)


def ids_of(scene):
    top = scene["layers"][-1]["children"]                    # layers are bottom -> top; "Top" is last
    return top


# ------------------------------------------------------------------ options

def test_normalize_options_defaults_and_validation():
    o = er.normalize_options(["pdf", "png"], None)
    assert o["pdf"] == {"color_mode": "native", "text": "embed", "bitmap_dpi": 200}
    assert o["raster"]["mode"] == "max_px" and o["raster"]["png_background"] == "transparent"
    for bad in ({"pdf": {"color_mode": "lab"}}, {"pdf": {"text": "x"}}, {"pdf": {"bitmap_dpi": 123}},
                {"raster": {"mode": "zoom"}}, {"raster": {"png_background": "red"}}):
        with pytest.raises(er.ExportOptionError):
            er.normalize_options(["pdf"], bad)
    with pytest.raises(er.ExportOptionError):
        er.normalize_options([], None)
    with pytest.raises(er.ExportOptionError):
        er.normalize_options(["gif"], None)


def test_resolve_raster_caps_and_modes():
    r = er.resolve_raster(3048, 1219.2, {"mode": "max_px", "max_px": 4000, "dpi": 96})
    assert (r["w_px"], r["h_px"]) == (4000, 1600) and r["megapixels"] == 6.4
    d = er.resolve_raster(3048, 1219.2, {"mode": "dpi", "dpi": 150, "max_px": 0})
    assert d["w_px"] == 18000
    with pytest.raises(er.ExportOptionError, match="too large"):
        er.resolve_raster(3048, 1219.2, {"mode": "dpi", "dpi": 300, "max_px": 0})     # 36000 px: the known blow-up
    with pytest.raises(er.ExportOptionError, match="megapixels"):
        er.resolve_raster(2000, 2000, {"mode": "dpi", "dpi": 250, "max_px": 0})       # 19685 px each side, ~387 MP


def test_plan_steps_follow_formats_and_measured_durations():
    plan = er.plan_steps(["png", "cdr"], 3)
    assert [s["key"] for s in plan] == ["launch", "open", "replay", "verify", "cdr", "png"]
    assert plan[-1]["endPct"] == 100 and all(a["endPct"] < b["endPct"] for a, b in zip(plan, plan[1:]))
    assert [s["key"] for s in er.plan_steps(["pdf"], 0)] == ["launch", "open", "pdf"]     # no edits: no replay/verify
    slow_pdf = er.plan_steps(["pdf", "png"], 0, {"pdf": 60.0})
    fast_pdf = er.plan_steps(["pdf", "png"], 0, {"pdf": 1.0})
    assert slow_pdf[2]["endPct"] - slow_pdf[1]["endPct"] > fast_pdf[2]["endPct"] - fast_pdf[1]["endPct"]


# ------------------------------------------------------------------- replay

def test_index_matches_scene_ids():
    doc, scene = fresh()
    shapes, layers = er.index_doc(doc.ActivePage)
    assert set(layers) == {l["id"] for l in scene["layers"]}
    assert set(shapes) == {n["id"] for n in scene_ops.iter_nodes(scene)}


def test_no_ops_verifies_clean():
    doc, scene = fresh()
    assert er.verify(doc.ActivePage, scene)["ok"]


def test_move_resize_order_visibility():
    doc, scene = fresh()
    top = ids_of(scene)
    text, group, curve = top[2]["id"], top[1]["id"], top[0]["id"]
    ops = [
        {"op": "move", "ids": [text], "dx": 25.5, "dy": -4},
        {"op": "resize", "ids": [group], "from": {k: top[1][k] for k in "xywh"}, "to": {"x": 300, "y": 300, "w": 240, "h": 80}},
        {"op": "order", "id": curve, "mode": "back"},
        {"op": "order", "id": text, "mode": "backward"},
        {"op": "visibility", "id": curve, "visible": False},
        {"op": "visibility", "id": "L3", "visible": False},
    ]
    _, r, expected, v = run(ops, doc, scene)
    assert v["ok"], v["mismatches"]
    assert doc.ActivePage._layers[2].Visible is False and r.warnings == []


def test_group_ungroup_and_delete():
    doc, scene = fresh()
    top = ids_of(scene)
    inner = [c["id"] for c in top[1]["children"]]
    ops = [
        {"op": "group", "ids": [top[0]["id"], top[2]["id"]], "group_id": "nG", "name": "Pair"},
        {"op": "ungroup", "id": top[1]["id"]},
        {"op": "delete", "ids": [inner[0]]},
    ]
    _, r, expected, v = run(ops, doc, scene)
    assert v["ok"], v["mismatches"]
    assert [k.Type for k in doc.ActivePage._layers[1]._kids].count(7) == 1          # only the new "Pair" group is left


def test_deleting_all_children_removes_the_group_like_the_editor_does():
    doc, scene = fresh()
    inner = [c["id"] for c in ids_of(scene)[1]["children"]]
    _, _, _, v = run([{"op": "delete", "ids": inner}], doc, scene)
    assert v["ok"], v["mismatches"]


def test_paste_duplicates_and_cut_paste_keeps_the_source_alive_until_the_end():
    doc, scene = fresh()
    top = ids_of(scene)
    curve = top[0]
    copy_node = dict(copy.deepcopy(curve), id="nCopy", src=curve["id"], x=curve["x"] + 50)
    cut_node = dict(copy.deepcopy(top[2]), id="nCut", src=top[2]["id"], x=10, y=10)
    ops = [
        {"op": "paste", "parent": "L2", "nodes": [copy_node]},
        {"op": "delete", "ids": [top[2]["id"]]},                       # cut ...
        {"op": "paste", "parent": "L2", "nodes": [cut_node]},          # ... then paste: needs the deleted text alive
    ]
    doc2, r, expected, v = run(ops, doc, scene)
    assert v["ok"], v["mismatches"]
    assert r.ghosts == {}                                              # ghosts are really deleted at the end


def test_paste_of_a_missing_source_fails_loudly():
    doc, scene = fresh()
    node = dict(copy.deepcopy(ids_of(scene)[0]), id="nX", src="s99999")
    with pytest.raises(er.ReplayError, match="not in the document"):
        run([{"op": "paste", "parent": "L2", "nodes": [node]}], doc, scene)


def test_reorder_into_and_out_of_a_group_and_between_layers():
    doc, scene = fresh()
    top = ids_of(scene)
    g = top[1]["id"]
    loose, bottom_curve = top[0]["id"], scene["layers"][0]["children"][1]["id"]
    ops = [
        {"op": "reorder", "id": loose, "parent": g, "index": 1},                                 # loose curve into the group
        {"op": "reorder", "id": top[1]["children"][0]["id"], "parent": "L2", "index": 0},        # child out to the layer bottom
        {"op": "reorder", "id": bottom_curve, "parent": "L2", "index": 2},                       # from the other layer
    ]
    _, r, expected, v = run(ops, doc, scene)
    assert v["ok"], v["mismatches"]


def test_layer_order_and_page_size():
    doc, scene = fresh()
    _, _, expected, v = run([{"op": "layer_order", "id": "L3", "index": 1}, {"op": "page", "width": 800, "height": 400}], doc, scene)
    assert v["ok"], v["mismatches"]
    assert [l.Name for l in doc.ActivePage._layers if not l.IsSpecialLayer] == ["Bottom", "Top"]     # Corel lists top-first
    assert (doc.ActivePage.SizeWidth, doc.ActivePage.SizeHeight) == (800, 400)


def test_text_edit_applies_content_font_size_and_warns_when_the_font_is_missing():
    doc, scene = fresh()
    t = ids_of(scene)[2]["id"]
    _, r, _, v = run([{"op": "text", "id": t, "content": "NEW NAME", "font": "Nirmala UI", "size_pt": 55}], doc, scene)
    story = doc.ActivePage._layers[1]._kids[0].Text.Story
    assert (story.Text, story.Font, story.Size) == ("NEW NAME", "Nirmala UI", 55.0) and r.warnings == []
    doc, scene = fresh()
    t = ids_of(scene)[2]["id"]
    _, r, _, _ = run([{"op": "text", "id": t, "font": "Noto Sans Tamil"}], doc, scene)
    assert any("Noto Sans Tamil" in w and "not applied" in w for w in r.warnings)


def test_text_whose_size_changed_with_its_content_still_verifies_by_anchor():
    doc, scene = fresh()
    t = ids_of(scene)[2]["id"]
    ops = [{"op": "text", "id": t, "content": "MUCH LONGER SHOP NAME"}]
    doc, r, expected, _ = run(ops, doc, scene)
    shape = doc.ActivePage._layers[1]._kids[0]
    shape._w = 320.0                                    # CorelDRAW widened it; right-anchored -> left edge moved
    shape._x -= 120.0
    assert er.verify(doc.ActivePage, expected)["ok"]
    shape._x += 400.0                                   # ... but a genuinely displaced text is still caught
    assert not er.verify(doc.ActivePage, expected)["ok"]


def test_unreplayable_ops_are_reported_with_their_index():
    doc, scene = fresh()
    with pytest.raises(er.ReplayError, match=r"operation #2 \(move\)"):
        run([{"op": "move", "ids": [ids_of(scene)[0]["id"]], "dx": 1, "dy": 1}, {"op": "move", "ids": ["nope"], "dx": 1, "dy": 1}], doc, scene)


def test_verification_detects_a_document_that_does_not_match():
    doc, scene = fresh()
    expected = scene_ops.apply_ops(scene, [{"op": "move", "ids": [ids_of(scene)[0]["id"]], "dx": 50, "dy": 0}])
    v = er.verify(doc.ActivePage, expected)                # nothing was replayed
    assert not v["ok"] and any("x is" in m for m in v["mismatches"])
    doc.ActivePage._layers[1]._kids.pop(0)
    assert any("objects in CorelDRAW" in m for m in er.verify(doc.ActivePage, expected)["mismatches"])


# ------------------------------------------------------------------ exports

def test_export_pdf_sets_every_option_before_publishing():
    class Settings:
        pass

    class Doc:
        PDFSettings = Settings()
        published = None

        def PublishToPDF(self, p):
            Doc.published = (p, dict(vars(Doc.PDFSettings)))

    warnings = []
    applied = er.export_pdf(Doc(), er.Path("x.pdf"), {"color_mode": "cmyk", "text": "curves", "bitmap_dpi": 300}, warnings)
    assert applied["ColorMode"] == 1 and applied["TextAsCurves"] is True and applied["EmbedFonts"] is False
    assert applied["ColorResolution"] == 300 and Doc.published[0].endswith("x.pdf") and warnings == []


def test_export_raster_transparency_and_antialiasing():
    calls = []

    class Flt:
        def Finish(self):
            pass

    class Doc:
        def ExportBitmap(self, *a):
            calls.append(a)
            return Flt()

    size = {"dpi": 96.0, "w_px": 10, "h_px": 5}
    er.export_raster(Doc(), er.Path("a.png"), "png", size, {"png_background": "transparent", "antialias": True})
    er.export_raster(Doc(), er.Path("a.png"), "png", size, {"png_background": "white", "antialias": False})
    er.export_raster(Doc(), er.Path("a.jpg"), "jpeg", size, {"png_background": "transparent", "antialias": True})
    png, png_white_noaa, jpg = calls
    assert (png[1], png[2], png[4], png[5], png[6], png[8], png[10]) == (802, 1, 10, 5, 96.0, 1, True)   # explicit px size
    assert (png_white_noaa[8], png_white_noaa[10]) == (0, False)
    assert jpg[1] == 774 and jpg[10] is False           # JPEG is never transparent



# ---- PowerClip child text (scene_ops.py's one narrow exception to PowerClip
# contents being read-only; see corel_util.ensure_tamil_font_renders) ----

class FPowerClip:
    """CorelDRAW's Shape.PowerClip: exposes the clipped contents via .Shapes."""

    def __init__(self, kids):
        self._kids = kids

    @property
    def Shapes(self):
        return Coll(self._kids)


def _doc_with_powerclip_text(text="INNER", font="Arial"):
    doc = build_doc()
    top = doc.ActivePage._layers[1]
    container = FShape(doc, 1, 800, 400, 100, 50)            # the clip frame (rectangle)
    container.parent = top
    inner = FShape(doc, 6, 810, 410, 80, 20, text=text)      # text clipped inside it
    inner.Text.Story._font = font
    inner.parent = container
    container.PowerClip = FPowerClip([inner])
    top._kids.append(container)
    scene = {"page": {"width": 1000.0, "height": 500.0}, "layers": scene_export.walk_page(doc.ActivePage)[0]}
    return doc, scene, inner


def test_index_finds_powerclip_children_and_text_op_edits_them():
    doc, scene, inner = _doc_with_powerclip_text()
    child_id = f"s{inner.StaticID}"
    assert child_id in er.index_doc(doc.ActivePage)[0]
    _, r, _, _ = run([{"op": "text", "id": child_id, "content": "EDITED"}], doc, scene)
    assert inner.Text.Story.Text == "EDITED"
    assert r.warnings == []


def test_powerclip_child_text_edit_runs_the_tamil_font_fix():
    tamil = "அல் மதீனா"
    doc, scene, inner = _doc_with_powerclip_text(font="Arial")
    child_id = f"s{inner.StaticID}"
    _, r, _, _ = run([{"op": "text", "id": child_id, "content": tamil}], doc, scene)
    assert inner.Text.Story.Text == tamil
    assert inner.Text.Story.Font == "Nirmala UI"     # was "Arial" - would render as tofu boxes
    assert r.warnings == []


def _child_box(shape):
    return [round(shape.LeftX, 3), round(shape.BottomY, 3), round(shape.SizeWidth, 3), round(shape.SizeHeight, 3)]


def test_powerclip_child_move_replays_by_page_delta_and_leaves_the_clip_frame():
    doc, scene, inner = _doc_with_powerclip_text()
    container = inner.parent
    frame_before = _child_box(container)
    _, r, _, v = run([{"op": "move", "ids": [f"s{inner.StaticID}"], "dx": 12.5, "dy": -4}], doc, scene)
    assert _child_box(inner) == [822.5, 406.0, 80.0, 20.0]      # 810,410 + (12.5,-4)
    assert _child_box(container) == frame_before                # the frame never follows its contents
    assert v["ok"], v                                            # verify() now compares PowerClip contents
    assert r.warnings == []


def test_powerclip_child_resize_replays_position_and_size():
    doc, scene, inner = _doc_with_powerclip_text()
    cid = f"s{inner.StaticID}"
    frm = {"x": 810, "y": 410, "w": 80, "h": 20}
    to = {"x": 820, "y": 405, "w": 40, "h": 30}
    _, r, expected, v = run([{"op": "resize", "ids": [cid], "from": frm, "to": to}], doc, scene)
    assert _child_box(inner) == [820.0, 405.0, 40.0, 30.0]
    child = next(c for c in scene_ops.find_node(expected, next(n["id"] for n in scene_ops.iter_nodes(expected) if n["kind"] == "powerclip"))["children"])
    assert [child["x"], child["y"], child["w"], child["h"]] == [820.0, 405.0, 40.0, 30.0]
    assert scene_ops.find_node(expected, next(n["id"] for n in scene_ops.iter_nodes(expected) if n["kind"] == "powerclip")).get("stale") is True
    assert v["ok"], v


def test_verify_catches_a_powerclip_child_that_did_not_end_up_where_the_edit_says():
    doc, scene, inner = _doc_with_powerclip_text()
    _, _, expected, _ = run([{"op": "move", "ids": [f"s{inner.StaticID}"], "dx": 10, "dy": 0}], doc, scene)
    inner._x -= 40.0                                             # CorelDRAW put it somewhere else
    result = er.verify(doc.ActivePage, expected)
    assert not result["ok"] and any("x is" in m for m in result["mismatches"])


def test_powerclip_child_still_rejects_order_and_delete():
    doc, scene, inner = _doc_with_powerclip_text()
    cid = f"s{inner.StaticID}"
    for op in ({"op": "order", "id": cid, "mode": "front"}, {"op": "delete", "ids": [cid]}):
        with pytest.raises(Exception, match="PowerClip"):
            run([op], doc, scene)


# ---- lazily assigned PowerClip ids (found live: the scene's s48 was s208 in the replay document) ----

class LazyIdShape(FShape):
    """CorelDRAW gives a PowerClip's contents their StaticID the first time it is read."""

    @property
    def StaticID(self):
        if getattr(self, "_sid", None) is None:
            self._sid = next(self.doc._lazy_ids)
        return self._sid

    @StaticID.setter
    def StaticID(self, _):
        self._sid = None


def _lazy_powerclip_doc():
    global _ids
    _ids = iter(range(1000, 100000))                   # every "open" of the same file gives its eager shapes the same ids
    doc = build_doc()
    doc._lazy_ids = iter(range(200, 400))
    top = doc.ActivePage._layers[1]
    frame = FShape(doc, 1, 0, 0, 500, 500)
    frame.parent = top
    kids = [LazyIdShape(doc, 3, 100, 100, 50, 20),      # top-most (COM index 1)
            LazyIdShape(doc, 3, 200, 200, 60, 30),
            LazyIdShape(doc, 5, 10, 10, 400, 300)]      # bottom-most: the big photo
    for k in kids:
        k.parent = frame
    frame.PowerClip = FPowerClip(kids)
    top._kids.append(frame)
    return doc, kids


def test_powerclip_child_ids_match_between_scene_export_and_a_fresh_replay_document():
    doc1, _ = _lazy_powerclip_doc()
    scene = {"page": {"width": 1000.0, "height": 500.0}, "layers": scene_export.walk_page(doc1.ActivePage)[0]}
    photo_id = next(n["id"] for n in scene_ops.iter_nodes(scene) if n["type"] == "bitmap")
    doc2, kids2 = _lazy_powerclip_doc()                       # a fresh "open" of the same file
    ids2, _ = er.index_doc(doc2.ActivePage)
    assert ids2[photo_id] is kids2[2]                          # the scene's photo id resolves to the photo, not another child
    _, r, _, v = run([{"op": "move", "ids": [photo_id], "dx": 7, "dy": 3}], doc2, scene)
    assert _child_box(kids2[2]) == [17.0, 13.0, 400.0, 300.0]
    assert _child_box(kids2[0]) == [100.0, 100.0, 50.0, 20.0]  # the other children stayed put
    assert v["ok"], v


def test_replay_refuses_a_scene_whose_shapes_do_not_match_the_document():
    doc, scene, inner = _doc_with_powerclip_text()
    container = next(n for n in scene_ops.iter_nodes(scene) if n["kind"] == "powerclip")
    container["x"] += 40.0                                            # scene disagrees with the document
    with pytest.raises(er.ReplayError, match="does not match"):
        er.Replayer(doc, scene, [])
    doc, scene, _ = _doc_with_powerclip_text()
    scene_ops.find_node(scene, next(n["id"] for n in scene_ops.iter_nodes(scene) if n["type"] == "text" and n.get("text", {}).get("content") == "INNER"))["id"] = "s99999"
    with pytest.raises(er.ReplayError, match="not in the document"):
        er.Replayer(doc, scene, [])


def test_scene_check_ignores_text_extent_but_still_requires_text_to_be_text():
    doc, scene, inner = _doc_with_powerclip_text()
    node = scene_ops.find_node(scene, f"s{inner.StaticID}")
    node["w"] += 30.0                                   # font substitution changed the measured width: fine
    er.Replayer(doc, scene, [])
    inner.Type = 3                                      # ... but the id now names a non-text shape: not fine
    with pytest.raises(er.ReplayError, match="not in CorelDRAW"):
        er.Replayer(doc, scene, [])


def test_replay_refuses_a_swap_image_op_with_a_clear_message_instead_of_an_attributeerror():
    # product_engine.py's swap_image/update_product_slot ops update the editor's scene (canvas + saved
    # op list) but have no COM replay yet - Replayer.apply must refuse them cleanly rather than
    # crashing on the missing "_op_swap_image" method.
    doc = FDoc(FPage([FLayer("Top")], 200.0, 200.0))
    layer = doc.ActivePage._layers[0]
    bmp = FShape(doc, 5, 10.0, 10.0, 20.0, 10.0)
    bmp.parent = layer
    layer._kids.append(bmp)
    scene = {"page": {"width": 200.0, "height": 200.0}, "layers": scene_export.walk_page(doc.ActivePage)[0]}
    node_id = scene["layers"][0]["children"][0]["id"]
    ops = [{"op": "swap_image", "id": node_id, "asset": {"name": "a.png", "w": 40, "h": 20}}]
    with pytest.raises(er.ReplayError, match="cannot be exported to CorelDRAW yet"):
        er.Replayer(doc, scene, ops).run()
