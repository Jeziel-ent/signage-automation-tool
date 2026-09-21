import pytest
from app.layout import Obj, compute_layout, detect_role, find_contact_ids, find_shopname_ids, is_tamil, to_mm

PAGE = (3000, 1000)
OBJS = [
    Obj("0", "bg_wall", "shape", 0, 0, 3000, 1000),
    Obj("1", "frame", "shape", 50, 50, 2900, 900),
    Obj("2", "logo_main", "group", 150, 300, 700, 400),
    Obj("3", "Shop name", "text", 1000, 400, 1500, 200),
    Obj("4", "fixed_phone", "text", 2400, 80, 500, 60),
]


def by_id(res):
    return {p.id: p for p in res}


def test_units():
    assert to_mm(10, "ft") == pytest.approx(3048)
    assert to_mm(2, "in") == pytest.approx(50.8)


def test_role_heuristics():
    assert detect_role(Obj("a", "Rectangle 1", "shape", 0, 0, 3000, 1000), 3000, 1000) == "bg"
    assert detect_role(Obj("a", "Text 1", "text", 10, 10, 50, 20), 3000, 1000) == "text"
    assert detect_role(Obj("a", "Group 1", "group", 10, 10, 50, 20), 3000, 1000) == "logo"


def test_bg_fills_new_page_and_frame_keeps_margin():
    r = by_id(compute_layout(OBJS, *PAGE, 4500, 1500))
    assert (r["0"].w, r["0"].h) == (4500, 1500)
    assert r["1"].x == 50 and r["1"].w == pytest.approx(4500 - 100)
    assert r["1"].h == pytest.approx(1500 - 100)


def test_logo_scales_uniformly_and_stays_proportional():
    r = by_id(compute_layout(OBJS, *PAGE, 1500, 500))  # half size
    logo = r["2"]
    assert logo.w / logo.h == pytest.approx(700 / 400)
    assert logo.w == pytest.approx(350)
    assert (logo.x + logo.w / 2) / 1500 == pytest.approx((150 + 350) / 3000)


def test_fixed_keeps_size_and_edge_distance():
    r = by_id(compute_layout(OBJS, *PAGE, 4000, 1000))
    phone = r["4"]
    assert (phone.w, phone.h) == (500, 60)
    assert 4000 - (phone.x + phone.w) == pytest.approx(100)  # right gap kept


def test_objects_clamped_and_warned():
    r = by_id(compute_layout(OBJS, *PAGE, 3000, 200))  # very short page
    assert all(0 <= p.y and p.y + p.h <= 200 + 1e-6 for p in r.values() if p.role in ("logo", "text"))
    assert r["2"].warnings


def test_bad_size():
    with pytest.raises(ValueError):
        compute_layout(OBJS, *PAGE, 0, 100)


# -- tiling: a bg + one "panel" logo + an untagged shop-name text, on a wide page --
TILE_PAGE = (1000, 200)
TILE_OBJS = [
    Obj("bg", "bg", "shape", 0, 0, 1000, 200),
    Obj("logo", "logo_panel", "group", 400, 50, 200, 100),
    Obj("name", "Shop name", "text", 350, 20, 300, 20, text="OLD SHOP"),
]


def test_tile_off_by_default_matches_untiled_output():
    # tile=False (the default) must ignore aspect ratio entirely, same as before
    r = by_id(compute_layout(TILE_OBJS, *TILE_PAGE, 3000, 200))
    assert set(r) == {"bg", "logo", "name"}  # no _tileN ids appear


def test_tile_horizontal_repeats_panel_with_even_gaps():
    # exclude "name" from the panel via shopname_ids, isolating "logo" as the panel
    r = by_id(compute_layout(TILE_OBJS, *TILE_PAGE, 3000, 200, tile=True, shopname_ids={"name"}))
    tiles = [r[f"logo_tile{i}"] for i in range(3)]
    assert "logo" not in r  # replaced by 3 tile copies
    for t in tiles:
        assert (t.w, t.h) == pytest.approx((400, 200))
    xs = [t.x for t in tiles]
    assert xs == sorted(xs)
    gaps = [xs[i + 1] - (xs[i] + tiles[i].w) for i in range(2)]
    assert gaps[0] == pytest.approx(gaps[1])  # even gaps between copies
    assert tiles[0].x >= 0 and tiles[-1].x + tiles[-1].w <= 3000 + 1e-6


def test_tile_vertical_stacks_panel():
    r = by_id(compute_layout(TILE_OBJS, *TILE_PAGE, 200, 1000, tile=True, shopname_ids={"name"}))
    tiles = [r[f"logo_tile{i}"] for i in range(3)]
    ys = sorted(t.y for t in tiles)  # tile0 is placed at the top (reading order), not the bottom
    gaps = [ys[i + 1] - ys[i] - tiles[0].h for i in range(2)]
    assert gaps[0] == pytest.approx(gaps[1])
    assert ys[0] >= 0 and ys[-1] + tiles[0].h <= 1000 + 1e-6


def test_tile_does_not_duplicate_standalone_footer_text():
    # a small-print footer line (not the shop name) sits alongside the logo;
    # real designer files never duplicate it even when the logo gets tiled
    objs = TILE_OBJS + [Obj("footer", "Authorized Dealer", "text", 10, 5, 80, 10, text="Authorized Dealer")]
    r = by_id(compute_layout(objs, *TILE_PAGE, 3000, 200, tile=True, shopname_ids={"name"}))
    assert "footer" in r  # single instance, not "footer_tile0"/"footer_tile1"...
    assert "footer_tile0" not in r
    assert r["footer"].role == "text"
    assert "logo_tile0" in r and "logo_tile1" in r and "logo_tile2" in r  # the logo still tiles


def test_tile_moves_shopname_into_the_gap_and_replaces_text():
    r = by_id(compute_layout(
        TILE_OBJS, *TILE_PAGE, 3000, 200, tile=True,
        shop_name="NEW SHOP", shopname_ids={"name"},
    ))
    name = r["name"]
    assert name.role == "shopname"
    assert name.text == "NEW SHOP"
    assert name.font is None  # plain ASCII text keeps the original font
    # centred on the page, in the gap between panel copies
    assert name.x + name.w / 2 == pytest.approx(1500, abs=1)
    assert name.y + name.h / 2 == pytest.approx(100, abs=1)


def test_shopname_tamil_text_gets_tamil_font():
    r = by_id(compute_layout(
        TILE_OBJS, *TILE_PAGE, 3000, 200, tile=True,
        shop_name_local="புதிய கடை", shopname_ids={"name"},
    ))
    assert r["name"].text == "புதிய கடை"
    assert r["name"].font == "Nirmala UI"


def test_find_shopname_ids_matches_by_content():
    assert find_shopname_ids(TILE_OBJS, "Old Shop") == {"name"}
    assert find_shopname_ids(TILE_OBJS, "Nonexistent") == set()


def test_is_tamil():
    assert is_tamil("ஸ்ரீ கவி ஸ்டீல்ஸ்")
    assert not is_tamil("SRI KAVI STEELS")
    assert not is_tamil(None)


# -- brand_rule tiling: two named logo groups instead of one rigid panel --
BRAND_PAGE = (1000, 400)
BRAND_OBJS = [
    Obj("bg", "bg", "shape", 0, 0, 1000, 400),
    Obj("big", "big_logo", "group", 100, 100, 200, 200),  # repeats per the rule below
    Obj("small", "small_logo", "group", 700, 150, 100, 100),  # never repeats
    Obj("name", "Shop name", "text", 400, 350, 200, 30, text="OLD SHOP"),
]
BRAND_RULE = {
    "brand": "test",
    "groups": [
        {"cluster_id": 0, "bbox_mm": {"x": 100, "y": 100, "w": 200, "h": 200},
         "repeat": "by_aspect", "repeat_table": [{"aspect": 2.5, "count": 1}, {"aspect": 5.0, "count": 2}]},
        {"cluster_id": 1, "bbox_mm": {"x": 700, "y": 150, "w": 100, "h": 100}, "repeat": "never"},
    ],
}


def test_brand_rule_repeats_only_the_matched_group():
    r = by_id(compute_layout(
        BRAND_OBJS, *BRAND_PAGE, 5000, 400, tile=True,
        shopname_ids={"name"}, brand_rule=BRAND_RULE,
    ))
    big_ids = [k for k in r if k.startswith("big_tile")]
    small_ids = [k for k in r if k.startswith("small_tile")]
    assert len(big_ids) == 2  # nearest-aspect lookup: target aspect 12.5 -> nearest table entry is 5.0 -> count 2
    assert len(small_ids) == 1  # "never" group always gets exactly one copy
    assert "big" not in r and "small" not in r  # replaced by their _tileN copies


def test_brand_rule_groups_ordered_left_to_right_by_master_position():
    r = by_id(compute_layout(
        BRAND_OBJS, *BRAND_PAGE, 5000, 400, tile=True,
        shopname_ids={"name"}, brand_rule=BRAND_RULE,
    ))
    big_xs = [r[k].x for k in r if k.startswith("big_tile")]
    small_xs = [r[k].x for k in r if k.startswith("small_tile")]
    # "big" sits left of "small" in the master (centre x=200 vs 750), so its
    # copies must occupy the leftmost cells regardless of repeat count
    assert max(big_xs) < min(small_xs)


def test_brand_rule_falls_back_to_generic_panel_without_a_rule():
    # same objects/target, but no brand_rule -> old single-rigid-panel behaviour:
    # "big" and "small" tile together as one panel, same count for both
    r = by_id(compute_layout(BRAND_OBJS, *BRAND_PAGE, 5000, 400, tile=True, shopname_ids={"name"}))
    big_n = len([k for k in r if k.startswith("big_tile")])
    small_n = len([k for k in r if k.startswith("small_tile")])
    assert big_n == small_n  # no per-group distinction without brand_rule
    assert big_n > 1


def test_load_brand_rule_missing_brand_returns_none():
    from app.layout import load_brand_rule
    assert load_brand_rule(None) is None
    assert load_brand_rule("no_such_brand_xyz") is None


# -- per-shop content replacement: phone/GST footer, found by label not by old value --
CONTACT_OBJS = [
    Obj("bg", "bg", "shape", 0, 0, 1000, 400),
    Obj("name", "Shop name", "text", 400, 350, 200, 30, text="OLD SHOP"),
    Obj("contact", "Contact info", "text", 100, 20, 300, 40,
        text="Phone No. 90000 11111\rGST NO. 33OLDOLD0000O1Z1"),
]


def test_find_contact_ids_matches_by_label_not_value():
    # unlike find_shopname_ids, no old value needs to be passed in - the
    # stable "Phone No"/"GST NO" label is what's matched
    assert find_contact_ids(CONTACT_OBJS) == {"contact"}


def test_find_contact_ids_matches_phone_only_line():
    objs = [Obj("c", "c", "text", 0, 0, 10, 10, text="Phone No. 12345 67890")]
    assert find_contact_ids(objs) == {"c"}


def test_find_contact_ids_ignores_unrelated_text():
    assert find_contact_ids([Obj("name", "n", "text", 0, 0, 10, 10, text="SRI KAVI STEELS")]) == set()


def test_contact_replacement_substitutes_both_values_keeping_labels():
    r = by_id(compute_layout(
        CONTACT_OBJS, 1000, 400, 1000, 400,
        phone="99999 88888", gst="33NEWNEW1111N1Z9", contact_ids={"contact"},
    ))
    assert r["contact"].text == "Phone No. 99999 88888\rGST NO. 33NEWNEW1111N1Z9"


def test_contact_replacement_phone_only_leaves_gst_line_untouched_if_not_given():
    r = by_id(compute_layout(
        CONTACT_OBJS, 1000, 400, 1000, 400, phone="99999 88888", contact_ids={"contact"},
    ))
    assert "99999 88888" in r["contact"].text
    assert "33OLDOLD0000O1Z1" in r["contact"].text  # untouched, gst=None means "don't change this line"


def test_contact_replacement_appends_gst_line_when_master_had_none():
    objs = [Obj("contact", "c", "text", 0, 0, 300, 40, text="Phone No. 90000 11111")]
    r = by_id(compute_layout(objs, 1000, 400, 1000, 400, phone="1", gst="33ABC", contact_ids={"contact"}))
    assert r["contact"].text == "Phone No. 1\rGST NO. 33ABC"


def test_contact_replacement_appends_address_lines():
    r = by_id(compute_layout(
        CONTACT_OBJS, 1000, 400, 1000, 400,
        phone="1", gst="2", address_lines=["12 Main St", "Chennai"], contact_ids={"contact"},
    ))
    assert r["contact"].text == "Phone No. 1\rGST NO. 2\r12 Main St\rChennai"


def test_contact_replacement_none_when_no_fields_given():
    r = by_id(compute_layout(CONTACT_OBJS, 1000, 400, 1000, 400, contact_ids={"contact"}))
    assert r["contact"].text is None  # nothing to replace -> shape's own text left as-is by the caller


# -- panel_sequence tiling: distinct fixed/enlarged/repeating slots, not one rigid panel --
SEQ_PAGE = (1000.0, 400.0)
SEQ_OBJS = [
    Obj("bg", "bg", "shape", 0, 0, 1000, 400),
    Obj("card_a", "logo_a", "group", 50, 100, 200, 200),  # "tamil_card": never repeats
    Obj("filler", "logo_b", "group", 400, 150, 100, 100),  # "roof_graphic": the repeat element
    Obj("badge", "logo_c", "group", 850, 350, 40, 20),  # "enlarged_badge_card": tiny in the master
    Obj("name", "Shop name", "text", 300, 350, 200, 30, text="OLD SHOP"),
]
SEQ_RULE = {
    "panel_sequence": {
        "aspect_split": 4.25,
        "groups": [
            {"group_id": "tamil_card", "bbox_mm": {"x": 50, "y": 100, "w": 200, "h": 200},
             "size_table": [{"aspect": 3.0, "target_h_frac": 0.5, "target_w_frac": 1.0, "target_cy_frac": 0.5}]},
            {"group_id": "roof_graphic", "bbox_mm": {"x": 400, "y": 150, "w": 100, "h": 100},
             "size_table": [{"aspect": 3.0, "target_h_frac": 0.3, "target_w_frac": 1.0, "target_cy_frac": 0.5}]},
            {"group_id": "enlarged_badge_card", "bbox_mm": {"x": 850, "y": 350, "w": 40, "h": 20},
             "size_table": [{"aspect": 3.0, "target_h_frac": 0.5, "target_w_frac": 1.0, "target_cy_frac": 0.5}]},
        ],
        "sequence_3": ["tamil_card", "roof_graphic", "enlarged_badge_card"],
        "sequence_4": ["tamil_card", "roof_graphic", "enlarged_badge_card", "roof_graphic"],
    },
}


def test_panel_sequence_uses_3_slots_below_aspect_split():
    # target aspect 3.0 (1000x400 -> 3000x1000) is below aspect_split=4.25
    r = by_id(compute_layout(SEQ_OBJS, *SEQ_PAGE, 3000, 1000, tile=True, shopname_ids={"name"}, brand_rule=SEQ_RULE))
    card_a_ids = [k for k in r if k.startswith("card_a_tile")]
    filler_ids = [k for k in r if k.startswith("filler_tile")]
    badge_ids = [k for k in r if k.startswith("badge_tile")]
    assert len(card_a_ids) == 1 and len(filler_ids) == 1 and len(badge_ids) == 1


def test_panel_sequence_adds_a_second_filler_copy_beyond_aspect_split():
    # target aspect 5.0 (1000x400 -> 5000x1000) is above aspect_split=4.25
    r = by_id(compute_layout(SEQ_OBJS, *SEQ_PAGE, 5000, 1000, tile=True, shopname_ids={"name"}, brand_rule=SEQ_RULE))
    filler_ids = [k for k in r if k.startswith("filler_tile")]
    card_a_ids = [k for k in r if k.startswith("card_a_tile")]
    badge_ids = [k for k in r if k.startswith("badge_tile")]
    assert len(filler_ids) == 2  # the repeating slot appears twice in sequence_4
    assert len(card_a_ids) == 1 and len(badge_ids) == 1  # the two "card" slots still appear exactly once


def test_panel_sequence_enlarges_badge_to_its_target_height_not_its_master_size():
    r = by_id(compute_layout(SEQ_OBJS, *SEQ_PAGE, 3000, 1000, tile=True, shopname_ids={"name"}, brand_rule=SEQ_RULE))
    badge = next(p for k, p in r.items() if k.startswith("badge_tile"))
    # master badge h=20mm; target_h_frac=0.5 of new_h=1000 -> should become 500mm, NOT
    # a small uniform-fit-scaled version of its own tiny 20mm height
    assert badge.h == pytest.approx(500, rel=0.01)


def test_panel_sequence_slots_are_evenly_spaced_left_to_right():
    # SEQ_RULE's groups have no cx_table - this locks in the FALLBACK path
    # (naive even-cell horizontal centering) used when a group's real
    # horizontal position has never been measured, exercised by every
    # hand-built fixture in this file. See the cx_table tests below for the
    # measured-position path real dalmia boards now use.
    r = by_id(compute_layout(SEQ_OBJS, *SEQ_PAGE, 3000, 1000, tile=True, shopname_ids={"name"}, brand_rule=SEQ_RULE))
    card_a = next(p for k, p in r.items() if k.startswith("card_a_tile"))
    filler = next(p for k, p in r.items() if k.startswith("filler_tile"))
    badge = next(p for k, p in r.items() if k.startswith("badge_tile"))
    centres = [card_a.x + card_a.w / 2, filler.x + filler.w / 2, badge.x + badge.w / 2]
    assert centres == sorted(centres)  # sequence order preserved left to right
    # 3 evenly spaced cells over width 3000 -> centres near 1/6, 3/6, 5/6
    assert centres[0] == pytest.approx(3000 / 6, abs=1)
    assert centres[1] == pytest.approx(3000 / 2, abs=1)
    assert centres[2] == pytest.approx(3000 * 5 / 6, abs=1)


def test_panel_sequence_uses_cx_table_instead_of_even_cell_spacing_when_present():
    # Regression lock for the engine-accuracy fix: all 4 real wide dalmia
    # boards showed every panel_sequence group landing 840-1100mm too far
    # left because the horizontal centre was never measured (just assumed
    # to be "cell i of n") - a group with its own cx_table now uses that
    # measured fraction of new_w directly instead.
    rule = {"panel_sequence": {
        **SEQ_RULE["panel_sequence"],
        "groups": [
            {**SEQ_RULE["panel_sequence"]["groups"][0], "cx_table": [{"aspect": 3.0, "target_cx_frac": 0.5}]},
            SEQ_RULE["panel_sequence"]["groups"][1],
            SEQ_RULE["panel_sequence"]["groups"][2],
        ],
    }}
    r = by_id(compute_layout(SEQ_OBJS, *SEQ_PAGE, 3000, 1000, tile=True, shopname_ids={"name"}, brand_rule=rule))
    card_a = next(p for k, p in r.items() if k.startswith("card_a_tile"))
    # cx_table says 0.5 (page centre), NOT its "cell 0 of 3" position (1/6)
    assert card_a.x + card_a.w / 2 == pytest.approx(3000 * 0.5, abs=1)
    # the other two groups have no cx_table - unaffected, still even-cell
    filler = next(p for k, p in r.items() if k.startswith("filler_tile"))
    assert filler.x + filler.w / 2 == pytest.approx(3000 / 2, abs=1)


def test_interp_cx_frac_returns_none_without_a_cx_table():
    from app.layout import _interp_cx_frac
    assert _interp_cx_frac({"group_id": "x"}, 3.5) is None


def test_interp_cx_frac_interpolates_between_two_nearest_aspects():
    from app.layout import _interp_cx_frac
    cfg = {"cx_table": [{"aspect": 3.0, "target_cx_frac": 0.2}, {"aspect": 5.0, "target_cx_frac": 0.4}]}
    assert _interp_cx_frac(cfg, 4.0) == pytest.approx(0.3)  # exactly midway


def test_interp_cx_frac_clamps_outside_range_instead_of_extrapolating():
    from app.layout import _interp_cx_frac
    cfg = {"cx_table": [{"aspect": 3.0, "target_cx_frac": 0.2}, {"aspect": 4.5, "target_cx_frac": 0.3}]}
    assert _interp_cx_frac(cfg, 2.0) == pytest.approx(0.2)  # below range -> clamp to first
    assert _interp_cx_frac(cfg, 6.0) == pytest.approx(0.3)  # above range -> clamp to last


def test_panel_sequence_card_from_uses_the_borrowing_groups_own_cx_table_not_the_templates():
    # enlarged_badge_card borrows tamil_card's size/scale (h/w/cy) but its
    # horizontal position must be its OWN measured slot, not tamil_card's -
    # each named group sits at a genuinely different page position. Uses
    # the real card_from fixture (CARD_FROM_RULE, defined further below)
    # rather than SEQ_RULE, which doesn't exercise card_from at all.
    rule = {"panel_sequence": {
        **CARD_FROM_RULE["panel_sequence"],
        "groups": [
            {**CARD_FROM_RULE["panel_sequence"]["groups"][0], "cx_table": [{"aspect": 3.0, "target_cx_frac": 0.1}]},
            CARD_FROM_RULE["panel_sequence"]["groups"][1],
            {**CARD_FROM_RULE["panel_sequence"]["groups"][2], "cx_table": [{"aspect": 3.0, "target_cx_frac": 0.9}]},
        ],
    }}
    r = by_id(compute_layout(
        CARD_FROM_OBJS, *CARD_FROM_PAGE, 3000, 1000, tile=True,
        shopname_ids={"name"}, brand_rule=rule,
    ))
    tamil_bg = r["tamil_bg_tile0"]
    borrowed_bg = next(p for k, p in r.items() if k.startswith("tamil_bg_tile") and k != "tamil_bg_tile0")
    assert tamil_bg.x + tamil_bg.w / 2 == pytest.approx(3000 * 0.1, abs=1)  # template's own slot
    assert borrowed_bg.x + borrowed_bg.w / 2 == pytest.approx(3000 * 0.9, abs=1)  # borrower's own slot, not the template's


def test_panel_sequence_wins_over_groups_schema_when_both_present():
    rule = {**SEQ_RULE, "groups": BRAND_RULE["groups"]}
    r = by_id(compute_layout(SEQ_OBJS, *SEQ_PAGE, 3000, 1000, tile=True, shopname_ids={"name"}, brand_rule=rule))
    assert any(k.startswith("card_a_tile") for k in r)  # panel_sequence's own ids, not the groups schema's


def test_interp_size_table_interpolates_between_two_nearest_aspects():
    from app.layout import _interp_size_table
    table = [
        {"aspect": 3.0, "target_h_frac": 0.4, "target_w_frac": 0.2, "target_cy_frac": 0.5},
        {"aspect": 5.0, "target_h_frac": 0.6, "target_w_frac": 0.4, "target_cy_frac": 0.7},
    ]
    h, w, cy = _interp_size_table(table, 4.0)  # exactly midway
    assert h == pytest.approx(0.5)
    assert w == pytest.approx(0.3)
    assert cy == pytest.approx(0.6)


def test_interp_size_table_clamps_outside_range_instead_of_extrapolating():
    from app.layout import _interp_size_table
    table = [
        {"aspect": 3.0, "target_h_frac": 0.4, "target_w_frac": 0.2, "target_cy_frac": 0.5},
        {"aspect": 4.5, "target_h_frac": 0.6, "target_w_frac": 0.3, "target_cy_frac": 0.53},
    ]
    assert _interp_size_table(table, 2.0) == (0.4, 0.2, 0.5)  # below range -> clamp to first
    assert _interp_size_table(table, 6.0) == (0.6, 0.3, 0.53)  # above range -> clamp to last


def test_panel_sequence_uses_per_aspect_size_not_a_flat_average():
    # two aspects with different target sizes for the same group - the
    # placed size at each target must match ITS OWN aspect's table entry,
    # not an average of both
    rule = {
        "panel_sequence": {
            "aspect_split": 100.0,  # keep both cases in sequence_3
            "groups": [
                {"group_id": "tamil_card", "bbox_mm": {"x": 50, "y": 100, "w": 200, "h": 200},
                 "size_table": [{"aspect": 3.0, "target_h_frac": 0.4, "target_w_frac": 1.0, "target_cy_frac": 0.5},
                                {"aspect": 4.0, "target_h_frac": 0.8, "target_w_frac": 1.0, "target_cy_frac": 0.5}]},
                {"group_id": "roof_graphic", "bbox_mm": {"x": 400, "y": 150, "w": 100, "h": 100},
                 "size_table": [{"aspect": 3.0, "target_h_frac": 0.3, "target_w_frac": 1.0, "target_cy_frac": 0.5}]},
                {"group_id": "enlarged_badge_card", "bbox_mm": {"x": 850, "y": 350, "w": 40, "h": 20},
                 "size_table": [{"aspect": 3.0, "target_h_frac": 0.2, "target_w_frac": 1.0, "target_cy_frac": 0.5}]},
            ],
            "sequence_3": ["tamil_card", "roof_graphic", "enlarged_badge_card"],
            "sequence_4": ["tamil_card", "roof_graphic", "enlarged_badge_card", "roof_graphic"],
        },
    }
    r_at_3 = by_id(compute_layout(SEQ_OBJS, *SEQ_PAGE, 3000, 1000, tile=True, shopname_ids={"name"}, brand_rule=rule))
    r_at_4 = by_id(compute_layout(SEQ_OBJS, *SEQ_PAGE, 4000, 1000, tile=True, shopname_ids={"name"}, brand_rule=rule))
    card_at_3 = next(p for k, p in r_at_3.items() if k.startswith("card_a_tile"))
    card_at_4 = next(p for k, p in r_at_4.items() if k.startswith("card_a_tile"))
    assert card_at_3.h == pytest.approx(0.4 * 1000)
    assert card_at_4.h == pytest.approx(0.8 * 1000)


def test_panel_sequence_clamps_scale_by_width_when_it_is_the_tighter_constraint():
    # a group whose master shape is much wider (relative to its height) than
    # its measured target_w_frac allows must be scaled down to fit the
    # width, not overflow the cell by scaling purely off target_h_frac -
    # this is the real bug found on 06 (180x60): the enlarged badge card's
    # real width/height ratio doesn't match its master shape's own ratio
    rule = {
        "panel_sequence": {
            "aspect_split": 100.0,
            "groups": [
                {"group_id": "tamil_card", "bbox_mm": {"x": 50, "y": 100, "w": 200, "h": 200},
                 "size_table": [{"aspect": 3.0, "target_h_frac": 0.5, "target_w_frac": 1.0, "target_cy_frac": 0.5}]},
                {"group_id": "roof_graphic", "bbox_mm": {"x": 400, "y": 150, "w": 100, "h": 100},
                 "size_table": [{"aspect": 3.0, "target_h_frac": 0.3, "target_w_frac": 1.0, "target_cy_frac": 0.5}]},
                # master badge is 40x20 (aspect 2.0); a target_h_frac of 0.5 (500mm on a
                # 1000mm-tall page) would naively scale it to 1000x500mm - way over a
                # 1000mm-wide page split into 3 cells (333mm each) - target_w_frac=0.2
                # (200mm) must be the binding constraint instead
                {"group_id": "enlarged_badge_card", "bbox_mm": {"x": 850, "y": 350, "w": 40, "h": 20},
                 "size_table": [{"aspect": 3.0, "target_h_frac": 0.5, "target_w_frac": 0.2, "target_cy_frac": 0.5}]},
            ],
            "sequence_3": ["tamil_card", "roof_graphic", "enlarged_badge_card"],
            "sequence_4": ["tamil_card", "roof_graphic", "enlarged_badge_card", "roof_graphic"],
        },
    }
    r = by_id(compute_layout(SEQ_OBJS, *SEQ_PAGE, 3000, 1000, tile=True, shopname_ids={"name"}, brand_rule=rule))
    badge = next(p for k, p in r.items() if k.startswith("badge_tile"))
    assert badge.w == pytest.approx(0.2 * 3000)  # width-constrained, not height-constrained
    assert badge.h < 0.5 * 1000  # shorter than the naive height-only target, to preserve aspect


# -- panel_sequence "card_from": a bare group gets a borrowed white-card background --
CARD_FROM_PAGE = (1000.0, 400.0)
CARD_FROM_OBJS = [
    Obj("bg", "bg", "shape", 0, 0, 1000, 400),
    Obj("tamil_bg", "card_bg", "shape", 50, 100, 200, 200),  # the "white card" - covers the whole group bbox
    Obj("tamil_content", "logo_icon", "group", 120, 170, 60, 60),  # icon+text, small relative to the card
    Obj("filler", "logo_b", "group", 400, 150, 100, 100),
    Obj("badge_content", "logo_c", "group", 850, 350, 40, 40),  # bare in the master - no card of its own; square so its content:card ratio is exact on both axes
    Obj("name", "Shop name", "text", 300, 350, 200, 30, text="OLD SHOP"),
]
CARD_FROM_RULE = {
    "panel_sequence": {
        "aspect_split": 100.0,  # keep this test in sequence_3
        "groups": [
            {"group_id": "tamil_card", "bbox_mm": {"x": 50, "y": 100, "w": 200, "h": 200},
             "size_table": [{"aspect": 3.0, "target_h_frac": 0.5, "target_w_frac": 1.0, "target_cy_frac": 0.5}]},
            {"group_id": "roof_graphic", "bbox_mm": {"x": 400, "y": 150, "w": 100, "h": 100},
             "size_table": [{"aspect": 3.0, "target_h_frac": 0.3, "target_w_frac": 1.0, "target_cy_frac": 0.5}]},
            {"group_id": "enlarged_badge_card", "bbox_mm": {"x": 850, "y": 350, "w": 40, "h": 20},
             "card_from": "tamil_card", "card_content_frac": {"w": 0.3, "h": 0.3, "cx": 0.5, "cy": 0.5}},
        ],
        "sequence_3": ["tamil_card", "roof_graphic", "enlarged_badge_card"],
        "sequence_4": ["tamil_card", "roof_graphic", "enlarged_badge_card", "roof_graphic"],
    },
}


def test_panel_sequence_card_from_gives_the_bare_group_a_matching_card_background():
    r = by_id(compute_layout(
        CARD_FROM_OBJS, *CARD_FROM_PAGE, 3000, 1000, tile=True,
        shopname_ids={"name"}, brand_rule=CARD_FROM_RULE,
    ))
    # the borrowed card background (a duplicate of tamil_bg) must appear in the badge's slot
    borrowed_bg = next(p for k, p in r.items() if k.startswith("tamil_bg_tile") and k != "tamil_bg_tile0")
    tamil_bg = r["tamil_bg_tile0"]
    badge_content = next(p for k, p in r.items() if k.startswith("badge_content_tile"))
    # same size as the template's own card (both use tamil_card's size table)
    assert borrowed_bg.w == pytest.approx(tamil_bg.w, rel=0.01)
    assert borrowed_bg.h == pytest.approx(tamil_bg.h, rel=0.01)
    # badge's own content sits INSIDE the borrowed card, not stretched to fill it
    assert badge_content.w < borrowed_bg.w
    assert badge_content.h < borrowed_bg.h
    assert borrowed_bg.x <= badge_content.x and badge_content.x + badge_content.w <= borrowed_bg.x + borrowed_bg.w


def test_panel_sequence_card_from_content_matches_measured_proportion():
    r = by_id(compute_layout(
        CARD_FROM_OBJS, *CARD_FROM_PAGE, 3000, 1000, tile=True,
        shopname_ids={"name"}, brand_rule=CARD_FROM_RULE,
    ))
    borrowed_bg = next(p for k, p in r.items() if k.startswith("tamil_bg_tile") and k != "tamil_bg_tile0")
    badge_content = next(p for k, p in r.items() if k.startswith("badge_content_tile"))
    assert badge_content.w / borrowed_bg.w == pytest.approx(0.3, abs=0.02)
    assert badge_content.h / borrowed_bg.h == pytest.approx(0.3, abs=0.02)


def test_panel_sequence_card_from_recolors_white_content_to_match_template_text():
    # tamil_content is styled dark blue in the master; badge_content is pure
    # white (styled for the master's own dark background) - it must be
    # recoloured to match when moved onto the new white card, or it renders
    # invisibly (confirmed live on a real board - see CLAUDE.md)
    objs = [o for o in CARD_FROM_OBJS if o.id != "tamil_content"] + [
        Obj("tamil_content", "logo_icon", "group", 120, 170, 60, 60, fill_cmyk=(95, 80, 4, 0)),
    ]
    objs = [o for o in objs if o.id != "badge_content"] + [
        Obj("badge_content", "logo_c", "group", 850, 350, 40, 40, fill_cmyk=(0, 0, 0, 0)),
    ]
    r = by_id(compute_layout(
        objs, *CARD_FROM_PAGE, 3000, 1000, tile=True,
        shopname_ids={"name"}, brand_rule=CARD_FROM_RULE,
    ))
    badge_content = next(p for k, p in r.items() if k.startswith("badge_content_tile"))
    assert badge_content.recolor_cmyk == (95, 80, 4, 0)


def test_panel_sequence_card_from_does_not_recolor_non_white_content():
    objs = [o for o in CARD_FROM_OBJS if o.id != "tamil_content"] + [
        Obj("tamil_content", "logo_icon", "group", 120, 170, 60, 60, fill_cmyk=(95, 80, 4, 0)),
    ]
    objs = [o for o in objs if o.id != "badge_content"] + [
        Obj("badge_content", "logo_c", "group", 850, 350, 40, 40, fill_cmyk=(20, 30, 40, 0)),  # not white - e.g. the icon
    ]
    r = by_id(compute_layout(
        objs, *CARD_FROM_PAGE, 3000, 1000, tile=True,
        shopname_ids={"name"}, brand_rule=CARD_FROM_RULE,
    ))
    badge_content = next(p for k, p in r.items() if k.startswith("badge_content_tile"))
    assert badge_content.recolor_cmyk is None


def test_panel_sequence_keeps_unmatched_shape_at_its_own_proportional_position():
    # a decorative shape far from every named group's bbox (e.g. dalmia's
    # real full-width accent strip) must NOT be force-assigned to whichever
    # group happens to be nearest - it should keep its own proportional
    # position/size instead, like an ordinary un-tiled logo
    objs = SEQ_OBJS + [Obj("strip", "accent_line", "shape", 0, 390, 1000, 2, text=None)]
    r = by_id(compute_layout(objs, *SEQ_PAGE, 3000, 1000, tile=True, shopname_ids={"name"}, brand_rule=SEQ_RULE))
    strip = next(p for k, p in r.items() if k.startswith("strip"))
    # proportional position preserved: centre x fraction and y fraction unchanged
    assert (strip.x + strip.w / 2) / 3000 == pytest.approx((0 + 1000 / 2) / 1000, abs=0.01)
    assert (strip.y + strip.h / 2) / 1000 == pytest.approx((390 + 2 / 2) / 400, abs=0.01)
    # and it must not have inflated any group's bounding box - the 3 real groups still fit their cells
    card_a = next(p for k, p in r.items() if k.startswith("card_a_tile"))
    assert card_a.w < 3000 / 3  # sane cell-relative size, not blown up by contamination
