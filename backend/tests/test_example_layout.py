"""Brand-agnostic example-library layout (app/example_layout.py) on small synthetic dumps - no CorelDRAW, no real dataset."""
import json

import pytest

from app import example_layout as X
from app.layout import Obj


def shape(type_, x, y, w, h, gp=None, **kw):
    return {"type": type_, "name": "", "x": x, "y": y, "w": w, "h": h, "group_path": gp or [], **kw}


def dump(W, H, els, texts=(), clip=()):
    """els: list of (type, x, y, w, h[, n_children]) top-level art; texts: (text, x, y, w, h); clip: PowerClip children."""
    sh = [shape("rectangle", 0, 0, W, H)]                       # the page-sized background that holds the clip
    for t in texts:
        sh.append(shape("text", t[1], t[2], t[3], t[4], text=t[0]))
    for e in els:
        sh.append(shape(e[0], *e[1:5]))
        for _ in range(e[5] if len(e) > 5 else 0):
            sh.append(shape("curve", e[1], e[2], 1, 1, gp=["g"]))
    for c in clip:
        sh.append(shape(c[0], *c[1:5], gp=["rect<clip>"], clip=True))
    return {"file": "x.cdr", "page_mm": {"w": W, "h": H}, "shapes": sh}


def master_dump():
    return dump(3000, 1000,
                [("group", 100, 600, 600, 300, 4),      # badge  2:1
                 ("group", 1500, 300, 900, 600, 6),     # logo   1.5:1
                 ("bitmap", 2700, 100, 150, 700)],      # product
                texts=[("MASTER SHOP", 100, 50, 1000, 100), ("மாஸ்டர்", 1500, 50, 800, 90)],
                clip=[("bitmap", -100, -50, 3200, 1100), ("group", -50, -300, 3100, 500),
                      ("group", 100, 150, 1200, 700)])


def board(W, H, fx):
    """A designer board: same pictures, boxes given as page fractions (cx, cy, w, h)."""
    def box(f, aspect_src=None):
        cx, cy, w, h = f
        return (cx * W - w * W / 2, cy * H - h * H / 2, w * W, h * H)
    return dump(W, H,
                [("group", *box(fx["badge"]), 4), ("group", *box(fx["logo"]), 6), ("bitmap", *box(fx["prod"]))],
                texts=[("NEW SHOP", *box(fx["en"])), ("நியூ", *box(fx["ta"]))],
                clip=[("bitmap", -0.02 * W, -0.05 * H, 1.04 * W, 1.1 * H), ("group", -0.02 * W, -0.3 * H, 1.04 * W, 0.5 * H),
                      ("group", *box(fx["comp"]))])


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    m = master_dump()
    master = X.describe_master(m, {1, 2})
    master["id"], master["file"] = "m0", "master.cdr"
    boards = []
    styles = {
        3000: dict(badge=(0.1, 0.8, 0.2, 0.3), logo=(0.6, 0.6, 0.3, 0.6), prod=(0.93, 0.45, 0.05, 0.7), en=(0.2, 0.08, 0.3, 0.1), ta=(0.7, 0.08, 0.25, 0.09), comp=(0.2, 0.5, 0.4, 0.7)),
        2000: dict(badge=(0.15, 0.82, 0.3, 0.3), logo=(0.55, 0.6, 0.45, 0.6), prod=(0.9, 0.45, 0.07, 0.7), en=(0.2, 0.08, 0.35, 0.1), ta=(0.7, 0.08, 0.25, 0.09), comp=(0.25, 0.5, 0.5, 0.7)),
    }
    for W, fx in styles.items():
        d = board(W, 1000, fx)
        rec = X.board_record(master, d, "NEW SHOP", f"b{W}.cdr")
        rec["master"] = "m0"
        boards.append(rec)
    library = {"brand": "t", "masters": [master], "boards": boards}
    X.library.cache_clear()
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "library.json").write_text(json.dumps(library), encoding="utf-8")
    monkeypatch.setattr(X, "DATA", tmp_path)
    return library


def objs_of(d):
    return [Obj(str(i), "", s["kind"], s["x"], s["y"], s["w"], s["h"], s.get("text"), None, s["n_desc"])
            for i, s in enumerate(X.top_level(d))]


def test_describe_master_lists_art_not_text_or_background():
    m = X.describe_master(master_dump(), {1, 2})
    assert [e["kind"] for e in m["elements"]] == ["group", "group", "bitmap"]
    assert m["composite_aspect"] == pytest.approx(1200 / 700, rel=1e-3)


def test_board_record_matches_by_signature(lib):
    b = lib["boards"][0]
    assert set(b["els"]) == {"e0", "e1", "e2"} and not any(e["loose"] for e in b["els"].values())
    assert b["clip"]["composite"]["cx"] == pytest.approx(0.2, abs=1e-3)
    assert set(b["texts"]) == {"name_en", "name_ta"}


def test_board_with_too_few_matching_pictures_is_not_built_from_the_master():
    m = X.describe_master(master_dump(), {1, 2})
    other = dump(3000, 1000, [("group", 0, 0, 100, 100, 9)])
    assert X.board_record(m, other, "S", "o.cdr") is None


def test_plan_exact_size_copies_the_designer_boxes(lib):
    d = master_dump()
    pl = X.plan("t", objs_of(d), 3000, 1000, 3000, 1000, name_ids={"1", "2"})
    assert pl["template"]["exact"] and pl["template"]["file"] == "b3000.cdr"
    by_key = {v[4]: v for v in pl["boxes"].values()}
    x, y, w, h, _ = by_key["e0"]
    assert (x + w / 2, y + h / 2) == pytest.approx((300, 800), abs=1) and (w, h) == pytest.approx((600, 300), abs=1)
    assert pl["clip"]["composite"][0] + pl["clip"]["composite"][2] / 2 == pytest.approx(600, abs=1)


def test_plan_picks_the_nearest_board_and_exclude_hides_it(lib):
    objs = objs_of(master_dump())
    near = X.plan("t", objs, 3000, 1000, 2100, 1000, name_ids={"1", "2"})
    assert near["template"]["file"] == "b2000.cdr"
    far = X.plan("t", objs, 3000, 1000, 2100, 1000, name_ids={"1", "2"}, exclude=["b2000.cdr"])
    assert far["template"]["file"] == "b3000.cdr"


def test_plan_keeps_master_proportions_for_a_new_aspect(lib):
    objs = objs_of(master_dump())
    pl = X.plan("t", objs, 3000, 1000, 2400, 1000, name_ids={"1", "2"})
    for oid, (x, y, w, h, k) in pl["boxes"].items():
        o = objs[int(oid)]
        assert w / h == pytest.approx(o.w / o.h, rel=1e-6)


def test_plan_is_none_without_a_library_or_a_matching_master(lib):
    # a master is recognised by its shapes, not by the brand label it was uploaded under
    assert X.plan("some other label", objs_of(master_dump()), 3000, 1000, 3000, 1000)["template"]["exact"]
    odd = [Obj("0", "", "group", 0, 0, 100, 100, None, None, 99)]
    assert X.plan("t", odd, 3000, 1000, 3000, 1000) is None


def test_text_spec_anchors(lib):
    spec = X.plan("t", objs_of(master_dump()), 3000, 1000, 3000, 1000, name_ids={"1", "2"})["texts"]
    assert spec["name_en"]["anchor"] == "left" and spec["name_ta"]["anchor"] == "right"
    assert spec["name_en"]["h"] == pytest.approx(100, abs=1)


def test_replaced_bitmap_makes_a_board_incompatible(lib):
    master = lib["masters"][0]
    b = json.loads(json.dumps(lib["boards"][0]))
    assert X.compatible(b, master)
    b["els"]["e2"]["loose"] = True                     # the designer swapped the product picture for another one
    assert not X.compatible(b, master)


def test_loose_fragments_cluster_into_one_picture():
    frags = [("curve", 100 + i * 30, 100, 25, 25) for i in range(5)] + [("curve", 2500, 800, 40, 40)]
    d = dump(3000, 1000, frags)
    units = X.dump_units(d)
    assert sorted((u.kind, len(u.members)) for u in units) == [("cluster", 5), ("shape", 1)]


def test_cluster_members_keep_their_place_in_the_moved_picture(tmp_path, monkeypatch):
    X.library.cache_clear()
    frags = [("curve", 100 + i * 30, 100, 25, 25) for i in range(5)]
    m = dump(3000, 1000, frags)
    master = X.describe_master(m)
    master["id"], master["file"] = "m0", "m.cdr"
    designer = dump(3000, 1000, [("curve", 1000 + i * 30, 500, 25, 25) for i in range(5)])
    rec = X.board_record(master, designer, None, "d.cdr")
    rec["master"] = "m0"
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "library.json").write_text(json.dumps({"brand": "c", "masters": [master], "boards": [rec]}), encoding="utf-8")
    monkeypatch.setattr(X, "DATA", tmp_path)
    objs = [Obj(str(i), "", "shape", s["x"], s["y"], s["w"], s["h"], None, None, 0) for i, s in enumerate(X.top_level(m))]
    pl = X.plan("c", objs, 3000, 1000, 3000, 1000)
    xs = sorted(v[0] for v in pl["boxes"].values())
    assert xs == pytest.approx([1000 + i * 30 for i in range(5)], abs=0.5)
    assert all(v[1] == pytest.approx(500, abs=0.5) for v in pl["boxes"].values())


def test_preferred_master_is_the_one_the_nearest_board_was_made_from(lib, tmp_path, monkeypatch):
    """Two design styles (two masters) of one brand: the target size is routed to the style whose board is nearest."""
    import copy
    other = copy.deepcopy(lib["masters"][0]); other["id"], other["file"] = "m1", "style_b.cdr"
    b = copy.deepcopy(lib["boards"][1]); b["master"], b["file"], b["W"], b["H"] = "m1", "style_b_board.cdr", 1500.0, 500.0
    lib2 = {"brand": "t", "masters": [lib["masters"][0], other], "boards": [lib["boards"][0], lib["boards"][1], b]}
    (tmp_path / "t" / "library.json").write_text(json.dumps(lib2), encoding="utf-8")
    X.library.cache_clear()
    assert X.preferred_master_file("t", 3000, 1000) == "master.cdr"         # the 3000 x 1000 board of master m0
    assert X.preferred_master_file("t", 1500, 500) == "style_b.cdr"         # the 1500 x 500 board of master m1
    assert X.preferred_master_file("nobrand", 1000, 1000) is None


def test_repeated_copies_are_recorded_and_planned_as_duplicates(tmp_path, monkeypatch):
    """A wide designer board shows the logo twice: the second one is a COPY of the master element (a `_tile1` duplicate), not an extra."""
    X.library.cache_clear()
    m = master_dump()
    master = X.describe_master(m, {1, 2})
    master["id"], master["file"] = "m0", "m.cdr"
    wide = dump(6000, 1000,
                [("group", 200, 600, 600, 300, 4), ("group", 3000, 300, 900, 600, 6), ("group", 4600, 300, 900, 600, 6),   # logo twice
                 ("bitmap", 5700, 100, 150, 700)],
                texts=[("NEW SHOP", 100, 50, 1000, 100), ("நியூ", 3000, 50, 800, 90)],
                clip=[("bitmap", -100, -50, 6200, 1100), ("group", -50, -300, 6100, 500), ("group", 100, 150, 1200, 700)])
    rec = X.board_record(master, wide, "NEW SHOP", "wide.cdr")
    rec["master"] = "m0"
    assert rec["extra"] == 0 and list(rec["copies"]) == ["e1"] and len(rec["copies"]["e1"]) == 1
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "library.json").write_text(json.dumps({"brand": "t", "masters": [master], "boards": [rec]}), encoding="utf-8")
    monkeypatch.setattr(X, "DATA", tmp_path)
    objs = objs_of(m)
    pl = X.plan("t", objs, 3000, 1000, 6000, 1000, name_ids={"1", "2"})
    tiles = {k: v for k, v in pl["boxes"].items() if "_tile" in k}
    assert list(tiles) == ["4_tile1"] and tiles["4_tile1"][4] == "e1"
    x, y, w, h, _ = tiles["4_tile1"]
    assert (x + w / 2, y + h / 2) == pytest.approx((5050, 600), abs=1)               # the copy's own box on the designer's board
    from app.layout import compute_layout
    placed = compute_layout(objs, 3000, 1000, 6000, 1000, shopname_ids={"1", "2"},
                            brand_rule={"brand": "t", "example_library": True}, shop_name="X", shop_name_local="Y")
    assert any(p.id == "4_tile1" for p in placed)                                    # CorelEngine duplicates shape 4 for it
