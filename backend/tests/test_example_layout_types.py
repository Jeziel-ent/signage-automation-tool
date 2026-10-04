"""Board type, uploaded-master routing and name-line safeguards of the example library (app/example_layout.py, app/main.py)."""
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
