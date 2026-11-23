"""master_analysis.extract_master against fake COM objects (NO CorelDRAW - these say nothing about a real CorelDRAW; see
docs/master-spatial-intelligence.md for what is and is not verified live)."""
from __future__ import annotations

from app import master_analysis as ma

_ids = iter(range(1, 100000))


class Coll:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i - 1]


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def color(cmyk=(0, 0, 0, 0)):
    c, m, y, k = cmyk
    rgb = [round(255 * (1 - c / 100) * (1 - k / 100)), round(255 * (1 - m / 100) * (1 - k / 100)), round(255 * (1 - y / 100) * (1 - k / 100))]
    return Obj(CMYKCyan=c, CMYKMagenta=m, CMYKYellow=y, CMYKBlack=k, Type=2, IsCMYK=True, IsSpot=False, SpotColorName="",
               HexValue="#%02X%02X%02X" % tuple(rgb))             # RGBRed... deliberately absent: raises like CorelDRAW on a CMYK colour


class Shape:
    def __init__(self, type_, x=0, y=0, w=10, h=10, name="", children=None, powerclip=None, text=None, fill=None, outline=True,
                 visible=True, locked=False, rotation=0.0, broken=()):
        self.Type = type_
        self.StaticID = next(_ids)
        self.LeftX, self.BottomY, self.SizeWidth, self.SizeHeight = x, y, w, h
        self.Name, self.RotationAngle, self.Visible, self.Locked = name, rotation, visible, locked
        self.Shapes = Coll(children or [])
        self.PowerClip = Obj(Shapes=Coll(powerclip)) if powerclip is not None else None
        self.Text = Obj(Story=Obj(Text=text, Font="Arial", Size=40.0), Type=0) if text is not None else None
        self.Fill = fill
        if outline:
            self.Outline = Obj(Type=1, Width=0.5, Color=color((0, 0, 0, 100)))
        self._broken = set(broken)

    def __getattribute__(self, item):
        if item != "_broken" and item in object.__getattribute__(self, "_broken"):
            raise RuntimeError(f"{item} is not available")
        return object.__getattribute__(self, item)


class Layer:
    def __init__(self, shapes, name="Layer 1", special=False):
        self.Shapes, self.Name, self.IsSpecialLayer, self.Visible, self.Editable = Coll(shapes), name, special, True, True


class Page:
    def __init__(self, shapes, layers=None, w=3000.0, h=1000.0):
        self.Shapes, self.SizeWidth, self.SizeHeight = Coll(shapes), w, h
        self.Layers = Coll(layers if layers is not None else [Layer(shapes)])


class Doc:
    def __init__(self, page):
        self.ActivePage, self.Unit = page, 0


def uniform(c=(95, 80, 4, 0)):
    return Obj(Type=1, UniformColor=color(c))


def test_extraction_reads_geometry_appearance_text_hierarchy_and_ids():
    inner = [Shape(3, 10, 10, 20, 20, "a"), Shape(6, 40, 10, 50, 20, "t", text="HELLO")]
    grp = Shape(7, 0, 0, 100, 40, "grp", children=inner)
    bg = Shape(1, 0, 0, 3000, 1000, "bg", fill=uniform())
    page = Page([grp, bg])                       # Shapes.Item(1) is the TOPMOST
    out = ma.extract_master(Doc(page), Obj(Version="27.0"))
    assert out["extractor_version"] == ma.EXTRACTOR_VERSION and out["corel_version"] == "27.0"
    assert out["page"] == {"width_mm": 3000.0, "height_mm": 1000.0, "unit": "mm", "origin": "bottom-left"}
    ids = [o["id"] for o in out["objects"]]
    assert ids[:2] == ["0", "1"] or ids[0] == "0"                         # top-level ids = index in page.Shapes (engine convention)
    g = next(o for o in out["objects"] if o["name"] == "grp")
    assert g["id"] == "0" and g["parent"] is None and len(g["children"]) == 2 and g["scene_id"].startswith("s")
    kids = [o for o in out["objects"] if o["parent"] == "0"]
    assert [k["id"] for k in kids] == ["0.0", "0.1"]
    text = next(o for o in out["objects"] if o["type"] == "text")
    assert text["text"]["content"] == "HELLO" and text["text"]["font"] == "Arial"
    b = next(o for o in out["objects"] if o["name"] == "bg")
    assert b["fill"]["type"] == "uniform" and b["fill"]["color"]["cmyk"] == [95, 80, 4, 0]
    assert b["fill"]["color"]["rgb"] == [13, 51, 245]           # from Color.HexValue, not from RGBRed (which raises on CMYK)
    assert b["outline"]["width_mm"] == 0.5 and b["outline"]["color"]["cmyk"] == [0, 0, 0, 100]


def test_an_unreadable_property_is_recorded_and_never_stops_the_walk():
    s1 = Shape(3, 1, 1, 5, 5, "x", broken={"Outline", "Transparency", "Fill"})
    s2 = Shape(3, 2, 2, 5, 5, "y", fill=uniform())
    out = ma.extract_master(Doc(Page([s1, s2])), None)
    assert len(out["objects"]) == 2
    assert {"outline", "transparency", "fill"} <= set(out["errors"])
    assert out["errors"]["outline"]["count"] == 1
    assert out["objects"][0].get("fill") is None                           # not invented
    assert out["objects"][1]["fill"]["type"] == "uniform"


def test_an_object_with_no_readable_box_is_left_out_and_counted():
    bad = Shape(3, 0, 0, 5, 5, "bad", broken={"SizeWidth"})
    good = Shape(3, 0, 0, 5, 5, "good")
    out = ma.extract_master(Doc(Page([good, bad])), None)
    assert [o["name"] for o in out["objects"]] == ["good"]
    assert out["errors"]["geometry"]["count"] == 1


def test_powerclip_children_and_frame_flag():
    kids = [Shape(5, 10, 10, 40, 40, "pic"), Shape(3, 0, 0, 30, 30, "shape")]
    pc = Shape(1, 0, 0, 3000, 1000, "clip", powerclip=kids, fill=uniform((0, 0, 0, 0)))
    out = ma.extract_master(Doc(Page([pc])), None)
    node = out["objects"][0]
    assert node["kind"] == "powerclip" and len(node["children"]) == 2 and "powerclip" in node
    assert [o["id"] for o in out["objects"] if o["parent"] == "0"] == ["0.0", "0.1"]


def test_page_shapes_and_layer_walk_difference_is_reported_not_assumed():
    a, b = Shape(3, 0, 0, 5, 5, "a"), Shape(3, 9, 9, 5, 5, "b")
    page = Page([a, b], layers=[Layer([a])])                               # the layer walk misses b
    out = ma.extract_master(Doc(page), None)
    sets = out["shape_sets"]
    assert sets["page_shapes"] == 2 and sets["layer_shapes"] == 1 and sets["equal"] is False
    assert len(sets["only_in_page_shapes"]) == 1


def test_object_limit_truncates_instead_of_running_away(monkeypatch):
    monkeypatch.setattr(ma, "MAX_OBJECTS", 3)
    out = ma.extract_master(Doc(Page([Shape(3, i, 0, 1, 1) for i in range(10)])), None)
    assert len(out["objects"]) == 3 and out["truncated"] is True


def test_fountain_fill_and_bitmap_info():
    f = Obj(Type=2, Fountain=Obj(StartColor=color((0, 0, 0, 0)), EndColor=color((100, 0, 0, 0)), Angle=90.0, Type=1))
    bm = Shape(5, 0, 0, 50, 50, "bm", fill=f)
    bm.Bitmap = Obj(SizeWidth=1000, SizeHeight=800, ResolutionX=300.0, ResolutionY=300.0)
    out = ma.extract_master(Doc(Page([bm])), None)
    n = out["objects"][0]
    assert n["fill"]["type"] == "fountain" and n["fill"]["fountain"]["angle"] == 90.0 and n["fill"]["fountain"]["end"]["rgb"] == [0, 255, 255]
    assert n["bitmap"] == {"px_w": 1000, "px_h": 800, "dpi_x": 300.0, "dpi_y": 300.0}
