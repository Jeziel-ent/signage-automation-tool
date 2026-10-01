"""Shop-name binding, 1.25 master routing and square/vertical layout: the master's own shop name taken from its
designer-style file name, the local (Tamil) name carried from an import to the engine, the Tamil line found next to the
matched English one, tiling limited to real shape changes, and a stretched background PowerClip keeping its
foreground contents in proportion (fake COM)."""
from __future__ import annotations

import pytest

from app import layout
from app.batch_import import clean_shop_name, shop_name_from_filename
from app.engines import CorelEngine
from app.layout import Obj, compute_layout, find_local_partner_ids
from tests.test_main_v2 import _fake_cdr_bytes, client  # noqa: F401  (fixture re-export)

IN, FT = 25.4, 304.8
DARSHAN_FILE = "76 - 36 X 48 Inch - Nonlit - SRI KANNIYAMMAN NATTU MARUNTHU KADAI.cdr"
TAMIL = "ஸ்ரீ கன்னியம்மன் நாட்டு மருந்து கடை"


# ------------------------------------------------------------ file names
def test_shop_name_from_a_designer_file_name():
    assert shop_name_from_filename(DARSHAN_FILE) == "SRI KANNIYAMMAN NATTU MARUNTHU KADAI"
    assert shop_name_from_filename("66 - 8 X 4 Feet - Nonlit - VASANTHAM ENTERPRISES - Copy.cdr") == "VASANTHAM ENTERPRISES"
    assert shop_name_from_filename("master.cdr") is None
    assert shop_name_from_filename(None) is None


def test_clean_shop_name_keeps_a_plain_name():
    assert clean_shop_name("73 - 60 X 75 Inch - Nonlit - SRI AMBIRAMI PROVISON STORES.cdr") == "SRI AMBIRAMI PROVISON STORES"
    assert clean_shop_name("  Anish - Stores ") == "Anish - Stores"


# ------------------------------------------------------------ Tamil partner line
def _footer():
    en = Obj("en", "t1", "text", 100, 134, 800, 29, "SRI KANNIYAMMAN NATTU MARUNTHU KADAI")
    ta = Obj("ta", "t2", "text", 100, 57, 800, 42, TAMIL)
    far = Obj("far", "t3", "text", 100, 900, 300, 40, "பிளாக் ஸ்டோன்")   # a Tamil logo caption, far away
    return [en, ta, far]


def test_local_partner_is_the_tamil_line_stacked_next_to_the_english_name():
    objs = _footer()
    ids = layout.find_shopname_ids(objs, "SRI KANNIYAMMAN NATTU MARUNTHU KADAI")
    assert ids == {"en"}
    assert find_local_partner_ids(objs, ids) == {"ta"}
    assert find_local_partner_ids(objs, set()) == set()


def test_local_partner_beside_the_english_name_on_the_same_row():
    # landscape DARSHAN master (125x48 in), footer: English left, Tamil right on one row
    en = Obj("3", "a", "text", 106, 138, 1447, 52, "SRI KANNIYAMMAN NATTU MARUNTHU KADAI")
    ta = Obj("2", "b", "text", 1634, 135, 1307, 68, TAMIL)
    cap = Obj("9", "c", "text", 2555, 1300, 333, 40, "பிளாக் ஸ்டோன்")
    assert find_local_partner_ids([en, ta, cap], {"3"}) == {"2"}


def test_both_lines_are_rewritten_with_the_imported_names():
    objs = _footer()
    ids = layout.find_shopname_ids(objs, "SRI KANNIYAMMAN NATTU MARUNTHU KADAI")
    ids |= find_local_partner_ids(objs, ids)
    placed = {p.id: p for p in compute_layout(objs, 914, 1219, 914, 1219, shop_name="ANISH STORES",
                                              shop_name_local="அனிஷ் ஸ்டோர்ஸ்", shopname_ids=ids)}
    assert placed["en"].text == "ANISH STORES"
    assert placed["ta"].text == "அனிஷ் ஸ்டோர்ஸ்" and placed["ta"].font == layout.TAMIL_FONT
    assert placed["far"].text is None


# ------------------------------------------------------------ tiling
PORTRAIT = (36 * IN, 48 * IN)
LANDSCAPE = (125 * IN, 48 * IN)


@pytest.mark.parametrize("master,target", [
    (PORTRAIT, (60 * IN, 75 * IN)), (PORTRAIT, (6 * FT, 6 * FT)), (PORTRAIT, (5 * FT, 5 * FT)),
    (LANDSCAPE, (11 * FT, 6 * FT)), (LANDSCAPE, (7 * FT, 5 * FT)),
])
def test_no_tiling_when_the_board_barely_changes_shape_or_is_square(master, target):
    assert layout._tile_plan(*master, *target) == (None, 1)


@pytest.mark.parametrize("master,target,want", [
    ((120 * IN, 48 * IN), (180 * IN, 48 * IN), ("x", 2)), ((120 * IN, 48 * IN), (180 * IN, 60 * IN), ("x", 2)),
    ((120 * IN, 48 * IN), (216 * IN, 48 * IN), ("x", 2)), ((120 * IN, 48 * IN), (240 * IN, 60 * IN), ("x", 2)),
    ((12 * FT, 4 * FT), (2 * FT, 6 * FT), ("y", 2)),
])
def test_real_wide_and_tall_tiling_cases_are_unchanged(master, target, want):
    assert layout._tile_plan(*master, *target) == want


def test_a_portrait_master_enlarged_to_60x75_has_one_copy_of_each_logo():
    objs = [Obj("bg", "b", "shape", 0, 0, *PORTRAIT), Obj("logo", "l", "group", 200, 685, 514, 344),
            Obj("badge", "g", "group", 51, 987, 201, 179)]
    placed = compute_layout(objs, *PORTRAIT, 60 * IN, 75 * IN, tile=True)
    assert sorted(p.id for p in placed) == ["badge", "bg", "logo"]


# ------------------------------------------------------------ fake COM
class _Coll:
    def __init__(self, items):
        self.items = items
        self.Count = len(items)

    def Item(self, i):
        return self.items[i - 1]


class _Shape:
    def __init__(self, x, y, w, h, kids=None, text=None, name=""):
        self.LeftX, self.BottomY, self.SizeWidth, self.SizeHeight = x, y, w, h
        self.Name = name
        self.Type = CorelEngine.SHAPE_TEXT if text is not None else 1
        if text is not None:
            self.Text = type("T", (), {})()
            self.Text.Story = type("S", (), {"Text": text, "Font": "Arial", "Size": 100.0})()
        self.PowerClip = type("P", (), {"Shapes": _Coll(kids)})() if kids is not None else None

    def SetSize(self, w, h):
        self.SizeWidth, self.SizeHeight = w, h


def test_stretched_background_clip_keeps_its_foreground_in_proportion():
    # 36x48 in portrait master stretched to a 6x6 ft square: sx = 2.0, sy = 1.5. The table composite (as CorelDRAW
    # left it after the frame stretch) goes back to its original aspect at the smaller factor, same bottom edge.
    page = 6 * FT
    table = _Shape(200.0, 300.0, 600.0 * 2.0, 400.0 * 1.5)   # master: x 100..700 of 914 - clear of both edges
    backdrop = _Shape(-50.0, -50.0, page + 100, page + 100)
    offpage = _Shape(0.0, -900.0, 1200.0, 1000.0)
    clip = _Shape(0, 0, page, page, kids=[table, backdrop, offpage])
    warnings = []
    assert CorelEngine._undistort_clip_contents(clip, 2.0, 1.5, page, page, warnings) == 1
    assert table.SizeWidth == pytest.approx(600 * 1.5) and table.SizeHeight == pytest.approx(400 * 1.5)
    assert table.BottomY == 300.0
    assert table.LeftX + table.SizeWidth / 2 == pytest.approx(200 + 1200 / 2)      # same centre
    assert backdrop.SizeWidth == page + 100 and offpage.SizeWidth == 1200.0       # backdrop keeps the stretch
    assert warnings and "in proportion" in warnings[0]


def test_uniform_scale_leaves_clip_contents_alone():
    table = _Shape(200.0, 300.0, 800.0, 400.0)
    clip = _Shape(0, 0, 2000, 2000, kids=[table])
    assert CorelEngine._undistort_clip_contents(clip, 1.5, 1.5, 2000, 2000, []) == 0


def test_nested_shop_name_texts_are_rewritten_in_place():
    en = _Shape(100, 134, 800, 29, text="SRI KANNIYAMMAN NATTU MARUNTHU KADAI")
    ta = _Shape(100, 57, 800, 42, text=TAMIL)
    other = _Shape(100, 900, 300, 40, text="DARSHAN INCENSE")
    group = _Shape(0, 0, 1000, 1000, kids=[en, ta, other])
    shop = {"name": "ANISH STORES", "shop_name_local": "அனிஷ் ஸ்டோர்ஸ்",
            "master_shop_name": "SRI KANNIYAMMAN NATTU MARUNTHU KADAI"}
    assert CorelEngine._replace_nested_shopnames([group], shop, []) == 2
    assert en.Text.Story.Text == "ANISH STORES"
    assert ta.Text.Story.Text == "அனிஷ் ஸ்டோர்ஸ்" and ta.Text.Story.Font == layout.TAMIL_FONT
    assert other.Text.Story.Text == "DARSHAN INCENSE"


def test_nested_tamil_line_is_left_alone_without_a_local_name():
    en = _Shape(100, 134, 800, 29, text="SRI KANNIYAMMAN NATTU MARUNTHU KADAI")
    ta = _Shape(100, 57, 800, 42, text=TAMIL)
    group = _Shape(0, 0, 1000, 1000, kids=[en, ta])
    shop = {"name": "ANISH STORES", "master_shop_name": "SRI KANNIYAMMAN NATTU MARUNTHU KADAI"}
    assert CorelEngine._replace_nested_shopnames([group], shop, []) == 1
    assert ta.Text.Story.Text == TAMIL


# ------------------------------------------------------------ API
def test_upload_stores_the_masters_shop_name_and_convert_passes_every_name(client):
    r = client.post("/api/v2/upload", data={"brand": "Adinn", "orientation": "portrait"},
                    files={"master": (DARSHAN_FILE, _fake_cdr_bytes(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["master_shop_name"] == "SRI KANNIYAMMAN NATTU MARUNTHU KADAI"
    s = client.post(f"/api/v2/jobs/{job['id']}/shops",
                    json={"name": "67 - 6 X 6 Feet - Nonlit - KALKEE POOJA STORES.cdr", "width": 6, "height": 6,
                          "unit": "ft", "shop_name_local": "கல்கி பூஜா ஸ்டோர்ஸ்", "portrait_master_id": job["id"]})
    assert s.status_code == 200, s.text
    shop = s.json()
    assert shop["shop_name_local"] == "கல்கி பூஜா ஸ்டோர்ஸ்"
    import app.main as main
    spec, used = main._convert_job(shop["id"])
    assert spec["shop"]["name"] == "KALKEE POOJA STORES"
    assert spec["shop"]["shop_name_local"] == "கல்கி பூஜா ஸ்டோர்ஸ்"
    assert spec["shop"]["master_shop_name"] == "SRI KANNIYAMMAN NATTU MARUNTHU KADAI"
    assert used["orientation"] == "portrait"

    p = client.patch(f"/api/v2/jobs/{job['id']}/master-shop-name", json={"master_shop_name_local": TAMIL})
    assert p.status_code == 200 and p.json()["master_shop_name_local"] == TAMIL
    assert main._convert_job(shop["id"])[0]["shop"]["master_shop_name_local"] == TAMIL

    e = client.patch(f"/api/v2/shops/{shop['id']}", json={"shop_name_local": ""})
    assert e.status_code == 200 and e.json()["shop_name_local"] is None
    assert "shop_name_local" not in main._convert_job(shop["id"])[0]["shop"]


def test_upload_accepts_an_explicit_master_shop_name(client):
    r = client.post("/api/v2/upload", data={"brand": "Adinn", "master_shop_name": "AL MADEENA POOJA STORE"},
                    files={"master": ("m.cdr", _fake_cdr_bytes(), "application/octet-stream")})
    assert r.json()["master_shop_name"] == "AL MADEENA POOJA STORE"


def test_clip_child_on_the_left_edge_stays_on_it():
    # landscape DARSHAN master (3175 wide) -> 167x29 in (4242 x 737): sx 1.336, sy 0.604. The table runs off the left
    # edge on the master (x = -40); after the frame stretch it must not float mid-board with both ends showing.
    sx, sy, page_w, page_h = 4241.8 / 3175, 736.6 / 1219.2, 4241.8, 736.6
    ox, oy, ow, oh = -40.0, 150.0, 1450.0, 700.0
    table = _Shape(ox * sx, oy * sy, ow * sx, oh * sy)
    clip = _Shape(0, 0, page_w, page_h, kids=[table])
    assert CorelEngine._undistort_clip_contents(clip, sx, sy, page_w, page_h, []) == 1
    k = min(sx, sy)
    assert table.LeftX == pytest.approx(ox * k)                      # still bleeding off the left edge
    assert table.SizeWidth == pytest.approx(ow * k) and table.SizeHeight == pytest.approx(oh * k)


def test_clip_child_on_the_right_edge_stays_on_it():
    sx, sy, page_w, page_h = 2.0, 1.0, 2000.0, 500.0
    ow, oh = 300.0, 200.0
    ox = 1000.0 - ow + 10.0                                           # 10 mm past the master's right edge (1000 wide)
    pack = _Shape(ox * sx, 50.0, ow * sx, oh * sy)
    clip = _Shape(0, 0, page_w, page_h, kids=[pack])
    CorelEngine._undistort_clip_contents(clip, sx, sy, page_w, page_h, [])
    assert pack.LeftX + pack.SizeWidth == pytest.approx(page_w + 10.0)  # same 10 mm bleed at k = 1
