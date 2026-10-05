"""Board type, uploaded-master routing and name-line safeguards of the example library (app/example_layout.py, app/main.py)."""
import pytest
from app import example_layout as X


def _b(file, W, H, master="m1", texts=None):
    return {"file": file, "W": W, "H": H, "els": {"e0": {"loose": False}}, "texts": texts or {}, "master": master,
            "clip": {}, "copies": {}, "extra": 0}


def _lib(boards, masters=None):
    return {"masters": masters or [{"id": "m1", "file": "6 X 3.cdr", "page": [1828.8, 914.4], "elements": [], "clip_bitmaps": []}],
            "boards": boards}


def test_norm_type_and_board_kind():
    assert X.norm_type("Double Side GSB") == "doublesidegsb"
    assert X.norm_type("Frotlit") == X.norm_type("Frontlit") == "frontlit"
    assert X.norm_type(None) == ""
    assert X.board_kind("14 - 8 X 4 Feet - GSB - New Sri.cdr") == "gsb"
    assert X.board_kind("6 X 3.cdr") == ""


def test_board_type_only_separates_boards_of_the_same_size(monkeypatch):
    monkeypatch.setattr(X, "compatible", lambda b, m: True)
    ds = _b("08 - 8 X 4 Feet - Double Side GSB - A.cdr", 2438.4, 1219.2)
    gsb = _b("14 - 8 X 4 Feet - GSB - B.cdr", 2438.4, 1219.2)
    lib = _lib([ds, gsb])
    m = lib["masters"][0]
    assert X.select_board(lib, m, 2438.4, 1219.2, None, "GSB") is gsb
    assert X.select_board(lib, m, 2438.4, 1219.2, None, "Double Side GSB") is ds
    first = X.select_board(lib, m, 2438.4, 1219.2)                    # no type = the old choice
    assert first is X.select_board(lib, m, 2438.4, 1219.2, None, "")
    # a nearer board of another type still wins over a farther board of the right type
    far = _b("20 - 20 X 4 Feet - GSB - C.cdr", 6096, 1219.2)
    lib2 = _lib([ds, far])
    assert X.select_board(lib2, lib2["masters"][0], 2438.4, 1219.2, None, "GSB") is ds


def test_preferred_master_uses_other_brand_libraries_and_only_uploaded_files(monkeypatch):
    monkeypatch.setattr(X, "compatible", lambda b, m: True)
    lib = _lib([_b("14 - 8 X 4 Feet - GSB - B.cdr", 2438.4, 1219.2)])
    monkeypatch.setattr(X, "candidate_libraries", lambda brand: [lib])
    monkeypatch.setattr(X, "library", lambda brand: None)             # the label the masters were uploaded under has no library
    assert X.preferred_master_file("adinn", 2438.4, 1219.2) is None   # old call: the brand's own library only
    assert X.preferred_master_file("adinn", 2438.4, 1219.2, available={"6 X 3.cdr"}) == "6 X 3.cdr"
    assert X.preferred_master_file("adinn", 2438.4, 1219.2, available={"other.cdr"}) is None


def test_a_master_drawn_at_the_targets_shape_is_the_design_for_it(monkeypatch):
    monkeypatch.setattr(X, "compatible", lambda b, m: True)
    masters = [{"id": "m0", "file": "10 X 3.cdr", "page": [3048, 914.4], "elements": [], "clip_bitmaps": []},
               {"id": "m1", "file": "6 X 3.cdr", "page": [1828.8, 914.4], "elements": [], "clip_bitmaps": []}]
    lib = _lib([_b("04 - 10 X 3 Feet - Nonlit - A.cdr", 3048, 914.4, "m1"), _b("23 - 10 X 3 Feet - Frontlit - B.cdr", 3048, 914.4, "m0"),
                _b("20 - 8 X 3 Feet - GSB - C.cdr", 2438.4, 914.4, "m1")], masters)
    lib["wide_boards_use_widest_master"] = True
    monkeypatch.setattr(X, "candidate_libraries", lambda brand: [lib])
    have = {"10 X 3.cdr", "6 X 3.cdr"}
    assert X.preferred_master_file("x", 3048, 914.4, available=have) == "10 X 3.cdr"        # 10x3 ft -> the 10 X 3 master
    assert X.preferred_master_file("x", 2438.4, 914.4, available=have) == "6 X 3.cdr"       # 8x3 ft: no master of that shape -> nearest board
    assert X.preferred_master_file("x", 3048, 914.4, available={"6 X 3.cdr"}) == "6 X 3.cdr"
    del lib["wide_boards_use_widest_master"]                                                # a library without the flag: nearest board decides
    assert X.preferred_master_file("x", 3048, 914.4, available=have) == "6 X 3.cdr"


def test_name_text_match_is_exact_or_mostly_the_same_words():
    assert X.is_name_text("Sri thanga Vilas soda factory", "SRI THANGAVILAS SODA FACTORY")
    assert X.is_name_text("New Sri happy Iyengar bakery", "NEW HAPPY IYENGAR BAKERY & SWEETS")
    assert X.is_name_text("Lakshmi Store", "LAKSHMI STORE")
    assert not X.is_name_text("Lakshmi Store", "ADINN/06/26")
    assert not X.is_name_text("Lakshmi Store", "ICE CREAM")
    assert not X.is_name_text("Lakshmi Store", "லட்சுமி ஸ்டோர்")
    assert not X.is_name_text(None, "ANYTHING HERE")


def test_overlapping_side_by_side_name_lines_are_separated():
    texts = {"name_ta": {"anchor": "center", "ax": 1000.0, "w_cap": 2600.0, "h": 100, "cy": 150},
             "name_en": {"anchor": "right", "ax": 2970.0, "w_cap": 700.0, "h": 100, "cy": 150}}
    board = {"texts": {"name_ta": {"x": 0.03, "w": 0.86, "cy": 0.13, "h": 0.09}, "name_en": {"x": 0.74, "w": 0.23, "cy": 0.13, "h": 0.16}}}
    X._separate_name_lines(texts, board, 3048.0)
    ta = texts["name_ta"]
    assert ta["anchor"] == "left" and abs(ta["ax"] - 0.03 * 3048) < 1e-6
    assert ta["w_cap"] <= (0.74 - 0.03) * 3048 and texts["name_en"]["w_cap"] == 700.0        # only the left line moved
    # lines on different rows are untouched
    t2 = {"name_ta": dict(texts["name_ta"], anchor="center"), "name_en": dict(texts["name_en"])}
    b2 = {"texts": {"name_ta": {"x": 0.1, "w": 0.8, "cy": 0.2, "h": 0.1}, "name_en": {"x": 0.1, "w": 0.8, "cy": 0.05, "h": 0.06}}}
    X._separate_name_lines(t2, b2, 3048.0)
    assert t2["name_ta"]["anchor"] == "center"


def test_a_name_line_is_held_inside_the_page():
    from app.engines import CorelEngine
    spec = {"page_w": 1000.0}
    assert CorelEngine._inside_page(500, 700, spec) == 480.0          # would run off the right edge -> pulled back (2 % margin)
    assert CorelEngine._inside_page(500, -50, spec) == 20.0
    assert CorelEngine._inside_page(500, 250, spec) == 250
    assert CorelEngine._inside_page(990, 5, spec) == 5.0              # wider than the page: centred
    assert CorelEngine._inside_page(500, 700, {}) == 700              # specs of an older library: untouched


def test_name_spec_width_is_capped_to_the_page(monkeypatch):
    lib = _lib([_b("09 - 6 X 3 Feet - Double Side GSB - A.cdr", 1828.8, 914.4, texts={
        "name_ta": {"cx": 0.6, "cy": 0.14, "w": 1.139, "h": 0.08, "x": 0.03, "lines": 1}})])
    b = lib["boards"][0]
    spec = X._text_spec(lib, lib["masters"][0], b, "name_ta", 1828.8, 914.4)
    assert spec["w_cap"] <= X.PAGE_FIT * 1828.8 and spec["page_w"] == 1828.8


def test_style_master_routing_recognises_masters_uploaded_under_another_brand(monkeypatch):
    from app import db, main
    lib = _lib([_b("14 - 8 X 4 Feet - GSB - B.cdr", 2438.4, 1219.2)])
    monkeypatch.setattr(X, "compatible", lambda b, m: True)
    monkeypatch.setattr(X, "candidate_libraries", lambda brand: [lib])
    monkeypatch.setattr(X, "library", lambda brand: None)
    ms = [{"id": "a", "master_filename": "10 X 3.cdr", "deleted_at": None}, {"id": "b", "master_filename": "6 x 3.CDR", "deleted_at": None}]
    monkeypatch.setattr(db, "list_masters", lambda brand=None, orientation=None: ms)
    r = main._example_style_master("Adinn", "landscape", 2438.4, 1219.2, "GSB")
    assert r and r["id"] == "b"                                       # file names compare case-insensitively
    monkeypatch.setattr(db, "list_masters", lambda brand=None, orientation=None: ms[:1])
    assert main._example_style_master("Adinn", "landscape", 2438.4, 1219.2) is None     # a single master is the default anyway


def test_a_library_can_route_boards_wider_than_a_master_to_the_widest_master(monkeypatch):
    monkeypatch.setattr(X, "compatible", lambda b, m: True)
    masters = [{"id": "m0", "file": "10 X 3.cdr", "page": [3048, 914.4], "elements": [], "clip_bitmaps": []},
               {"id": "m1", "file": "6 X 3.cdr", "page": [1828.8, 914.4], "elements": [], "clip_bitmaps": []}]
    boards = [_b("04 - 10 X 3 Feet - Nonlit - A.cdr", 3048, 914.4, "m0"), _b("22 - 20 X 3 Feet - Frontlit - B.cdr", 6096, 914.4, "m1"),
              _b("20 - 8 X 3 Feet - GSB - C.cdr", 2438.4, 914.4, "m1"), _b("18 - 10 X 4 Feet - GSB - D.cdr", 3048, 1219.2, "m1")]
    lib = _lib(boards, masters)
    monkeypatch.setattr(X, "candidate_libraries", lambda brand: [lib])
    have = {"10 X 3.cdr", "6 X 3.cdr"}
    assert X.preferred_master_file("x", 6096, 914.4, available=have) == "6 X 3.cdr"          # no flag: the board's own master, as before
    lib["wide_boards_use_widest_master"] = True
    assert X.preferred_master_file("x", 6096, 914.4, available=have) == "10 X 3.cdr"         # 20x3 ft: the 10 X 3 design stretched
    assert X.preferred_master_file("x", 2743.2, 914.4, available=have) == "10 X 3.cdr"       # 9x3 ft is within 12 % of 10x3
    assert X.preferred_master_file("x", 2438.4, 914.4, available=have) == "6 X 3.cdr"        # 8x3 ft stays on the 6 X 3 design
    assert X.preferred_master_file("x", 3048, 1219.2, available=have) == "6 X 3.cdr"         # 10x4 ft (2.5) too
    assert X.preferred_master_file("x", 6096, 914.4, available={"6 X 3.cdr"}) == "6 X 3.cdr"


# --- name block of a wide board comes from the nearest designer board with a name (any master) -------------------------------------

def _name(cx, cy, w, h, lines=1):
    return {"cx": cx, "cy": cy, "w": w, "h": h, "x": cx - w / 2, "lines": lines}


def _wide_lib(**flags):
    masters = [{"id": "m0", "file": "10 X 3.cdr", "page": [3048, 914.4], "elements": [], "clip_bitmaps": []},
               {"id": "m1", "file": "6 X 3.cdr", "page": [1828.8, 914.4], "elements": [], "clip_bitmaps": []}]
    near10 = _b("23 - 10 X 3 Feet - Frontlit - A.cdr", 3048, 914.4, "m0", {"name_en": _name(0.5, 0.25, 0.18, 0.26, 2)})
    wide20 = _b("25 - 20 X 3 Feet - Frontlit - B.cdr", 6096, 914.4, "m1", {"name_en": _name(0.5, 0.2, 0.40, 0.30)})
    return {**_lib([near10, wide20], masters), **flags}, masters[0], near10, wide20


def _plan(monkeypatch, lib, master, board, new_w):
    monkeypatch.setattr(X, "candidate_libraries", lambda brand: [lib])
    monkeypatch.setattr(X, "find_master", lambda *a, **k: (master, {}))
    monkeypatch.setattr(X, "select_board", lambda *a, **k: board)
    return X.plan("x", [], 3048, 914.4, new_w, 914.4)


def test_text_board_is_the_nearest_board_with_a_name_of_any_master():
    lib, _m, near10, wide20 = _wide_lib()
    assert X.select_text_board(lib, 6096, 914.4) is wide20
    assert X.select_text_board(lib, 3048, 914.4) is near10
    assert X.select_text_board(lib, 6096, 914.4, exclude=wide20["file"]) is near10
    lib["boards"] = [_b("x - 20 X 3 Feet - GSB - C.cdr", 6096, 914.4, "m1")]            # no name text anywhere
    assert X.select_text_board(lib, 6096, 914.4) is None


def test_wide_target_takes_its_name_block_from_the_wide_board_when_the_library_says_so(monkeypatch):
    lib, m0, near10, wide20 = _wide_lib(wide_boards_use_widest_master=True)
    p = _plan(monkeypatch, lib, m0, near10, 6096)
    assert round(p["texts"]["name_en"]["w_cap"]) == round(0.40 * 6096)                  # the 20 ft board's, not 0.18 of the page
    assert p["texts"]["name_en"]["cy"] == 0.2 * 914.4
    lib.pop("wide_boards_use_widest_master")
    p = _plan(monkeypatch, lib, m0, near10, 6096)                                       # a library without the flag: unchanged
    assert round(p["texts"]["name_en"]["w_cap"]) == round(0.18 * 6096)


def test_a_masters_name_width_cap_applies_to_all_its_boards_and_only_those(monkeypatch):
    lib, m0, near10, wide20 = _wide_lib(wide_boards_use_widest_master=True)
    m0["name_max_page_frac"] = 0.12
    p = _plan(monkeypatch, lib, m0, near10, 6096)
    assert round(p["texts"]["name_en"]["w_cap"]) == round(0.12 * 6096)                  # the wide board's 0.40 would run past the panel
    same = _plan(monkeypatch, lib, m0, near10, 3048)                                    # the master's own size: the cap holds there too
    assert round(same["texts"]["name_en"]["w_cap"]) == round(0.12 * 3048)               # (the designer's 0.18 is wider than the panel)
    del m0["name_max_page_frac"]                                                        # a master without the key: the designer's width
    assert round(_plan(monkeypatch, lib, m0, near10, 3048)["texts"]["name_en"]["w_cap"]) == round(0.18 * 3048)


def test_nested_names_flag_is_passed_through_the_plan(monkeypatch):
    lib, m0, near10, _w = _wide_lib()
    assert _plan(monkeypatch, lib, m0, near10, 6096)["nested_names"] is False
    lib["nested_names_by_spec"] = True
    assert _plan(monkeypatch, lib, m0, near10, 6096)["nested_names"] is True


def test_a_name_line_without_a_box_of_its_own_does_not_borrow_the_other_lines_box(monkeypatch):
    from app import layout
    spec = {"anchor": "left", "ax": 2000.0, "cy": 300.0, "h": 60.0, "w_cap": 900.0, "page_w": 4000.0}
    plan = {"boxes": {}, "clip": {}, "texts": {"name_ta": spec}, "board_scripts": ["name_ta"], "template": {"file": "x", "W": 4000, "H": 600}}
    monkeypatch.setattr(X, "plan", lambda *a, **k: plan)
    en = layout.Obj("0", "", "text", 100, 80, 1300, 50, "OLD NAME", None, 0)
    ta = layout.Obj("1", "", "text", 1400, 90, 1400, 36, "ஸ்ரீ கன்ன", None, 0)
    rule = {"example_library": True, "brand": "x"}
    placed = layout.compute_layout([en, ta], 3000, 1200, 4000, 600, brand_rule=rule, shopname_ids={"0", "1"}, shop_name="NEW NAME",
                                   shop_name_local="புதிய")
    by = {p.id: p for p in placed}
    assert by["1"].text_fit is spec or by["1"].text_fit == spec          # the Tamil line has its box
    assert by["0"].text_fit is None                                      # the English line is placed by the ordinary rule
    # a master with ONE name line still takes the other script's box
    placed = layout.compute_layout([en], 3000, 1200, 4000, 600, brand_rule=rule, shopname_ids={"0"}, shop_name="NEW NAME",
                                   shop_name_local="புதிய")
    assert placed[0].text_fit is not None


def test_library_name_case_upper_sets_the_english_name_in_capitals(monkeypatch):
    from app import layout
    spec = {"anchor": "center", "ax": 2000.0, "cy": 300.0, "h": 60.0, "w_cap": 900.0, "page_w": 4000.0}
    plan = {"boxes": {}, "clip": {}, "texts": {"name_en": spec}, "board_scripts": ["name_en"], "template": {"file": "x", "W": 4000, "H": 600},
            "name_case": "upper", "nested_names": True}
    monkeypatch.setattr(X, "plan", lambda *a, **k: plan)
    en = layout.Obj("0", "", "text", 100, 80, 1300, 50, "Old name", None, 0)
    placed = layout.compute_layout([en], 3000, 1200, 4000, 600, brand_rule={"example_library": True, "brand": "x"}, shopname_ids={"0"},
                                   shop_name="Kanish Cool Drink's")
    assert placed[0].text == "KANISH COOL DRINK'S"
    assert placed[0].name_case == "upper"
    plan["name_case"] = None
    assert layout.compute_layout([en], 3000, 1200, 4000, 600, brand_rule={"example_library": True, "brand": "x"}, shopname_ids={"0"},
                                 shop_name="Kanish Cool Drink's")[0].text == "Kanish Cool Drink's"


def test_the_panel_follows_the_nearest_board_the_pictures_on_its_edge_move_and_the_names_fill_it(monkeypatch):
    lib, m0, near10, wide20 = _wide_lib(wide_boards_use_widest_master=True)
    m0["clip_bitmaps"] = [{"k": "b0", "aspect": 1.0, "area": 0.05, "backdrop": False}]
    near10["clip"] = {"panel": {"cx": 0.5, "cy": 0.5, "w": 0.333, "h": 1.0, "x": 0.3335}}
    near10["cbm"] = {"b0": {"cx": 0.35, "cy": 0.2, "w": 0.1, "h": 0.3, "bd": False}}          # the left picture sits on the panel's left edge
    wide20["clip"] = {"panel": {"cx": 0.5, "cy": 0.5, "w": 0.366, "h": 1.0, "x": 0.317}}
    p = _plan(monkeypatch, lib, m0, near10, 6096)
    x, y, w, h = p["clip"]["panel"]
    assert (round(x), round(w), round(h, 1)) == (round(0.317 * 6096), round(0.366 * 6096), 914.4)
    px = p["clip"]["bitmaps"]["b0"][0]
    assert px == pytest.approx(0.35 * 6096 - 0.1 * 6096 / 2 + (0.317 - 0.3335) * 6096)       # moved with the panel's left edge
    assert round(p["texts"]["name_en"]["w_cap"]) == round(X.PANEL_EN_FIT * 0.366 * 6096)       # names fill the panel of THIS size
    near10["clip"]["panel"]["w"] = 0.333
    same = _plan(monkeypatch, lib, m0, near10, 3048)                                           # the master's own size: its own panel
    assert round(same["texts"]["name_ta"]["w_cap"] if "name_ta" in same["texts"] else 0.92 * 0.333 * 3048) == round(0.92 * 0.333 * 3048)
    assert round(same["clip"]["panel"][2]) == round(0.333 * 3048)
    near10["clip"] = {}                                                                         # no recorded panel: nothing planned
    assert "panel" not in _plan(monkeypatch, lib, m0, near10, 6096)["clip"]


def test_panel_boards_of_one_height_share_the_biggest_name_block(monkeypatch):
    lib, m0, near10, wide20 = _wide_lib(wide_boards_use_widest_master=True)
    m0["clip_bitmaps"] = []
    for b in (near10, wide20):
        b["clip"] = {"panel": {"cx": 0.5, "cy": 0.5, "w": 0.366, "h": 1.0, "x": 0.317}}
    wide20["texts"] = {"name_en": _name(0.5, 0.2, 0.20, 0.15)}                             # this board's name was short and one line
    other = _b("08 - 26 X 3 Feet - Frontlit - C.cdr", 7924.8, 914.4, "m1", {"name_en": _name(0.5, 0.1, 0.19, 0.40, 3)})
    other["clip"] = {"panel": {"cx": 0.5, "cy": 0.5, "w": 0.27, "h": 1.0, "x": 0.365}}
    lib["boards"].append(other)
    p = _plan(monkeypatch, lib, m0, near10, 6096)
    en = p["texts"]["name_en"]
    assert round(en["h"] / 914.4, 2) == 0.36 and en["max_lines"] == 3 and en["mode"] == "block"   # the biggest block of that height, kept on the page
    assert round(en["w_cap"]) == round(X.PANEL_EN_FIT * 0.366 * 6096)                               # the panel limits the width
    assert en["cy"] == pytest.approx(0.36 * 914.4 / 2 + X.BLOCK_EDGE_FRAC * 914.4)                  # the nearest board's 0.2 would clip the tall block: raised clear of the edge


def test_left_name_line_is_limited_to_the_room_before_the_right_one_even_without_overlap():
    texts = {"name_ta": {"anchor": "left", "ax": 51.0, "w_cap": 1672.0, "h": 100, "cy": 120, "page_w": 2438.4},
             "name_en": {"anchor": "right", "ax": 2385.0, "w_cap": 677.0, "h": 100, "cy": 120}}
    board = {"texts": {"name_ta": {"x": 0.02, "w": 0.63, "cy": 0.13, "h": 0.2}, "name_en": {"x": 0.70, "w": 0.28, "cy": 0.13, "h": 0.2}}}
    X._separate_name_lines(texts, board, 2438.4)
    assert texts["name_ta"]["w_cap"] == pytest.approx(min(0.70 * 2438.4, 2385.0 - 677.0) - 0.03 * 2438.4 - 51.0)   # ends before the English line starts (its planned width)
    assert texts["name_ta"]["ax"] == 51.0 and texts["name_ta"]["anchor"] == "left"                    # position untouched
    assert texts["name_en"]["w_cap"] == 677.0
    texts2 = {"name_ta": {"anchor": "center", "ax": 1500.0, "w_cap": 700.0, "h": 100, "cy": 600}, "name_en": {"anchor": "center", "ax": 1500.0, "w_cap": 500.0, "h": 100, "cy": 200}}
    board2 = {"texts": {"name_ta": {"x": 0.3, "w": 0.4, "cy": 0.7, "h": 0.3}, "name_en": {"x": 0.4, "w": 0.2, "cy": 0.2, "h": 0.2}}}
    X._separate_name_lines(texts2, board2, 3000.0)                                                     # stacked, not side by side: untouched
    assert texts2["name_ta"]["w_cap"] == 700.0


def test_stacked_name_blocks_stay_on_the_page_and_apart():
    texts = {"name_ta": {"h": 600.0, "cy": 649.0, "w_cap": 1, "anchor": "center", "ax": 0}, "name_en": {"h": 360.0, "cy": 200.0, "w_cap": 1, "anchor": "center", "ax": 0}}
    X._fit_blocks_in_panel(texts, 914.4)
    ta, en = texts["name_ta"], texts["name_en"]
    assert ta["cy"] + ta["h"] / 2 <= 914.4 and en["cy"] - en["h"] / 2 >= 0                              # on the page
    assert ta["cy"] - ta["h"] / 2 - (en["cy"] + en["h"] / 2) >= 0.059 * 914.4                           # a gap between the two blocks


def test_name_blocks_keep_clear_of_the_board_edges():
    texts = {"name_en": {"h": 300.0, "cy": 120.0, "w_cap": 1, "anchor": "center", "ax": 0}, "name_ta": {"h": 300.0, "cy": 820.0, "w_cap": 1, "anchor": "center", "ax": 0}}
    X._fit_blocks_in_panel(texts, 914.4)
    for t in texts.values():
        assert t["cy"] - t["h"] / 2 >= 0.039 * 914.4 and t["cy"] + t["h"] / 2 <= 914.4 - 0.039 * 914.4


def test_left_name_line_ends_before_the_right_one_even_when_that_one_is_wider_than_on_the_designers_board():
    texts = {"name_ta": {"anchor": "left", "ax": 51.0, "w_cap": 1672.0, "h": 100, "cy": 120, "page_w": 2438.4},
             "name_en": {"anchor": "right", "ax": 2385.0, "w_cap": 677.0, "h": 100, "cy": 120}}
    board = {"texts": {"name_ta": {"x": 0.021, "w": 0.778, "cy": 0.13, "h": 0.07}, "name_en": {"x": 0.719, "w": 0.261, "cy": 0.13, "h": 0.15}}}   # her boxes overlap
    X._separate_name_lines(texts, board, 2438.4)
    assert 51.0 + texts["name_ta"]["w_cap"] <= 2385.0 - 677.0 - 0.029 * 2438.4


def test_panel_english_name_keeps_the_designers_line_count_and_a_narrower_cap(monkeypatch):
    lib, m0, near10, wide20 = _wide_lib(wide_boards_use_widest_master=True)
    m0["clip_bitmaps"] = []
    for b in (near10, wide20):
        b["clip"] = {"panel": {"cx": 0.5, "cy": 0.5, "w": 0.366, "h": 1.0, "x": 0.317}}
    wide20["texts"] = {"name_en": _name(0.5, 0.2, 0.26, 0.30, 2)}
    en = _plan(monkeypatch, lib, m0, near10, 6096)["texts"]["name_en"]
    assert en["pref_lines"] == 2 and X.PANEL_EN_FIT == 0.66
    assert round(en["w_cap"]) == round(X.PANEL_EN_FIT * 0.366 * 6096)


def test_apply_text_fit_never_uses_fewer_lines_than_the_designer_for_the_english_name():
    from app.engines import CorelEngine
    assert CorelEngine._balanced_lines(["KARTHIKEYAN", "STORE"], 2) == ["KARTHIKEYAN", "STORE"]


def test_side_anchored_name_lines_stop_before_the_picture_beside_them():
    pic = {"b": (2300.0, 0.0, 140.0, 600.0, 0.3, False)}                                  # a tall pack at the right edge
    t = {"name_ta": {"anchor": "right", "ax": 2385.0, "w_cap": 700.0, "h": 60.0, "cy": 120.0}}
    X._clear_of_pictures(t, pic, 2438.4, 914.4)                                               # the picture covers the right edge: end before it
    assert t["name_ta"]["ax"] == pytest.approx(2300.0 - X.PICTURE_GAP_FRAC * 2438.4)
    t = {"name_ta": {"anchor": "left", "ax": 100.0, "w_cap": 2300.0, "h": 60.0, "cy": 120.0}}
    X._clear_of_pictures(t, pic, 2438.4, 914.4)                                               # a picture in the far half: shorter line
    assert 100.0 + t["name_ta"]["w_cap"] <= 2300.0 - X.PICTURE_GAP_FRAC * 2438.4 + 1e-6
    t = {"name_en": {"anchor": "center", "ax": 1200.0, "w_cap": 2000.0, "h": 60.0, "cy": 120.0}}
    X._clear_of_pictures(t, pic, 2438.4, 914.4)
    assert t["name_en"]["w_cap"] == 2000.0                                                    # centred lines are left alone
    t = {"name_ta": {"anchor": "left", "ax": 100.0, "w_cap": 2300.0, "h": 60.0, "cy": 800.0}}
    X._clear_of_pictures(t, {"b": (2300.0, 0.0, 140.0, 300.0, 0.3, False)}, 2438.4, 914.4)   # the picture is above this line: untouched
    assert t["name_ta"]["w_cap"] == 2300.0


def test_a_narrow_panel_narrows_the_english_line_in_step(monkeypatch):
    lib, m0, near10, wide20 = _wide_lib(wide_boards_use_widest_master=True)
    m0["clip_bitmaps"] = []
    near10["clip"] = {"panel": {"cx": 0.5, "cy": 0.5, "w": 0.333, "h": 1.0, "x": 0.3335}}
    wide20["clip"] = {"panel": {"cx": 0.5, "cy": 0.5, "w": 0.2475, "h": 1.0, "x": 0.376}}       # 0.75 of the full-ratio width
    wide20["texts"] = {"name_en": _name(0.5, 0.2, 0.2, 0.3)}
    en = _plan(monkeypatch, lib, m0, near10, 6096)["texts"]["name_en"]
    assert round(en["w_cap"]) == round(X.PANEL_EN_FIT * 0.2475 * 0.75 * 6096)
