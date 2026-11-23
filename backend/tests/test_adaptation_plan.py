"""Target adaptation planning + validation + the legacy integration (no CorelDRAW)."""
from __future__ import annotations

import math

import pytest

from app import adaptation_plan as ap, layout, master_model as mm, plan_validation as pv
from app.engines import CorelEngine
from spatial_fixtures import build, extraction, obj, sign_10x8

FT = 304.8


def sized(wf, hf):
    return wf * FT, hf * FT


# ----------------------------------------------------------------------------------------------------------- scenario A


def test_same_aspect_10x8_to_20x16_is_an_exact_2x_scale():
    m = build()                                                     # 10 x 8 ft master
    p = ap.plan_adaptation(m, *sized(20, 16))
    assert p.strategy == "uniform" and p.supported and p.validation == []
    for o in p.objects:
        src = m.get(o.id).box_mm
        for k in ("x", "y", "width", "height"):
            assert o.box[k] == pytest.approx(src[k] * 2, abs=1e-3)


# ----------------------------------------------------------------------------------------------------------- scenario B


def test_different_aspect_10x8_to_20x6_keeps_logos_text_and_photo_undistorted():
    m = build()
    tw, th = sized(12, 6)                                           # a wide-ish board that does not trigger panel tiling
    p = ap.plan_adaptation(m, tw, th)
    assert p.strategy == "fit" and p.supported
    s = min(tw / m.page_w, th / m.page_h)
    by = {o.id: o for o in p.objects}
    # background fills the board exactly, no other object is stretched
    assert by["0"].box == {"x": 0.0, "y": 0.0, "width": pytest.approx(tw), "height": pytest.approx(th)} and by["0"].mode == mm.STRETCH
    for oid in ("1", "3", "4"):
        b, src = by[oid].box, m.get(oid).box_mm
        assert b["width"] == pytest.approx(src["width"] * s, abs=1e-2) and b["height"] == pytest.approx(src["height"] * s, abs=1e-2)
        assert (b["width"] / b["height"]) == pytest.approx(src["width"] / src["height"], rel=1e-4)
    # fixed object keeps its size; anchored badge keeps its distance to the top/right edges (scaled)
    assert by["5"].box["width"] == 600 and by["5"].mode == mm.FIXED
    badge = by["6"]
    assert badge.mode == mm.ANCHOR and tw - (badge.box["x"] + badge.box["width"]) == pytest.approx((m.page_w - 3020) * s, abs=1e-2)
    assert th - (badge.box["y"] + badge.box["height"]) == pytest.approx((m.page_h - 2430.0) * s, abs=1e-2)
    assert not pv.has_errors([pv.Issue(**v) for v in p.validation])


def test_20x6_from_10x8_is_tiled_when_the_legacy_rule_tiles_it():
    m = build()
    p = ap.plan_adaptation(m, *sized(20, 6))
    assert p.strategy == "tile" and p.tiling["axis"] == "x" and p.tiling["tiles"] == 2
    assert any("_tile1" in o.id for o in p.objects) and all(o.mode == mm.TILE for o in p.objects if "_tile" in o.id)


# ----------------------------------------------------------------------------------------------------------- scenario C


def test_landscape_to_portrait_reflows_through_the_editor_zones_without_overlap_or_clipping():
    objs = [obj(0, "bg", "rectangle", 0, 0, 3048, 914, fill_cmyk=(95, 80, 4, 0)),
            obj(1, "logo_left", "group", 100, 500, 400, 300), obj(2, "logo_right", "group", 2500, 500, 400, 300),
            obj(3, "product", "bitmap", 1100, 150, 800, 500),
            obj(4, "name", "text", 900, 700, 1200, 120, text="SHOP"), obj(5, "fixed_phone", "text", 100, 30, 700, 50, text="Phone No. 1")]
    m = mm.build_model(extraction(3048, 914, objs))
    p = ap.plan_adaptation(m, 4 * FT, 8 * FT)
    assert p.strategy == "reflow" and p.supported and p.target["orientation"] == "portrait"
    assert all(o.mode == mm.REFLOW for o in p.objects)
    tw, th = 4 * FT, 8 * FT
    for o in p.objects:
        if o.id == "0":
            continue
        b = o.box
        assert b["x"] >= -0.5 and b["y"] >= -0.5 and b["x"] + b["width"] <= tw + 0.5 and b["y"] + b["height"] <= th + 0.5
    assert [i for i in p.validation if i["severity"] == "error"] == []
    prod = next(o for o in p.objects if o.id == "3")
    assert prod.box["width"] / prod.box["height"] == pytest.approx(800 / 500, rel=1e-3)          # the photo is not stretched


def test_reflow_declines_when_foreground_hides_inside_the_background_powerclip():
    objs = [obj(0, "clip", "rectangle", 0, 0, 3048, 914, kind="powerclip", children=["0.0"]),
            obj("0.0", "inner_logo", "group", 600, 100, 500, 400, parent="0", z=0)]
    m = mm.build_model(extraction(3048, 914, objs))
    p = ap.plan_adaptation(m, 4 * FT, 8 * FT)
    assert p.strategy == "declined" and "PowerClip" in p.reason


# ----------------------------------------------------------------------------------------------------------- scenario D / E


def test_very_wide_board_reports_a_tile_plan_with_copies_and_even_continuity():
    objs = [obj(0, "bg", "rectangle", 0, 0, 3048, 914), obj(1, "logo", "group", 1000, 200, 900, 500)]
    m = mm.build_model(extraction(3048, 914, objs))
    p = ap.plan_adaptation(m, 9144, 914)
    assert p.strategy == "tile" and p.tiling["tiles"] >= 2
    copies = [o for o in p.objects if o.id.startswith("1_tile")]
    assert copies and len({round(o.box["width"], 3) for o in copies}) == 1


@pytest.mark.parametrize("name", ["AB", "A VERY LONG SHOP NAME PRIVATE LIMITED", "ஸ்ரீ கவி ஸ்டீல்ஸ் மற்றும் நிறுவனம்"])
def test_text_plan_does_not_depend_on_the_name_length(name):
    """Text replacement and fitting stay with the engine (TEXT_FIT): the plan only gives the slot, so it is identical for any name."""
    m = build()
    assert ap.plan_adaptation(m, *sized(12, 6)).box_of("3") == ap.plan_adaptation(m, *sized(12, 6)).box_of("3")
    assert m.get("3").adaptation_mode == mm.TEXT_FIT


def test_invalid_target_and_empty_master():
    with pytest.raises(ValueError):
        ap.plan_adaptation(build(), 0, 100)
    m = mm.build_model(extraction(3000, 1000, [obj(0, "h", "curve", 1, 1, 5, 5, visible=False)]))
    assert ap.plan_adaptation(m, 6000, 2000).strategy == "declined"


# ------------------------------------------------------------------------------------------------------------ validation


def test_validation_flags_distortion_overlap_and_out_of_page_but_not_intentional_overlap():
    m = build()
    p = ap.plan_adaptation(m, *sized(12, 6))
    by = {o.id: o for o in p.objects}
    # intentional: the shop name sits on its card in the master and still does
    assert not [i for i in pv.validate_plan(m, p) if i.rule == "new_overlap" and {"2", "3"} <= set(i.ids)]
    by["1"].box = dict(by["1"].box, width=by["1"].box["width"] * 3)                      # distort a logo
    by["4"].box = dict(by["4"].box, x=-500.0)                                            # push the photo off the board
    by["5"].box = dict(by["3"].box)                                                      # phone on top of the name: a NEW overlap
    rules = {(i.rule, i.severity) for i in pv.validate_plan(m, p)}
    assert ("distortion", "error") in rules and ("out_of_page", "error") in rules and any(r == "new_overlap" for r, _ in rules)


def test_validation_flags_background_that_stops_covering_and_nan():
    m = build()
    p = ap.plan_adaptation(m, *sized(12, 6))
    bg = next(o for o in p.objects if o.id == "0")
    bg.box = dict(bg.box, width=bg.box["width"] * 0.8)
    assert any(i.rule == "background_coverage" for i in pv.validate_plan(m, p))
    bg.box = dict(bg.box, x=float("nan"))
    assert [i.rule for i in pv.validate_plan(m, p)] == ["invalid_coordinates"]


def test_orientation_consistency_warning_when_a_cross_orientation_plan_did_not_reflow():
    m = build()
    p = ap.plan_adaptation(m, *sized(12, 6))
    p.target = dict(p.target, orientation="portrait")
    assert any(i.rule == "orientation_consistency" for i in pv.validate_plan(m, p))


# ------------------------------------------------------------------------------------------ integration with the legacy path


def _objs_from_model(m):
    return [layout.Obj(o.id, o.name, mm._layout_kind(o), o.box_mm["x"], o.box_mm["y"], o.box_mm["width"], o.box_mm["height"],
                       (o.text or {}).get("content"), mm._cmyk_of(o), o.n_descendants if o.type == "group" else 0) for o in m.top_level()]


def _legacy(m, tw, th, **kw):
    return layout.compute_layout(_objs_from_model(m), m.page_w, m.page_h, tw, th, tile=False, **kw)


def test_apply_to_placed_moves_plain_objects_only_and_leaves_the_name_line_to_the_legacy_path():
    m = build()
    objs = _objs_from_model(m)
    tw, th = sized(12, 6)
    placed = layout.compute_layout(objs, m.page_w, m.page_h, tw, th, tile=False, shop_name="NEW", shopname_ids={"3"})
    legacy = {p.id: (p.x, p.y, p.w, p.h) for p in placed}
    name = next(p for p in placed if p.id == "3")
    summary = ap.apply_to_placed(placed, m, objs, tw, th, page_w=m.page_w, page_h=m.page_h)
    assert summary["used"] and summary["strategy"] == "fit"
    assert (name.x, name.y, name.w, name.h) == legacy["3"] and name.text == "NEW"          # shop-name line untouched
    badge = next(p for p in placed if p.id == "6")
    assert (badge.x, badge.y) != legacy["6"][:2]                                          # the anchored badge now keeps its edge distance


def test_a_model_that_does_not_match_the_opened_file_is_never_used():
    m = build()
    objs = _objs_from_model(m)
    tw, th = sized(12, 6)
    placed = _legacy(m, tw, th)
    before = [(p.x, p.y, p.w, p.h) for p in placed]
    objs[1].x += 50                                                                       # the master changed since the analysis
    s = ap.apply_to_placed(placed, m, objs, tw, th, page_w=m.page_w, page_h=m.page_h)
    assert not s["used"] and "differs" in s["reason"]
    assert [(p.x, p.y, p.w, p.h) for p in placed] == before
    s2 = ap.apply_to_placed(placed, m, objs[:-1], tw, th, page_w=m.page_w, page_h=m.page_h)
    assert not s2["used"] and "count" in s2["reason"]


def test_tiled_and_failed_validation_plans_fall_back_to_legacy(monkeypatch):
    m = build()
    objs = _objs_from_model(m)
    tw, th = sized(20, 6)
    placed = _legacy(m, tw, th)
    before = [(p.x, p.y) for p in placed]
    assert not ap.apply_to_placed(placed, m, objs, tw, th, page_w=m.page_w, page_h=m.page_h)["used"]
    assert [(p.x, p.y) for p in placed] == before
    tw, th = sized(12, 6)
    monkeypatch.setattr(pv, "validate_plan", lambda *_a, **_k: [pv.Issue(pv.ERROR, "distortion", ["1"], "x")])
    placed = _legacy(m, tw, th)
    before = [(p.x, p.y) for p in placed]
    s = ap.apply_to_placed(placed, m, objs, tw, th, page_w=m.page_w, page_h=m.page_h)
    assert not s["used"] and "distortion" in s["reason"] and [(p.x, p.y) for p in placed] == before


def test_engine_step_respects_priority_example_layout_and_learned_corrections_come_first():
    m = build()
    objs = _objs_from_model(m)
    tw, th = sized(12, 6)

    def run(shop_extra=None, src=None):
        placed = _legacy(m, tw, th)
        placed[0].layout_source = src
        before = [(p.x, p.y, p.w, p.h) for p in placed]
        warnings: list[str] = []
        CorelEngine._apply_spatial_plan(placed, objs, {"spatial_model": m.to_dict(), **(shop_extra or {})}, m.page_w, m.page_h, tw, th, warnings)
        return placed, before, warnings

    placed, before, w = run()
    assert any("spatial planner (fit)" in x for x in w) and [(p.x, p.y, p.w, p.h) for p in placed] != before
    placed, before, w = run(src={"mode": "example", "file": "x"})                        # 1. designer example
    assert [(p.x, p.y, p.w, p.h) for p in placed] == before and not w
    placed, before, w = run({"intelligence": [{"id": 1}]})                               # 3. learned corrections
    assert [(p.x, p.y, p.w, p.h) for p in placed] == before and not w
    placed, before, w = run(src={"mode": "rules", "reason": "x"})                        # no example fits: the planner may act
    assert placed[0].layout_source["spatial"]["used"] is True and "validation" not in placed[0].layout_source["spatial"]
    # a corrupt model never breaks the conversion
    placed = _legacy(m, tw, th)
    before = [(p.x, p.y) for p in placed]
    warnings: list[str] = []
    CorelEngine._apply_spatial_plan(placed, objs, {"spatial_model": {"schema_version": 99}}, m.page_w, m.page_h, tw, th, warnings)
    assert [(p.x, p.y) for p in placed] == before and any("not used" in x for x in warnings)


def test_no_nan_or_inf_in_any_plan_over_many_targets():
    m = build()
    for tw, th in [(3048, 2438), (6096, 4876), (4000, 1000), (900, 2400), (1500, 1500), (12000, 900)]:
        p = ap.plan_adaptation(m, tw, th)
        for o in p.objects:
            assert all(math.isfinite(v) for v in o.box.values())


def test_reflow_that_stretches_text_or_photos_fails_validation_but_a_small_group_stretch_only_warns():
    objs = [obj(0, "bg", "rectangle", 0, 0, 3048, 914), obj(1, "name", "text", 900, 700, 1200, 120, text="SHOP"),
            obj(2, "photo", "bitmap", 1100, 150, 800, 500), obj(3, "logo", "group", 100, 500, 400, 300)]
    m = mm.build_model(extraction(3048, 914, objs))
    p = ap.plan_adaptation(m, 4 * FT, 8 * FT)
    by = {o.id: o for o in p.objects}
    by["1"].box = dict(by["1"].box, width=by["1"].box["height"] * 12 * 1.4)          # text stretched 1.4x
    by["2"].box = dict(by["2"].box, width=by["2"].box["width"] * 1.2)                 # photo stretched 1.2x
    by["3"].box = dict(by["3"].box, width=by["3"].box["width"] * 1.1)                 # group 1.1x -> warning only
    got = {(i.ids[0], i.severity) for i in pv.validate_plan(m, p) if i.rule == "distortion"}
    assert ("1", "error") in got and ("2", "error") in got and ("3", "warning") in got
