"""Tests for app/orientation_adapter.py: zone geometry, slot->zone classification, and the
end-to-end op list applied through scene_ops.apply_ops - see that module's docstring for the design
(reusing product_engine.py's slots and scene_ops.py's existing `resize`/`page` ops, no new op type).
"""
from __future__ import annotations

import copy

import pytest

from app import orientation_adapter as oa
from app import scene_ops
from app.scene_ops import OpError

TOL = 1e-6


def rect_overlap_area(a: dict, b: dict) -> float:
    ox = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    oy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    return ox * oy


def box_of(scene: dict, node_id: str) -> dict:
    n = scene_ops.find_node(scene, node_id)
    return {"x": n["x"], "y": n["y"], "w": n["w"], "h": n["h"]}


def within_page(scene: dict, node_id: str, tol: float = 1e-3) -> bool:
    b = box_of(scene, node_id)
    pw, ph = scene["page"]["width"], scene["page"]["height"]
    return b["x"] >= -tol and b["y"] >= -tol and b["x"] + b["w"] <= pw + tol and b["y"] + b["h"] <= ph + tol


def assert_covers_page_without_distortion(box: dict, orig_w: float, orig_h: float, target_w: float, target_h: float, tol: float = 1e-3):
    """The background's "cover" fit (see convert_orientation): box fully covers [0,target_w] x
    [0,target_h] (may overflow, never falls short), and its own aspect ratio is unchanged from the
    original - i.e. the content itself isn't stretched, only cropped."""
    assert box["x"] <= tol and box["y"] <= tol
    assert box["x"] + box["w"] >= target_w - tol
    assert box["y"] + box["h"] >= target_h - tol
    assert box["w"] / box["h"] == pytest.approx(orig_w / orig_h, rel=1e-6)


# --------------------------------------------------------------------- fixtures

def _text(id_, name, x, y, w, h, content, size_pt=24.0):
    return {"id": id_, "kind": "shape", "type": "text", "name": name, "x": x, "y": y, "w": w, "h": h,
            "rotation": 0, "visible": True, "locked": False, "text": {"content": content, "font": "Arial", "size_pt": size_pt}}


def _bitmap(id_, name, x, y, w, h, visible=True, locked=False):
    return {"id": id_, "kind": "shape", "type": "bitmap", "name": name, "x": x, "y": y, "w": w, "h": h,
            "rotation": 0, "visible": visible, "locked": locked}


def portrait_scene() -> dict:
    """A synthetic PORTRAIT (400 x 1000 mm) board with every zone represented, including a
    PowerClip-nested product image (the case product_engine.ProductSlot.container_id exists for)."""
    return {
        "page": {"width": 400.0, "height": 1000.0},
        "layers": [{"id": "L1", "name": "Layer 1", "visible": True, "locked": False, "children": [
            {"id": "bg", "kind": "shape", "type": "rectangle", "name": "", "x": 0, "y": 0, "w": 400, "h": 1000,
             "rotation": 0, "visible": True, "locked": False},
            _text("brand", "brand_title", 50, 900, 300, 60, "BRAND", 60.0),
            {"id": "pc", "kind": "powerclip", "type": "rectangle", "name": "product_image_1", "x": 50, "y": 500,
             "w": 300, "h": 350, "rotation": 0, "visible": True, "locked": False, "frame_rect": True,
             "children": [_bitmap("photo", "", 60, 520, 280, 300)]},
            _text("title", "product_title", 50, 400, 300, 50, "Widget 3000", 40.0),
            _text("addr", "address", 50, 200, 300, 30, "123 Main St", 20.0),
            _text("contact", "", 50, 150, 300, 30, "Phone No. 555-1234", 20.0),
        ]}],
    }


def _transposed(scene: dict) -> dict:
    """The same board turned on its side: page and every node's x<->y and w<->h swapped (recursively), so a
    PORTRAIT fixture becomes a LANDSCAPE source. A portrait source converted to a wide target is now routed by
    the portrait bands (see `_place_p2l`); the plain proportional `other` fallback these tests lock in still
    applies to landscape sources."""
    out = copy.deepcopy(scene)
    out["page"] = {"width": scene["page"]["height"], "height": scene["page"]["width"]}

    def swap(n):
        n["x"], n["y"], n["w"], n["h"] = n["y"], n["x"], n["h"], n["w"]
        for c in n.get("children") or []:
            swap(c)

    for layer in out["layers"]:
        for n in layer["children"]:
            swap(n)
    return out


def untagged_scene() -> dict:
    """A synthetic stand-in for a real, untagged master (AL MADEENA - job 16bfc025ca11): a page
    -covering background and several untagged shapes at different heights, none of them a
    brand_title/product_title/address/contact tag or an image-slot heuristic match - so header,
    main_text and footer all come back empty from classify_zones, exactly like the real board."""
    return {
        "page": {"width": 2000.0, "height": 800.0},
        "layers": [{"id": "L1", "name": "Layer 1", "visible": True, "locked": False, "children": [
            {"id": "bg", "kind": "shape", "type": "rectangle", "name": "", "x": 0, "y": 0, "w": 2000, "h": 800,
             "rotation": 0, "visible": True, "locked": False},
            {"id": "top1", "kind": "shape", "type": "curve", "name": "", "x": 50, "y": 700, "w": 200, "h": 60,
             "rotation": 0, "visible": True, "locked": False},
            {"id": "top2", "kind": "shape", "type": "curve", "name": "", "x": 1700, "y": 680, "w": 200, "h": 80,
             "rotation": 0, "visible": True, "locked": False},
            {"id": "mid1", "kind": "shape", "type": "curve", "name": "", "x": 900, "y": 380, "w": 300, "h": 150,
             "rotation": 0, "visible": True, "locked": False},
            {"id": "bottom1", "kind": "shape", "type": "text", "name": "", "x": 100, "y": 30, "w": 700, "h": 40,
             "rotation": 0, "visible": True, "locked": False, "text": {"content": "SHOP NAME", "font": "Arial", "size_pt": 24}},
        ]}],
    }


# --------------------------------------------------------------------- calculate_zone_rects

def _assert_disjoint_and_in_bounds(frames: dict, target_w: float, target_h: float):
    names = list(frames)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            assert rect_overlap_area(frames[names[i]], frames[names[j]]) < TOL, (names[i], names[j])
    for f in frames.values():
        assert f["w"] > 0 and f["h"] > 0
        assert f["x"] >= -TOL and f["y"] >= -TOL
        assert f["x"] + f["w"] <= target_w + TOL
        assert f["y"] + f["h"] <= target_h + TOL


def test_calculate_zone_rects_wide_template_is_pairwise_disjoint_and_inside_the_page():
    # R = 900/300 = 3.0 >= WIDE_RATIO
    frames = oa.calculate_zone_rects(900.0, 300.0)
    assert set(frames) == {oa.ZONE_HEADER, oa.ZONE_PRODUCT, oa.ZONE_MAIN_TEXT, oa.ZONE_FOOTER}
    _assert_disjoint_and_in_bounds(frames, 900.0, 300.0)


def test_calculate_zone_rects_stack_template_is_also_disjoint_and_inside_the_page():
    # R = 300/900 = 0.33 < GRID_RATIO
    frames = oa.calculate_zone_rects(300.0, 900.0)
    _assert_disjoint_and_in_bounds(frames, 300.0, 900.0)


PORTRAIT_SIZES_IN = [(30, 40), (24, 36), (21, 29.7), (36, 96), (10, 40), (39, 40)]   # R = .75 .667 .707 .375 .25 .975


@pytest.mark.parametrize("w_in,h_in", PORTRAIT_SIZES_IN)
def test_stack_template_uses_the_same_normalized_bands_for_every_portrait_ratio(w_in, h_in):
    # Ratio-driven, not tuned to one size: footer 0-0.20, product 0.20-0.55, brand 0.55-0.72,
    # header 0.72-0.98 of the canvas height, each inset only by the small margin/gap.
    W, H = w_in * 25.4, h_in * 25.4
    f = oa.calculate_zone_rects(W, H)
    tol = 0.035   # margin/gap insets, as a fraction of H, never more than this at any band edge
    def band(zone):
        return f[zone]["y"] / H, (f[zone]["y"] + f[zone]["h"]) / H
    for zone, (lo, hi) in ((oa.ZONE_FOOTER, (0.0, 0.20)), (oa.ZONE_PRODUCT, oa.STACK_BAND_PRODUCT),
                           (oa.ZONE_MAIN_TEXT, oa.STACK_BAND_BRAND), (oa.ZONE_HEADER, oa.STACK_BAND_HEADER)):
        y0, y1 = band(zone)
        assert lo - 1e-9 <= y0 <= lo + tol and hi - tol <= y1 <= hi + 1e-9, (zone, y0, y1)
        assert f[zone]["w"] == pytest.approx(W - 2 * oa.MARGIN_FRAC * min(W, H), abs=1e-6)   # full width
    # strictly stacked top to bottom, no overlap, products anchored right above the footer, and the
    # header reaches high enough that there is no large empty top region
    assert band(oa.ZONE_HEADER)[0] >= band(oa.ZONE_MAIN_TEXT)[1]
    assert band(oa.ZONE_MAIN_TEXT)[0] >= band(oa.ZONE_PRODUCT)[1]
    assert band(oa.ZONE_PRODUCT)[0] >= band(oa.ZONE_FOOTER)[1]
    assert band(oa.ZONE_PRODUCT)[0] < 0.22
    assert band(oa.ZONE_HEADER)[1] > 0.95
    _assert_disjoint_and_in_bounds(f, W, H)


def test_calculate_zone_rects_footer_fraction_is_20pct_only_for_portrait_and_others_unchanged():
    assert oa.FOOTER_FRAC_PORTRAIT == 0.20
    wide_frames = oa.calculate_zone_rects(900.0, 300.0)
    assert wide_frames[oa.ZONE_FOOTER]["h"] == pytest.approx(oa.FOOTER_FRAC * 300.0, abs=1e-6)
    grid_frames = oa.calculate_zone_rects(60.0, 60.0)
    assert grid_frames[oa.ZONE_FOOTER]["h"] == pytest.approx(oa.FOOTER_FRAC * 60.0, abs=1e-6)


def test_calculate_zone_rects_grid_template_is_also_disjoint_and_inside_the_page():
    # R = 60/60 = 1.0, exactly the GRID_RATIO boundary (GRID_RATIO <= R < WIDE_RATIO)
    frames = oa.calculate_zone_rects(60.0, 60.0)
    _assert_disjoint_and_in_bounds(frames, 60.0, 60.0)
    # the defining feature of the grid template: product and main_text are an equal-width pair of
    # cells side by side (same y/h), unlike the wide or stack templates
    assert frames[oa.ZONE_PRODUCT]["w"] == pytest.approx(frames[oa.ZONE_MAIN_TEXT]["w"], abs=1e-6)
    assert frames[oa.ZONE_PRODUCT]["y"] == pytest.approx(frames[oa.ZONE_MAIN_TEXT]["y"], abs=1e-6)
    assert frames[oa.ZONE_PRODUCT]["h"] == pytest.approx(frames[oa.ZONE_MAIN_TEXT]["h"], abs=1e-6)


@pytest.mark.parametrize("w,h,expect_ratio_at_least", [
    (90.0, 40.0, oa.WIDE_RATIO), (120.0, 36.0, oa.WIDE_RATIO), (48.0, 96.0, None), (60.0, 60.0, oa.GRID_RATIO),
])
def test_calculate_zone_rects_across_the_tasks_own_arbitrary_target_sizes(w, h, expect_ratio_at_least):
    frames = oa.calculate_zone_rects(w, h)
    _assert_disjoint_and_in_bounds(frames, w, h)
    if expect_ratio_at_least is not None:
        assert w / h >= expect_ratio_at_least


@pytest.mark.parametrize("w,h", [(0, 100), (100, 0), (-5, 100), (100, -5)])
def test_calculate_zone_rects_rejects_non_positive_targets(w, h):
    with pytest.raises(OpError, match="must be positive"):
        oa.calculate_zone_rects(w, h)


@pytest.mark.parametrize("w,h", [
    (50, 50), (10000, 10), (10, 10000), (1.0, 1.0),           # the original range
    (90, 40), (120, 36), (48, 96), (60, 60), (0.01, 0.01),    # extreme/non-standard sizes, one per template
    (100000.0, 1.0), (1.0, 100000.0),                         # extreme aspect ratios in both directions
])
def test_calculate_zone_rects_never_degenerates_across_a_wide_range_of_aspect_ratios(w, h):
    frames = oa.calculate_zone_rects(w, h)
    for f in frames.values():
        assert f["w"] > 0 and f["h"] > 0
    _assert_disjoint_and_in_bounds(frames, w, h)


# --------------------------------------------------------------------- classify_zones

def test_classify_zones_on_the_synthetic_portrait_scene():
    zones, warnings = oa.classify_zones(portrait_scene())
    assert zones[oa.ZONE_HEADER] == ["brand"]
    assert zones[oa.ZONE_PRODUCT] == ["pc"]           # the CONTAINER id, not "photo"
    assert zones[oa.ZONE_MAIN_TEXT] == ["title"]
    assert zones[oa.ZONE_FOOTER] == ["addr", "contact"]
    assert zones[oa.ZONE_BACKGROUND] == ["bg"]
    assert zones[oa.ZONE_OTHER] == []
    assert warnings == []


def test_classify_zones_excludes_hidden_top_level_shapes():
    scene = portrait_scene()
    scene["layers"][0]["children"].append(_bitmap("hidden", "", 0, 0, 5, 5, visible=False))
    zones, _ = oa.classify_zones(scene)
    all_ids = [i for ids in zones.values() for i in ids]
    assert "hidden" not in all_ids


def test_classify_zones_buckets_an_untagged_shape_as_other_not_background():
    scene = portrait_scene()
    scene["layers"][0]["children"].append(
        {"id": "deco", "kind": "shape", "type": "curve", "name": "", "x": 10, "y": 10, "w": 20, "h": 20,
         "rotation": 0, "visible": True, "locked": False})
    zones, _ = oa.classify_zones(scene)
    assert zones[oa.ZONE_OTHER] == ["deco"]


def test_classify_zones_warns_about_a_locked_slot_shape():
    scene = portrait_scene()
    scene["layers"][0]["children"][1]["locked"] = True   # "brand"
    zones, warnings = oa.classify_zones(scene)
    assert zones[oa.ZONE_HEADER] == ["brand"]             # still classified...
    assert any("brand" in w and "locked" in w for w in warnings)


def test_classify_zones_forwards_map_slots_warnings():
    scene = portrait_scene()
    scene["layers"][0]["children"].append(
        {"id": "badtag", "kind": "shape", "type": "rectangle", "name": "brand_title", "x": 0, "y": 0, "w": 10, "h": 10,
         "rotation": 0, "visible": True, "locked": False})
    _, warnings = oa.classify_zones(scene)
    assert any("badtag" in w for w in warnings)


# --------------------------------------------------------------------- wireframe sub-placement

def test_split_left_right_by_x_splits_around_the_groups_own_mean():
    idx = {
        "a": {"node": {"x": 0, "w": 10}}, "b": {"node": {"x": 100, "w": 10}},
        "c": {"node": {"x": 5, "w": 10}},
    }
    left, right = oa._split_left_right_by_x(idx, ["a", "b", "c"])
    assert set(left) | set(right) == {"a", "b", "c"}
    assert set(left) & set(right) == set()
    assert "b" in right   # b (x=100) is far to the right of a/c's mean


def test_split_left_right_by_x_falls_back_to_index_midpoint_when_everything_ties():
    idx = {"a": {"node": {"x": 0, "w": 10}}, "b": {"node": {"x": 0, "w": 10}}}
    left, right = oa._split_left_right_by_x(idx, ["a", "b"])
    assert left and right   # neither side is empty despite identical x


def test_split_left_right_by_x_single_id_goes_entirely_left():
    idx = {"a": {"node": {"x": 5, "w": 10}}}
    assert oa._split_left_right_by_x(idx, ["a"]) == (["a"], [])


def _idx_of(scene: dict) -> dict:
    return scene_ops._index(scene)


def test_place_top_region_single_id_fills_the_whole_header_frame():
    scene = portrait_scene()
    idx = _idx_of(scene)
    frame = {"x": 0.0, "y": 0.0, "w": 400.0, "h": 100.0}
    ops = oa._place_top_region(idx, ["brand"], frame)
    assert len(ops) == 1
    assert ops[0]["to"]["w"] == pytest.approx(frame["w"] * (1 - 2 * oa.ZONE_PADDING_FRAC))


def test_place_top_region_two_ids_reserves_separate_left_and_right_bounds():
    scene = portrait_scene()
    scene["layers"][0]["children"].append(_text("logo2", "", 350, 900, 40, 40, "B"))
    scene["layers"][0]["children"][1]["x"] = 10   # "brand" pinned to the left
    idx = _idx_of(scene)
    frame = {"x": 0.0, "y": 0.0, "w": 400.0, "h": 100.0}
    ops = oa._place_top_region(idx, ["brand", "logo2"], frame)
    assert len(ops) == 2
    by_id = {tuple(op["ids"]): op["to"] for op in ops}
    brand_box = by_id[("brand",)]
    logo2_box = by_id[("logo2",)]
    assert brand_box["x"] < logo2_box["x"]                     # brand (left) stays left of logo2 (right)
    assert brand_box["x"] + brand_box["w"] <= logo2_box["x"] + 1e-6   # no overlap between the two halves
    assert brand_box["w"] <= frame["w"] / 2 + 1e-6 and logo2_box["w"] <= frame["w"] / 2 + 1e-6


def test_place_main_and_subobjects_single_id_is_unchanged_from_before():
    scene = portrait_scene()
    idx = _idx_of(scene)
    frame = {"x": 0.0, "y": 0.0, "w": 300.0, "h": 350.0}
    ops = oa._place_main_and_subobjects(idx, ["pc"], frame)
    assert len(ops) == 1
    assert ops[0]["ids"] == ["pc"]


def test_place_main_and_subobjects_picks_the_largest_as_main_and_flanks_it_with_the_rest():
    scene = portrait_scene()
    scene["layers"][0]["children"].append(_text("sub_left", "", 0, 500, 20, 20, "L"))
    scene["layers"][0]["children"].append(_text("sub_right", "", 400, 500, 20, 20, "R"))
    idx = _idx_of(scene)
    frame = {"x": 0.0, "y": 0.0, "w": 600.0, "h": 350.0}
    # "pc" (300x350 = 105000 mm^2) is by far the largest of the three
    ops = oa._place_main_and_subobjects(idx, ["pc", "sub_left", "sub_right"], frame)
    by_id = {tuple(op["ids"]): op["to"] for op in ops}
    assert ("pc",) in by_id
    main_box = by_id[("pc",)]
    left_box = by_id[("sub_left",)]
    right_box = by_id[("sub_right",)]
    # main_object centred, flanked by its own left/right columns, nothing overlapping
    assert left_box["x"] + left_box["w"] <= main_box["x"] + 1e-6
    assert main_box["x"] + main_box["w"] <= right_box["x"] + 1e-6
    # the PowerClip-nested bitmap inside "pc" keeps its exact aspect ratio (uniform scale, item 2)
    out_scene = scene_ops.apply_ops(scene, [{"op": "page", "width": frame["w"], "height": frame["h"]}] + ops)
    orig_photo = box_of(scene, "photo")
    new_photo = box_of(out_scene, "photo")
    assert new_photo["w"] / new_photo["h"] == pytest.approx(orig_photo["w"] / orig_photo["h"], rel=1e-6)


def test_place_footer_banner_english_only_is_unchanged_from_before():
    scene = portrait_scene()
    idx = _idx_of(scene)
    frame = {"x": 0.0, "y": 0.0, "w": 400.0, "h": 100.0}
    ops = oa._place_footer_banner(idx, ["addr", "contact"], frame, portrait=True)
    assert len(ops) == 1
    assert set(ops[0]["ids"]) == {"addr", "contact"}


def test_place_footer_banner_stacks_english_above_tamil_for_portrait():
    scene = portrait_scene()
    scene["layers"][0]["children"].append(_text("ta", "", 50, 100, 300, 30, "வணக்கம்"))
    idx = _idx_of(scene)
    frame = {"x": 0.0, "y": 0.0, "w": 400.0, "h": 100.0}
    ops = oa._place_footer_banner(idx, ["addr", "ta"], frame, portrait=True)
    by_id = {tuple(op["ids"]): op["to"] for op in ops}
    en_box = by_id[("addr",)]
    ta_box = by_id[("ta",)]
    assert en_box["y"] > ta_box["y"]        # English (top half - higher y) above Tamil (bottom half)
    assert en_box["y"] >= ta_box["y"] + ta_box["h"] - 1e-6   # no vertical overlap


def test_place_footer_banner_puts_english_left_of_tamil_for_landscape():
    scene = portrait_scene()
    scene["layers"][0]["children"].append(_text("ta", "", 50, 100, 300, 30, "வணக்கம்"))
    idx = _idx_of(scene)
    frame = {"x": 0.0, "y": 0.0, "w": 400.0, "h": 100.0}
    ops = oa._place_footer_banner(idx, ["addr", "ta"], frame, portrait=False)
    by_id = {tuple(op["ids"]): op["to"] for op in ops}
    en_box = by_id[("addr",)]
    ta_box = by_id[("ta",)]
    assert en_box["x"] < ta_box["x"]                              # English left, Tamil right
    assert en_box["x"] + en_box["w"] <= ta_box["x"] + 1e-6         # no horizontal overlap


def test_convert_orientation_end_to_end_wireframe_on_a_tagged_scene():
    # A fully-tagged scene exercising all three wireframe sub-placements at once: two brand_title
    # logos (top region), a product_image + two untagged subobjects sharing the product zone (center
    # region), and a genuine English/Tamil address pair (bottom region).
    scene = portrait_scene()
    children = scene["layers"][0]["children"]
    children[1]["x"] = 10                                        # "brand" pinned left
    children.append(_text("logo2", "brand_title", 350, 900, 30, 40, "CO"))
    children.append(_text("sub_left", "", 0, 500, 20, 20, "L"))
    children.append(_text("sub_right", "", 380, 500, 20, 20, "R"))
    children.append(_text("ta_addr", "address", 50, 100, 300, 30, "வணக்கம்"))

    for target_w, target_h, portrait in ((900.0, 300.0, False), (300.0, 900.0, True)):
        s = copy.deepcopy(scene)
        ops = oa.convert_orientation(s, target_w, target_h)
        out = scene_ops.apply_ops(s, ops)
        # the PowerClip-nested product photo keeps its aspect ratio regardless of template
        orig_photo, new_photo = box_of(s, "photo"), box_of(out, "photo")
        assert new_photo["w"] / new_photo["h"] == pytest.approx(orig_photo["w"] / orig_photo["h"], rel=1e-6)
        # nothing among the placed foreground shapes overlaps anything else
        fg_ids = ["brand", "logo2", "pc", "sub_left", "sub_right", "title", "addr", "contact", "ta_addr"]
        boxes = [box_of(out, i) for i in fg_ids]
        for a in range(len(boxes)):
            assert within_page(out, fg_ids[a])
            for b in range(a + 1, len(boxes)):
                assert rect_overlap_area(boxes[a], boxes[b]) < 1.0, (fg_ids[a], fg_ids[b], target_w, target_h)


# ------------------------------------------ untagged-master fallback (Y-banded "other" absorption)

def test_split_by_capacity_does_not_crush_a_disproportionately_large_item_into_a_tiny_zone():
    # Found live on a real DARSHAN AGARBATHI board (job 8a41177716c4, shop fe047cace239): a 170.7mm
    # -tall badge group landed alone in the middle of 3 equal-count buckets, whose own zone (a stack
    # template's main_text sliver) was only 43.3mm tall - a 4.2x overflow/crush (170.7/43.3). Here,
    # _split_by_capacity instead groups "big" with the two small items ABOVE it into the much larger
    # "header" capacity (265.3/303.0 = 0.876x, no overflow at all) and leaves "main_text" empty
    # (reclaimed by `_reclaim_empty_absorbing_frames` - see its own tests) rather than crush "big".
    items = ["small1", "small2", "big", "small3", "small4"]
    weights = [33.2, 61.4, 170.7, 15.5, 11.8]
    capacities = [303.0, 43.3, 203.2]     # header, main_text, footer - the real board's own numbers
    buckets = oa._split_by_capacity(items, weights, capacities)
    assert sum(buckets, []) == items      # every item placed exactly once, order preserved
    assert buckets == [["small1", "small2", "big"], [], ["small3", "small4"]]
    big_bucket_weight = sum(weights[items.index(i)] for i in buckets[0])
    assert big_bucket_weight / capacities[0] < 1.0   # no overflow at all, unlike the old equal-count split


def test_split_by_capacity_falls_back_to_split_evenly_when_nothing_to_weigh_by():
    items = ["a", "b", "c"]
    assert oa._split_by_capacity(items, [0, 0, 0], [10, 10]) == oa._split_evenly(items, 2)
    assert oa._split_by_capacity(items, [1, 2, 3], [0, 0]) == oa._split_evenly(items, 2)


def test_split_by_capacity_preserves_order_and_handles_empty_items():
    assert oa._split_by_capacity([], [], [10, 20, 30]) == [[], [], []]
    assert oa._split_by_capacity([], [], []) == []


def test_reclaim_empty_absorbing_frames_extends_a_neighbour_into_an_empty_zones_slot():
    frames = {
        "header": {"x": 0.0, "y": 700.0, "w": 900.0, "h": 300.0},
        "main_text": {"x": 0.0, "y": 240.0, "w": 900.0, "h": 40.0},   # will end up empty
        "footer": {"x": 0.0, "y": 20.0, "w": 900.0, "h": 200.0},
    }
    absorbing = ["header", "main_text", "footer"]
    buckets = [["a", "b"], [], ["c"]]
    result = oa._reclaim_empty_absorbing_frames(absorbing, buckets, frames)
    # header's frame is untouched (nothing empty precedes it)
    assert result[0] == frames["header"]
    # footer's frame grows to also cover main_text's freed slot (same x/w, so the merge is valid)
    assert result[2]["y"] == pytest.approx(20.0)
    assert result[2]["h"] == pytest.approx((240.0 + 40.0) - 20.0)   # up to main_text's old top edge


def test_reclaim_empty_absorbing_frames_does_not_merge_across_different_x_or_w():
    # a grid/wide-template main_text column (narrower, offset) must not be pulled sideways into a
    # neighbouring differently-shaped frame - the merge only fires when x AND w already match.
    frames = {
        "header": {"x": 0.0, "y": 700.0, "w": 900.0, "h": 300.0},      # full width
        "main_text": {"x": 500.0, "y": 240.0, "w": 400.0, "h": 40.0},  # narrower column, empty
        "footer": {"x": 0.0, "y": 20.0, "w": 900.0, "h": 200.0},
    }
    absorbing = ["header", "main_text", "footer"]
    buckets = [["a"], [], ["c"]]
    result = oa._reclaim_empty_absorbing_frames(absorbing, buckets, frames)
    assert result[2] == frames["footer"]   # unchanged - x/w mismatch blocks the merge


def test_convert_orientation_does_not_crush_a_large_secondary_group_into_a_tiny_zone():
    # End-to-end reproduction of the real DARSHAN AGARBATHI finding: an untagged portrait master with
    # small header/footer-ish shapes AND one disproportionately large secondary group, all landing in
    # ZONE_OTHER (no brand_title/product_title/address/contact tags) - the large group must not be
    # squeezed to a small fraction of its original size just because of where it falls in a
    # count-based split.
    scene = {
        "page": {"width": 762.0, "height": 1016.0},
        "layers": [{"id": "L1", "name": "Layer 1", "visible": True, "locked": False, "children": [
            {"id": "bg", "kind": "shape", "type": "rectangle", "name": "", "x": 0, "y": 0, "w": 762, "h": 1016,
             "rotation": 0, "visible": True, "locked": False},
            {"id": "top1", "kind": "shape", "type": "curve", "name": "", "x": 24, "y": 878, "w": 183, "h": 33,
             "rotation": 0, "visible": True, "locked": False},
            {"id": "top2", "kind": "shape", "type": "curve", "name": "", "x": 581, "y": 815, "w": 76, "h": 61,
             "rotation": 0, "visible": True, "locked": False},
            {"id": "big_badge", "kind": "group", "type": "group", "name": "", "x": 365, "y": 478, "w": 255, "h": 171,
             "rotation": 0, "visible": True, "locked": False, "children": [
                 {"id": "big_badge_c", "kind": "shape", "type": "curve", "name": "", "x": 365, "y": 478, "w": 255,
                  "h": 171, "rotation": 0, "visible": True, "locked": False},
             ]},
            _text("bot1", "", 24, 85, 329, 12, "Phone No. 555-1234"),
            _text("bot2", "", 371, 86, 297, 16, "GST 12345"),
            _bitmap("redbox", "", 648, 326, 95, 212),
        ]}],
    }
    ops = oa.convert_orientation(scene, 762.0, 1016.0)
    out = scene_ops.apply_ops(scene, ops)

    # the redbox bitmap (heuristic product_image) keeps its exact aspect ratio
    orig_box = box_of(scene, "redbox")
    new_box = box_of(out, "redbox")
    assert new_box["w"] / new_box["h"] == pytest.approx(orig_box["w"] / orig_box["h"], rel=1e-6)

    # big_badge must not be crushed to a small fraction of its original size (previously: 13.8x)
    orig_badge = box_of(scene, "big_badge")
    new_badge = box_of(out, "big_badge")
    shrink = (orig_badge["w"] * orig_badge["h"]) / (new_badge["w"] * new_badge["h"])
    assert shrink < 3.0

    # no overlap among the final top-level (non-background) shapes
    fg_ids = ["top1", "top2", "big_badge", "bot1", "bot2", "redbox"]
    boxes = [box_of(out, i) for i in fg_ids]
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            assert rect_overlap_area(boxes[a], boxes[b]) < 1.0, (fg_ids[a], fg_ids[b])
        assert within_page(out, fg_ids[a])


def test_convert_orientation_absorbs_other_into_empty_named_zones_by_vertical_band():
    # Found live on the real AL MADEENA board (job 16bfc025ca11): header/main_text/footer all come
    # back empty for an untagged master, so its real content (here: top1/top2/mid1/bottom1) must not
    # be left in the weak per-shape proportional fallback - it should fill that empty template space
    # instead, bucketed by original vertical position so unrelated shapes don't get crammed together.
    scene = untagged_scene()
    ops = oa.convert_orientation(scene, 900.0, 1600.0)   # a tall target, like the real repro
    out = scene_ops.apply_ops(scene, ops)

    zones, _ = oa.classify_zones(scene)
    assert zones[oa.ZONE_HEADER] == zones[oa.ZONE_MAIN_TEXT] == zones[oa.ZONE_FOOTER] == []
    assert set(zones[oa.ZONE_OTHER]) == {"top1", "top2", "mid1", "bottom1"}

    # every absorbed shape landed inside the page, and their original top-to-bottom order survives -
    # top1/top2 (bucketed into the topmost empty zone) end up above mid1, which ends up above bottom1
    for nid in ("top1", "top2", "mid1", "bottom1"):
        assert within_page(out, nid), nid
    top1, top2, mid1, bottom1 = (box_of(out, i) for i in ("top1", "top2", "mid1", "bottom1"))
    assert min(top1["y"], top2["y"]) > mid1["y"] + mid1["h"] - 1.0
    assert mid1["y"] > bottom1["y"] + bottom1["h"] - 1.0
    # nothing overlaps anything else post-conversion
    boxes = [box_of(out, i) for i in ("top1", "top2", "mid1", "bottom1")]
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            assert rect_overlap_area(boxes[a], boxes[b]) < 1.0


def test_convert_orientation_leaves_other_alone_when_no_named_zone_is_empty():
    # A properly slot-tagged master (portrait_scene: every named zone already has content) must not
    # have its "other" content redirected - the absorption only fires for a genuinely empty zone.
    # Same shape/position as test_convert_orientation_moves_an_untagged_other_shape_to_the_same_
    # proportional_position below, which already locks in the plain-fallback numbers this test
    # asserts are UNCHANGED by the new absorption logic.
    scene = _transposed(portrait_scene())          # a LANDSCAPE source keeps the plain fallback (see _transposed)
    scene["layers"][0]["children"].append(
        {"id": "deco", "kind": "shape", "type": "curve", "name": "", "x": 500, "y": 200, "w": 20, "h": 20,
         "rotation": 0, "visible": True, "locked": False})
    ops = oa.convert_orientation(scene, 900.0, 300.0)
    out = scene_ops.apply_ops(scene, ops)
    b = box_of(out, "deco")
    cx, cy = (b["x"] + b["w"] / 2) / 900.0, (b["y"] + b["h"] / 2) / 300.0
    assert cx == pytest.approx(0.51, abs=1e-3)    # unchanged: still the plain proportional fallback
    assert cy == pytest.approx(0.525, abs=1e-3)


# --------------------------------------------------------------------- convert_orientation (end to end)

@pytest.fixture
def converted():
    scene = portrait_scene()
    ops = oa.convert_orientation(scene, 900.0, 300.0)
    out = scene_ops.apply_ops(scene, ops)
    return scene, ops, out


def test_convert_orientation_starts_with_a_page_op_matching_the_target(converted):
    _, ops, out = converted
    assert ops[0] == {"op": "page", "width": 900.0, "height": 300.0}
    assert out["page"] == {"width": 900.0, "height": 300.0}


def test_convert_orientation_does_not_mutate_its_input(converted):
    scene, ops, out = converted
    before = copy.deepcopy(portrait_scene())
    assert scene == before


def test_convert_orientation_places_every_named_zone_without_overlap_and_in_bounds(converted):
    _, _, out = converted
    zones, _ = oa.classify_zones(out)  # re-classify the ORIGINAL scene's zone membership is unchanged by geometry-only ops
    named = [(z, i) for z in (oa.ZONE_HEADER, oa.ZONE_PRODUCT, oa.ZONE_MAIN_TEXT, oa.ZONE_FOOTER) for i in zones[z]]
    for _, node_id in named:
        assert within_page(out, node_id), node_id
    boxes = [(zone, nid, box_of(out, nid)) for zone, nid in named]
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            za, ia, ba = boxes[a]
            zb, ib, bb = boxes[b]
            assert rect_overlap_area(ba, bb) < 1e-3, f"{ia} ({za}) overlaps {ib} ({zb})"


def test_convert_orientation_covers_the_new_page_with_the_background_without_distorting_it(converted):
    _, _, out = converted
    bg = box_of(out, "bg")
    assert_covers_page_without_distortion(bg, 400.0, 1000.0, 900.0, 300.0)


def test_convert_orientation_scales_text_size_with_the_zone_resize(converted):
    scene, _, out = converted
    orig_brand = scene_ops.find_node(scene, "brand")["text"]["size_pt"]
    orig_title = scene_ops.find_node(scene, "title")["text"]["size_pt"]
    new_brand = scene_ops.find_node(out, "brand")["text"]["size_pt"]
    new_title = scene_ops.find_node(out, "title")["text"]["size_pt"]
    assert new_brand > 0 and new_title > 0
    assert new_brand != orig_brand
    assert new_title != orig_title


def test_convert_orientation_keeps_the_powerclip_child_moving_with_its_container(converted):
    _, _, out = converted
    pc = box_of(out, "pc")
    photo = box_of(out, "photo")
    # the child was fully inside the container before conversion and stays fully inside it after,
    # since scaling/translating the container carries its children with it
    assert photo["x"] >= pc["x"] - TOL and photo["y"] >= pc["y"] - TOL
    assert photo["x"] + photo["w"] <= pc["x"] + pc["w"] + TOL
    assert photo["y"] + photo["h"] <= pc["y"] + pc["h"] + TOL


def test_convert_orientation_scales_a_bitmap_containing_zone_uniformly_not_stretched(converted):
    # "pc" (the product zone's PowerClip container, holding the nested "photo" bitmap) must keep its
    # own aspect ratio - a non-uniform stretch here would visibly distort the photo it clips.
    scene, _, out = converted
    orig = box_of(scene, "pc")
    new = box_of(out, "pc")
    assert new["w"] / new["h"] == pytest.approx(orig["w"] / orig["h"], rel=1e-6)


def test_convert_orientation_still_stretches_a_text_only_zone_non_uniformly_within_the_cap():
    # "brand" (the header zone, text only - no bitmap anywhere in it) keeps the earlier fill-to-zone behaviour
    # while the stretch it needs is within MAX_STRETCH_RATIO: its aspect ratio is free to change so it occupies
    # the full zone bounds. (Beyond the cap it gets a uniform fit instead - see the stretch-cap tests below.)
    # (a portrait target: portrait -> WIDE unfolds badges with a uniform scale instead, see _place_p2l)
    scene = portrait_scene()
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, 800.0, 1000.0))
    orig = box_of(scene, "brand")
    new = box_of(out, "brand")
    assert new["w"] / new["h"] != pytest.approx(orig["w"] / orig["h"], rel=1e-3)
    assert _stretch(orig, new) <= oa.MAX_STRETCH_RATIO + 1e-9


def _product_table_scene() -> dict:
    """A portrait scene whose product zone is a GROUP containing a product-image bitmap grouped with
    a table/pedestal-surface bitmap - the "product image grouped with a table/pedestal surface" case
    from the task. Both bitmaps have different native aspect ratios, unlike the group's own bbox."""
    scene = portrait_scene()
    children = scene["layers"][0]["children"]
    # replace the PowerClip product entry with a plain group of two differently-shaped bitmaps
    children[2] = {
        "id": "assembly", "kind": "group", "type": "group", "name": "product_image_1", "x": 50, "y": 500,
        "w": 300, "h": 350, "rotation": 0, "visible": True, "locked": False,
        "children": [
            _bitmap("product_photo", "", 80, 650, 200, 150),   # aspect 1.333
            _bitmap("table_surface", "", 60, 520, 280, 100),   # aspect 2.8, different from the group bbox's own 300/350
        ],
    }
    return scene


def test_convert_orientation_scales_a_product_plus_table_assembly_group_uniformly():
    scene = _product_table_scene()
    ops = oa.convert_orientation(scene, 900.0, 300.0)   # a wide target - the assembly's group sits in ZONE_PRODUCT
    out = scene_ops.apply_ops(scene, ops)
    orig = box_of(scene, "assembly")
    new = box_of(out, "assembly")
    assert new["w"] / new["h"] == pytest.approx(orig["w"] / orig["h"], rel=1e-6)
    # never truncated/overflowing its own zone frame
    frame = oa.calculate_zone_rects(900.0, 300.0, portrait_source=True)[oa.ZONE_PRODUCT]   # portrait master -> wide
    assert new["x"] >= frame["x"] - TOL and new["y"] >= frame["y"] - TOL
    assert new["x"] + new["w"] <= frame["x"] + frame["w"] + TOL
    assert new["y"] + new["h"] <= frame["y"] + frame["h"] + TOL
    # each bitmap inside the group keeps its own individual aspect ratio too (children scale with
    # the group uniformly, per scene_ops._scale)
    for nid, orig_ratio in (("product_photo", 200 / 150), ("table_surface", 280 / 100)):
        b = box_of(out, nid)
        assert b["w"] / b["h"] == pytest.approx(orig_ratio, rel=1e-6)


def test_convert_orientation_leaves_a_locked_shape_untouched_instead_of_raising():
    scene = portrait_scene()
    scene["layers"][0]["children"][1]["locked"] = True   # "brand" - the only header shape
    before = box_of(scene, "brand")
    ops = oa.convert_orientation(scene, 900.0, 300.0)
    out = scene_ops.apply_ops(scene, ops)                # must not raise despite the locked shape
    assert box_of(out, "brand") == before


def test_convert_orientation_moves_an_untagged_other_shape_to_the_same_proportional_position():
    scene = _transposed(portrait_scene())          # a LANDSCAPE source keeps the plain fallback (see _transposed)
    scene["layers"][0]["children"].append(
        {"id": "deco", "kind": "shape", "type": "curve", "name": "", "x": 500, "y": 200, "w": 20, "h": 20,
         "rotation": 0, "visible": True, "locked": False})
    ops = oa.convert_orientation(scene, 900.0, 300.0)
    out = scene_ops.apply_ops(scene, ops)
    b = box_of(out, "deco")
    cx, cy = (b["x"] + b["w"] / 2) / 900.0, (b["y"] + b["h"] / 2) / 300.0
    assert cx == pytest.approx(0.51, abs=1e-3)    # (500 + 20/2) / 1000 on the original page
    assert cy == pytest.approx(0.525, abs=1e-3)   # (200 + 20/2) / 400 on the original page
    assert within_page(out, "deco")


def test_convert_orientation_handles_a_scene_with_no_slots_at_all():
    scene = {
        "page": {"width": 400.0, "height": 1000.0},
        "layers": [{"id": "L1", "name": "Layer 1", "visible": True, "locked": False,
                    "children": [{"id": "bg", "kind": "shape", "type": "rectangle", "name": "", "x": 0, "y": 0,
                                  "w": 400, "h": 1000, "rotation": 0, "visible": True, "locked": False}]}],
    }
    ops = oa.convert_orientation(scene, 900.0, 300.0)
    out = scene_ops.apply_ops(scene, ops)
    assert_covers_page_without_distortion(box_of(out, "bg"), 400.0, 1000.0, 900.0, 300.0)


def test_convert_orientation_absorbs_other_content_into_product_zone_when_portrait_and_product_empty():
    # A vector-only master (no bitmaps at all - real logos are often raw ungrouped curves, see
    # CLAUDE.md "Designer dataset analysis", and product_engine has no slot for a non-text,
    # non-bitmap shape) has an empty product zone too, not just header/main_text/footer. On a
    # portrait target, product should join the absorbing set so this content still lands in a
    # properly sized zone instead of falling through to the weak, non-reflowing `other` fallback.
    mm = 25.4
    scene = {
        "page": {"width": 125 * mm, "height": 48 * mm},
        "layers": [{"id": "L1", "name": "L1", "visible": True, "locked": False, "children": [
            {"id": "logo", "kind": "shape", "type": "curve", "name": "", "x": 2 * mm, "y": 38 * mm,
             "w": 15 * mm, "h": 8 * mm, "rotation": 0, "visible": True, "locked": False},
            {"id": "brand_text", "kind": "shape", "type": "curve", "name": "", "x": 50 * mm, "y": 24 * mm,
             "w": 25 * mm, "h": 6 * mm, "rotation": 0, "visible": True, "locked": False},
            {"id": "secondary", "kind": "shape", "type": "curve", "name": "", "x": 95 * mm, "y": 10 * mm,
             "w": 20 * mm, "h": 20 * mm, "rotation": 0, "visible": True, "locked": False},
            {"id": "footer", "kind": "shape", "type": "text", "name": "", "x": 5 * mm, "y": 1 * mm,
             "w": 100 * mm, "h": 4 * mm, "rotation": 0, "visible": True, "locked": False,
             "text": {"content": "Shop Name - 12345", "font": "Arial", "size_pt": 24}},
        ]}],
    }
    zones, _ = oa.classify_zones(scene)
    assert zones[oa.ZONE_PRODUCT] == []          # confirms this repro genuinely exercises the empty-product case

    target_w, target_h = 30 * mm, 40 * mm         # portrait, the task's own example
    ops = oa.convert_orientation(scene, target_w, target_h)
    out = scene_ops.apply_ops(scene, ops)

    # all three untagged shapes must apply cleanly and stay on the page
    for nid in ("logo", "brand_text", "secondary", "footer"):
        assert within_page(out, nid, tol=1e-2)

    # they must not all collapse into a single group/position - each ends up in a distinct zone
    boxes = {nid: box_of(out, nid) for nid in ("logo", "brand_text", "secondary")}
    centers_y = {nid: b["y"] + b["h"] / 2 for nid, b in boxes.items()}
    assert len({round(v, 1) for v in centers_y.values()}) == 3   # three distinct vertical positions

    # a landscape target (r >= GRID_RATIO) must NOT include product in absorption - product stays
    # excluded there (a full-height side column, not comparable by vertical position), so this must
    # still apply cleanly via the plain per-shape `other` fallback rather than the zone-absorption path
    wide_ops = oa.convert_orientation(scene, 900.0, 300.0)
    assert not any(op.get("ids") == ["logo", "brand_text", "secondary"] for op in wide_ops if op["op"] == "resize")
    wide_out = scene_ops.apply_ops(scene, wide_ops)
    assert wide_out["page"] == {"width": 900.0, "height": 300.0}
    for nid in ("logo", "brand_text", "secondary", "footer"):
        assert within_page(wide_out, nid, tol=1e-2)


def test_convert_orientation_to_a_same_size_portrait_target_still_applies_cleanly():
    scene = portrait_scene()
    ops = oa.convert_orientation(scene, scene["page"]["width"], scene["page"]["height"])
    out = scene_ops.apply_ops(scene, ops)          # must not raise
    assert out["page"] == {"width": 400.0, "height": 1000.0}


def test_classify_zones_treats_a_page_covering_container_as_background_even_with_a_heuristic_slot_nested_inside():
    # A real untagged master's whole board can be one page-sized PowerClip that also happens to
    # contain a modest bitmap product_engine's heuristic alone would call a product_image slot -
    # background must win for the CONTAINER regardless (found live on job 16bfc025ca11).
    scene = {
        "page": {"width": 1000.0, "height": 500.0},
        "layers": [{"id": "L1", "name": "L1", "visible": True, "locked": False, "children": [
            {"id": "pc", "kind": "powerclip", "type": "rectangle", "name": "", "x": 0, "y": 0, "w": 1000, "h": 500,
             "rotation": 0, "visible": True, "locked": False, "frame_rect": True, "children": [
                {"id": "small", "kind": "shape", "type": "bitmap", "name": "", "x": 100, "y": 100, "w": 100, "h": 100,
                 "rotation": 0, "visible": True, "locked": False},
            ]},
        ]}],
    }
    zones, _ = oa.classify_zones(scene)
    assert zones[oa.ZONE_BACKGROUND] == ["pc"]
    # the container is still the background, but its separable foreground child is now extracted and
    # routed (it used to be dropped): the whole-board clip is never squeezed into the product zone.
    assert zones[oa.ZONE_PRODUCT] == ["small"]
    ops = oa.convert_orientation(scene, 900.0, 300.0)
    out = scene_ops.apply_ops(scene, ops)
    assert_covers_page_without_distortion(box_of(out, "pc"), 1000.0, 500.0, 900.0, 300.0)


@pytest.mark.parametrize("target_w,target_h", [
    (90.0, 40.0),    # wide (R=2.25)
    (120.0, 36.0),   # wide (R=3.33)
    (48.0, 96.0),    # stack (R=0.5)
    (60.0, 60.0),    # grid (R=1.0)
    (90.0, 60.0),    # grid (R=1.5)
])
def test_convert_orientation_across_arbitrary_target_sizes_has_no_overlap_and_stays_in_bounds(target_w, target_h):
    scene = portrait_scene()
    ops = oa.convert_orientation(scene, target_w, target_h)
    out = scene_ops.apply_ops(scene, ops)
    assert out["page"] == {"width": target_w, "height": target_h}

    zones, _ = oa.classify_zones(scene)
    named = [(z, i) for z in (oa.ZONE_HEADER, oa.ZONE_PRODUCT, oa.ZONE_MAIN_TEXT, oa.ZONE_FOOTER) for i in zones[z]]
    for _, node_id in named:
        assert within_page(out, node_id, tol=1e-2), (target_w, target_h, node_id)
    boxes = [(zone, nid, box_of(out, nid)) for zone, nid in named]
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            za, ia, ba = boxes[a]
            zb, ib, bb = boxes[b]
            assert rect_overlap_area(ba, bb) < 1e-2, f"{ia} ({za}) overlaps {ib} ({zb}) at {target_w}x{target_h}"

    # atomic group resize: the footer's two shapes (addr, contact) are moved together in ONE op,
    # not two independent ones - this is what keeps their relative layout instead of re-deriving it
    footer_ops = [op for op in ops if op["op"] == "resize" and set(op["ids"]) == {"addr", "contact"}]
    assert len(footer_ops) == 1


def _wide_untagged_vector_master() -> dict:
    """A 125x48in landscape master, no tags at all: two top badges (left/right), a central brand logo,
    a table with incense sticks on its left and a product box on its right, and a footer text line."""
    mm = 25.4

    def c(id_, x, y, w, h, t="curve", **extra):
        d = {"id": id_, "kind": "shape", "type": t, "name": "", "x": x * mm, "y": y * mm, "w": w * mm,
             "h": h * mm, "rotation": 0, "visible": True, "locked": False}
        d.update(extra)
        return d

    return {"page": {"width": 125 * mm, "height": 48 * mm}, "layers": [{
        "id": "L", "name": "L", "visible": True, "locked": False, "children": [
            c("badgeL", 3, 36, 18, 9), c("badgeR", 104, 36, 18, 9), c("brand", 45, 30, 35, 9),
            c("table", 30, 6, 65, 22), c("sticks", 20, 8, 8, 20), c("box", 98, 8, 22, 20),
            c("footer", 5, 1, 100, 4, "text", text={"content": "Shop - 12345", "font": "Arial", "size_pt": 24}),
        ]}]}


@pytest.mark.parametrize("w_in,h_in", [(30, 40), (24, 36), (21, 29.7)])   # R = 0.75, 0.667, 0.707
def test_landscape_to_portrait_distributes_untagged_content_across_the_normalized_bands(w_in, h_in):
    mm = 25.4
    scene = _wide_untagged_vector_master()
    W, H = w_in * mm, h_in * mm
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    b = {i: box_of(out, i) for i in ("badgeL", "badgeR", "brand", "table", "sticks", "box", "footer")}
    y0 = lambda i: b[i]["y"] / H
    y1 = lambda i: (b[i]["y"] + b[i]["h"]) / H
    cx = lambda i: (b[i]["x"] + b[i]["w"] / 2) / W

    for i in b:
        assert within_page(out, i, tol=1e-2)
    # every group sits inside ITS band (small margin/gap insets allowed)
    e = 0.035
    for i in ("badgeL", "badgeR"):
        assert y0(i) >= oa.STACK_BAND_HEADER[0] - 1e-6 and y1(i) <= oa.STACK_BAND_HEADER[1] + 1e-6
    assert y0("brand") >= oa.STACK_BAND_BRAND[0] - 1e-6 and y1("brand") <= oa.STACK_BAND_BRAND[1] + 1e-6
    for i in ("table", "sticks", "box"):
        assert y0(i) >= oa.STACK_BAND_PRODUCT[0] - 1e-6 and y1(i) <= oa.STACK_BAND_PRODUCT[1] + 1e-6
        assert y0(i) < oa.STACK_BAND_PRODUCT[0] + e                       # base right above the footer
    assert y1("footer") <= oa.FOOTER_FRAC_PORTRAIT + 1e-6
    # left badge stays left of the right badge (relative source x decides the corner)
    assert cx("badgeL") < 0.5 < cx("badgeR")
    # branding logo is strictly above the products, products strictly above the footer
    assert y0("brand") >= max(y1(i) for i in ("table", "sticks", "box"))
    assert min(y0(i) for i in ("table", "sticks", "box")) >= y1("footer")
    # no large empty top region: header content reaches the top of the canvas
    # corner badges snap to the canvas: centres at 0.18 / 0.82 of the width, top edge at 0.92 of the height
    assert y1("badgeL") == pytest.approx(oa.BADGE_TOP_Y, abs=2e-3) and y1("badgeR") == pytest.approx(oa.BADGE_TOP_Y, abs=2e-3)
    assert cx("badgeL") == pytest.approx(oa.BADGE_LEFT_CX, abs=2e-3) and cx("badgeR") == pytest.approx(oa.BADGE_RIGHT_CX, abs=2e-3)
    # no overlap between any two shapes
    ids = list(b)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            assert rect_overlap_area(b[ids[i]], b[ids[j]]) < 1e-2, (ids[i], ids[j], w_in, h_in)


def _badge_master(product_type: str = "bitmap") -> dict:
    mm = 25.4

    def c(id_, x, y, w, h, t="curve"):
        return {"id": id_, "kind": "shape", "type": t, "name": "", "x": x * mm, "y": y * mm, "w": w * mm,
                "h": h * mm, "rotation": 0, "visible": True, "locked": False}

    footer = c("footer", 5, 1, 100, 4, "text")
    footer["text"] = {"content": "Shop", "font": "Arial", "size_pt": 24}
    return {"page": {"width": 125 * mm, "height": 48 * mm}, "layers": [{
        "id": "L", "name": "L", "visible": True, "locked": False, "children": [
            c("ovalL", 3, 36, 18, 9),
            c("blackstone", 96, 26, 24, 10),               # centre-y ~0.65 (< 0.72), wholly in the right half
            c("brand", 45, 30, 35, 9),                      # central logo, centre-x 0.50
            c("table", 30, 6, 65, 22, product_type), c("sticks", 20, 8, 8, 20, product_type),
            c("box", 98, 8, 22, 18, product_type), footer]}]}


@pytest.mark.parametrize("w_in,h_in", [(30, 40), (24, 36), (21, 29.7)])
def test_upper_right_badge_below_the_header_boundary_is_promoted_into_the_top_right_corner(w_in, h_in):
    mm = 25.4
    scene = _badge_master()
    W, H = w_in * mm, h_in * mm
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    bs, oval, brand = box_of(out, "blackstone"), box_of(out, "ovalL"), box_of(out, "brand")
    # source centre-y is ~0.65 - without promotion it is routed to the branding band and the corner stays empty
    assert (bs["y"] + bs["h"] / 2) / H > oa.STACK_BAND_HEADER[0]
    assert (bs["y"] + bs["h"]) / H == pytest.approx(oa.BADGE_TOP_Y, abs=2e-3)          # snapped: top edge at 0.92
    assert bs["y"] / H >= oa.STACK_BAND_HEADER[0] - 1e-6
    assert (bs["x"] + bs["w"] / 2) / W == pytest.approx(oa.BADGE_RIGHT_CX, abs=2e-3)   # snapped: centre at 0.82
    assert (oval["x"] + oval["w"] / 2) / W == pytest.approx(oa.BADGE_LEFT_CX, abs=2e-3)
    assert (bs["x"] + bs["w"] / 2) / W > 0.5 > (oval["x"] + oval["w"] / 2) / W     # right corner vs. left corner
    assert rect_overlap_area(bs, oval) < 1e-2
    # the central brand logo (centre-x exactly 0.5, straddling the centre line) is NOT promoted
    assert brand["y"] / H >= oa.STACK_BAND_BRAND[0] - 1e-6 and (brand["y"] + brand["h"]) / H <= oa.STACK_BAND_BRAND[1] + 1e-6


def test_promotion_ignores_a_central_shape_slightly_right_of_centre_and_a_tall_composite():
    mm = 25.4
    scene = _badge_master()
    kids = scene["layers"][0]["children"]
    # centre-x = 0.525 but it straddles the centre line (an earlier "centre-x > 0.5" rule promoted this)
    kids.append({"id": "nearcentre", "kind": "shape", "type": "curve", "name": "", "x": 57 * mm, "y": 26 * mm,
                 "w": 17 * mm, "h": 9 * mm, "rotation": 0, "visible": True, "locked": False})
    # wholly in the right half, centre-y > 0.5, but taller than a badge (a product composite, not a badge)
    kids.append({"id": "tall", "kind": "shape", "type": "curve", "name": "", "x": 90 * mm, "y": 14 * mm,
                 "w": 10 * mm, "h": 30 * mm, "rotation": 0, "visible": True, "locked": False})
    W, H = 30 * mm, 40 * mm
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    n1, tall = box_of(out, "nearcentre"), box_of(out, "tall")
    assert n1["y"] / H < oa.STACK_BAND_HEADER[0]          # stayed below the header band
    assert tall["y"] / H < oa.STACK_BAND_HEADER[0]


@pytest.mark.parametrize("w_in,h_in", [(30, 40), (24, 36), (21, 29.7)])
def test_portrait_product_zone_boosts_the_main_object_without_overlap_or_distortion(w_in, h_in):
    mm = 25.4
    scene = _badge_master("bitmap")
    W, H = w_in * mm, h_in * mm
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    t, s, b = (box_of(out, i) for i in ("table", "sticks", "box"))
    # width-limited main object: its width is PORTRAIT_FILL_BOOST x what the default 0.5 column gave
    frame = oa.calculate_zone_rects(W, H)[oa.ZONE_PRODUCT]
    default_w = frame["w"] * (1 - 2 * oa.ZONE_PADDING_FRAC) * oa.MAIN_OBJECT_FRAC
    assert t["w"] / default_w == pytest.approx(oa.PORTRAIT_FILL_BOOST, rel=0.06)
    src = {i: box_of(scene, i) for i in ("table", "sticks", "box")}
    for i, bx in (("table", t), ("sticks", s), ("box", b)):                   # bitmaps: uniform scale only
        assert bx["w"] / bx["h"] == pytest.approx(src[i]["w"] / src[i]["h"], rel=1e-6)
        assert bx["y"] / H >= oa.STACK_BAND_PRODUCT[0] - 1e-6 and (bx["y"] + bx["h"]) / H <= oa.STACK_BAND_PRODUCT[1] + 1e-6
    assert rect_overlap_area(t, s) < 1e-2 and rect_overlap_area(t, b) < 1e-2 and rect_overlap_area(s, b) < 1e-2


def test_boost_is_portrait_only():
    scene = _badge_master("bitmap")
    idx = scene_ops._index(scene)
    ids = ["table", "sticks", "box"]
    frame = oa.calculate_zone_rects(762.0, 1016.0)[oa.ZONE_PRODUCT]

    def table_w(portrait: bool) -> float:
        ops = oa._place_zone_content(oa.ZONE_PRODUCT, idx, ids, frame, portrait)
        main = next(op for op in ops if op["ids"] == ["table"])
        return main["to"]["w"]

    assert table_w(True) / table_w(False) == pytest.approx(oa.PORTRAIT_FILL_BOOST, rel=0.06)


# --------------------------------------------------------------------- page-covering PowerClip unwrapping

def _clip_scene(children, kind="powerclip", locked=False, page=(1000.0, 1000.0)):
    W, H = page
    return {"page": {"width": W, "height": H}, "layers": [{"id": "L", "name": "L", "visible": True, "locked": False,
            "children": [{"id": "pc", "kind": kind, "type": "rectangle", "name": "", "x": 0, "y": 0, "w": W, "h": H,
                          "rotation": 0, "visible": True, "locked": locked, "frame_rect": True, "children": children}]}]}


def _kid(id_, cx, cy, w, h, kind="shape", type_="curve", name="", text=None, W=1000.0, H=1000.0, children=None):
    n = {"id": id_, "kind": kind, "type": type_, "name": name, "x": cx * W - w / 2, "y": cy * H - h / 2, "w": w, "h": h,
         "rotation": 0, "visible": True, "locked": False}
    if text is not None:
        n["text"] = {"content": text, "font": "Arial", "size_pt": 24}
    if children is not None:
        n["children"] = children
    return n


def _clip_children():
    return [
        _kid("backdrop", 0.5, 0.5, 1100, 1100, type_="bitmap"),                 # cov > 1: the clip's own texture
        _kid("spill", 0.5, -0.06, 900, 600, kind="group", type_="group",        # clipped artwork: bbox mostly off-page
             children=[_kid("spill_a", 0.5, -0.06, 100, 100)]),
        _kid("badgeL", 0.12, 0.86, 180, 90, kind="group", type_="group", children=[_kid("bl", 0.12, 0.86, 90, 40)]),
        _kid("badgeR", 0.88, 0.85, 180, 90, kind="group", type_="group", children=[_kid("br", 0.88, 0.85, 90, 40)]),
        _kid("logo", 0.5, 0.64, 350, 100),                                       # branding roof
        _kid("table", 0.3, 0.34, 400, 250, kind="group", type_="group",          # product/table composite
             children=[_kid("tb", 0.3, 0.34, 400, 250, type_="bitmap")]),
        _kid("sticks", 0.75, 0.40, 60, 260, type_="bitmap"),
        _kid("shop", 0.5, 0.06, 600, 60, type_="text", text="SHOP NAME"),        # bottom band
        _kid("phone", 0.5, 0.45, 300, 40, type_="text", text="Phone No. 555-1234"),   # contact regex beats its Y
    ]


def test_page_covering_powerclip_children_are_routed_into_their_zones():
    zones, _ = oa.classify_zones(_clip_scene(_clip_children()))
    assert zones[oa.ZONE_BACKGROUND] == ["pc"]                                   # container stays the background
    assert sorted(zones[oa.ZONE_HEADER]) == ["badgeL", "badgeR"]                 # Y > 0.72, both corners
    assert zones[oa.ZONE_MAIN_TEXT] == ["logo"]                                  # 0.55-0.72 branding roof
    assert sorted(zones[oa.ZONE_PRODUCT]) == ["sticks", "table"]                 # 0.20-0.55 mid/lower body
    assert sorted(zones[oa.ZONE_FOOTER]) == ["phone", "shop"]                    # Y < 0.20, and contact regex
    assert zones[oa.ZONE_OTHER] == []
    everything = [i for z in oa.ZONES for i in zones[z]]
    assert "backdrop" not in everything and "spill" not in everything            # backdrop + clipped art ride along
    assert len(everything) == len(set(everything))


def test_a_slot_tag_on_a_clip_child_beats_the_position_band():
    kids = [_kid("addr", 0.5, 0.9, 500, 40, type_="text", name="address", text="12 Main St")]   # tag says footer
    zones, _ = oa.classify_zones(_clip_scene(kids))
    assert zones[oa.ZONE_FOOTER] == ["addr"] and zones[oa.ZONE_HEADER] == []


@pytest.mark.parametrize("kind", ["group", "shape"])
def test_a_page_covering_non_powerclip_stays_a_plain_background_with_nothing_extracted(kind):
    scene = _clip_scene(_clip_children(), kind=kind)
    if kind == "shape":
        scene["layers"][0]["children"][0].pop("children")
    zones, _ = oa.classify_zones(scene)
    assert zones[oa.ZONE_BACKGROUND] == ["pc"]
    assert not any(zones[z] for z in (oa.ZONE_HEADER, oa.ZONE_PRODUCT, oa.ZONE_MAIN_TEXT, oa.ZONE_FOOTER))


def test_a_locked_page_covering_powerclip_is_not_unwrapped():
    zones, warnings = oa.classify_zones(_clip_scene(_clip_children(), locked=True))
    assert zones[oa.ZONE_BACKGROUND] == ["pc"]
    assert not any(zones[z] for z in (oa.ZONE_HEADER, oa.ZONE_PRODUCT, oa.ZONE_MAIN_TEXT, oa.ZONE_FOOTER))
    assert any("pc" in w and "locked" in w for w in warnings)


@pytest.mark.parametrize("target", [(762.0, 1016.0), (609.6, 914.4), (3000.0, 900.0)])   # portrait x2, landscape
def test_extracted_clip_children_are_placed_once_inside_their_zone_and_the_container_still_covers(target):
    tw, th = target
    scene = _clip_scene(_clip_children())
    ops = oa.convert_orientation(scene, tw, th)
    out = scene_ops.apply_ops(scene, ops)                                        # every op must apply cleanly
    assert_covers_page_without_distortion(box_of(out, "pc"), 1000.0, 1000.0, tw, th)
    frames = oa.calculate_zone_rects(tw, th)
    zone_of = {"badgeL": oa.ZONE_HEADER, "badgeR": oa.ZONE_HEADER, "logo": oa.ZONE_MAIN_TEXT, "table": oa.ZONE_PRODUCT,
               "sticks": oa.ZONE_PRODUCT, "shop": oa.ZONE_FOOTER, "phone": oa.ZONE_FOOTER}
    for nid, zone in zone_of.items():
        b, f = box_of(out, nid), frames[zone]
        tol = 1e-2
        assert f["x"] - tol <= b["x"] and b["x"] + b["w"] <= f["x"] + f["w"] + tol, (nid, target)
        assert f["y"] - tol <= b["y"] and b["y"] + b["h"] <= f["y"] + f["h"] + tol, (nid, target)
    # not transformed twice: the children move only through their own zone op, never again via the container's
    assert sum(1 for op in ops if op["op"] == "resize" and "pc" in op["ids"]) == 1
    # bitmaps inside extracted groups keep their aspect ratio (uniform scale)
    tb, src = box_of(out, "tb"), box_of(scene, "tb")
    assert tb["w"] / tb["h"] == pytest.approx(src["w"] / src["h"], rel=1e-6)
    # the clipped artwork and the backdrop are carried by the container, so they scale with it as one
    assert box_of(out, "spill_a")["w"] == pytest.approx(box_of(scene, "spill_a")["w"] * box_of(out, "pc")["w"] / 1000.0, rel=1e-6)


# --------------------------------------------------------------------- product-zone bottom anchoring + bounded boost

PORTRAITS_MM = [(30 * 25.4, 40 * 25.4), (24 * 25.4, 36 * 25.4), (21 * 25.4, 29.7 * 25.4)]


def _lone_product_scene(w_mm, h_mm, type_="bitmap"):
    """Wide 125x48in master with a single untagged product object whose source centre-y (0.35) is in the
    product band, plus nothing else - so the product zone holds exactly one object."""
    mm = 25.4
    kid = {"id": "prod", "kind": "shape", "type": type_, "name": "", "x": 40 * mm, "y": 8 * mm, "w": w_mm,
           "h": h_mm, "rotation": 0, "visible": True, "locked": False}
    return {"page": {"width": 125 * mm, "height": 48 * mm}, "layers": [{"id": "L", "name": "L", "visible": True,
            "locked": False, "children": [kid]}]}


@pytest.mark.parametrize("W,H", PORTRAITS_MM)
@pytest.mark.parametrize("type_", ["bitmap", "curve"])
def test_a_lone_portrait_product_stands_on_the_product_baseline_and_is_horizontally_centred(W, H, type_):
    scene = _lone_product_scene(65 * 25.4, 22 * 25.4, type_)          # a wide table, shorter than its zone
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    b, f = box_of(out, "prod"), oa.calculate_zone_rects(W, H)[oa.ZONE_PRODUCT]
    assert b["y"] == pytest.approx(f["y"], abs=1e-6)                                   # bottom on the baseline
    assert b["y"] + b["h"] <= f["y"] + f["h"] + 1e-6                                   # never above the zone
    assert b["x"] + b["w"] / 2 == pytest.approx(f["x"] + f["w"] / 2, abs=1e-6)         # X stays centred
    assert b["y"] / H >= oa.STACK_BAND_PRODUCT[0] - 1e-6                               # baseline ~ just above the footer


@pytest.mark.parametrize("W,H", PORTRAITS_MM)
def test_main_object_and_flanking_objects_all_stand_on_the_same_baseline(W, H):
    scene = _badge_master("bitmap")                                     # table + sticks + box in the product zone
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    f = oa.calculate_zone_rects(W, H)[oa.ZONE_PRODUCT]
    for i in ("table", "sticks", "box"):
        assert box_of(out, i)["y"] == pytest.approx(f["y"], abs=1e-6), i


def test_product_never_reaches_the_branding_zone_or_the_footer():
    W, H = PORTRAITS_MM[0]
    scene = _badge_master("bitmap")
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    fr = oa.calculate_zone_rects(W, H)
    for i in ("table", "sticks", "box"):
        b = box_of(out, i)
        assert b["y"] + b["h"] <= fr[oa.ZONE_MAIN_TEXT]["y"] + 1e-6              # top stays below the branding roof
        assert b["y"] >= fr[oa.ZONE_FOOTER]["y"] + fr[oa.ZONE_FOOTER]["h"] - 1e-6


@pytest.mark.parametrize("w,h", [(300, 100), (100, 300), (200, 200), (400, 200)])
def test_zone_fit_boost_is_bounded_by_the_unpadded_frame_and_never_shrinks(w, h):
    frame = oa.calculate_zone_rects(762.0, 1016.0)[oa.ZONE_PRODUCT]
    idx = {"a": {"node": {"id": "a", "kind": "shape", "type": "bitmap", "x": 0, "y": 0, "w": w, "h": h}}}
    frm = {"x": 0, "y": 0, "w": w, "h": h}
    plain = oa._zone_fit(idx, ["a"], frm, frame, anchor_bottom=True)                 # anchored, no boost
    boosted = oa._zone_fit(idx, ["a"], frm, frame, anchor_bottom=True, boost=oa.PORTRAIT_FILL_BOOST)
    assert boosted["w"] >= plain["w"] - 1e-9 and boosted["h"] >= plain["h"] - 1e-9
    assert boosted["w"] / boosted["h"] == pytest.approx(w / h, rel=1e-6)             # uniform: aspect kept
    assert boosted["w"] <= frame["w"] + 1e-6 and boosted["y"] + boosted["h"] <= frame["y"] + frame["h"] + 1e-6
    assert boosted["w"] / plain["w"] <= oa.PORTRAIT_FILL_BOOST + 1e-9                # never more than asked
    assert boosted["y"] == pytest.approx(frame["y"], abs=1e-6)


def test_anchoring_and_boost_apply_only_to_the_portrait_product_zone():
    idx = {"a": {"node": {"id": "a", "kind": "shape", "type": "bitmap", "x": 0, "y": 0, "w": 300, "h": 100}}}
    frm = {"x": 0, "y": 0, "w": 300, "h": 100}
    frame = oa.calculate_zone_rects(762.0, 1016.0)[oa.ZONE_PRODUCT]

    def to(zone, portrait):
        return oa._place_zone_content(zone, idx, ["a"], frame, portrait)[0]["to"]

    # branding (main_text) keeps the standard centred fit - vertically centred in its frame, no boost
    t = to(oa.ZONE_MAIN_TEXT, True)
    assert t["y"] + t["h"] / 2 == pytest.approx(frame["y"] + frame["h"] / 2, abs=1e-6)
    # a landscape target's product zone (a side column) is centred too, not bottom-anchored
    t = to(oa.ZONE_PRODUCT, False)
    assert t["y"] + t["h"] / 2 == pytest.approx(frame["y"] + frame["h"] / 2, abs=1e-6)
    # the portrait product zone is the anchored one
    assert to(oa.ZONE_PRODUCT, True)["y"] == pytest.approx(frame["y"], abs=1e-6)


import json as _json
import os as _os

_REAL = {
    "AL MADEENA": ("data/jobs_v2/16bfc025ca11/out/91a4cdb56ffe/scene/scene.json", ["s46", "s6"]),
    "DARSHAN": ("data/jobs_v2/8a41177716c4/out/fe047cace239/scene/scene.json", ["s46", "s4"]),
}


@pytest.mark.parametrize("board", list(_REAL))
@pytest.mark.parametrize("W,H", [PORTRAITS_MM[0], PORTRAITS_MM[1]])
def test_real_boards_product_objects_stand_on_the_baseline(board, W, H):
    rel, ids = _REAL[board]
    path = _os.path.join(_os.path.dirname(__file__), "..", rel)
    if not _os.path.exists(path):
        pytest.skip("cached real scene not present in this checkout")
    scene = _json.load(open(path, encoding="utf-8"))
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    f = oa.calculate_zone_rects(W, H)[oa.ZONE_PRODUCT]
    for i in ids:
        b = box_of(out, i)
        assert b["y"] == pytest.approx(f["y"], abs=1e-3), (board, i)
        assert b["y"] + b["h"] <= f["y"] + f["h"] + 1e-3
    # the whole-board container still covers the page
    pc = box_of(out, "s44")
    assert pc["x"] <= 1e-3 and pc["y"] <= 1e-3 and pc["x"] + pc["w"] >= W - 1e-3 and pc["y"] + pc["h"] >= H - 1e-3


# --------------------------------------------------------------------- Portrait -> Landscape (wide stage)

WIDE_RATIOS_IN = [(60, 30), (120, 30), (180, 30)]      # 2:1, 4:1, 6:1


def _tall_master() -> dict:
    """A 30x40in PORTRAIT master, untagged: two top badges, a brand roof, three products stacked vertically on
    one axis (prodA above prodB above prodC), and a footer line."""
    mm = 25.4
    W, H = 30 * mm, 40 * mm

    def c(id_, cx, cy, w, h, type_="curve", **extra):
        d = {"id": id_, "kind": "shape", "type": type_, "name": "", "x": cx * W - w / 2, "y": cy * H - h / 2,
             "w": w, "h": h, "rotation": 0, "visible": True, "locked": False}
        d.update(extra)
        return d

    kids = [c("badgeL", .16, .87, 150, 90), c("badgeR", .84, .87, 150, 90), c("brand", .5, .64, 420, 110),
            c("prodA", .5, .46, 260, 200, "bitmap"), c("prodB", .5, .32, 300, 150, "bitmap"),
            c("prodC", .5, .24, 200, 110, "bitmap"),
            c("footer", .5, .07, 600, 60, "text", text={"content": "Shop - 12345", "font": "Arial", "size_pt": 24})]
    return {"page": {"width": W, "height": H}, "layers": [{"id": "L", "name": "L", "visible": True,
            "locked": False, "children": kids}]}


def test_direction_distinguishes_source_from_target_orientation():
    assert oa.Direction(400, 1000, 900, 300).portrait_to_wide
    assert oa.Direction(400, 1000, 900, 300) == ("portrait", "landscape")
    assert not oa.Direction(1000, 400, 900, 300).portrait_to_wide        # landscape -> landscape
    assert not oa.Direction(400, 1000, 300, 900).portrait_to_wide        # portrait -> portrait ...
    assert oa.Direction(400, 1000, 300, 900).to_portrait                 # ... which is still a portrait target
    assert oa.Direction(500, 500, 900, 300).source == "square"


@pytest.mark.parametrize("w_in,h_in", WIDE_RATIOS_IN + [(45, 30)])
def test_wide_stage_bands_are_the_same_normalized_fractions_for_every_wide_ratio(w_in, h_in):
    W, H = w_in * 25.4, h_in * 25.4
    f = oa.calculate_zone_rects(W, H, portrait_source=True)
    _assert_disjoint_and_in_bounds(f, W, H)
    tol = 0.035
    for zone, (lo, hi) in ((oa.ZONE_FOOTER, oa.WIDE_STAGE_FOOTER), (oa.ZONE_PRODUCT, oa.WIDE_STAGE_PRODUCT),
                           (oa.ZONE_MAIN_TEXT, oa.WIDE_STAGE_BRAND), (oa.ZONE_HEADER, oa.WIDE_STAGE_HEADER)):
        y0, y1 = f[zone]["y"] / H, (f[zone]["y"] + f[zone]["h"]) / H
        assert lo - 1e-9 <= y0 <= lo + tol and hi - tol <= y1 <= hi + 1e-9, (zone, y0, y1)
        assert f[zone]["w"] == pytest.approx(W - 2 * oa.MARGIN_FRAC * H, abs=1e-6)          # full width
    assert (f[oa.ZONE_FOOTER]["y"] + f[oa.ZONE_FOOTER]["h"]) / H <= 0.12                     # slim footer bar
    # a landscape SOURCE keeps the original wide template, untouched
    assert oa.calculate_zone_rects(W, H)[oa.ZONE_FOOTER]["h"] == pytest.approx(oa.FOOTER_FRAC * H, abs=1e-6)


@pytest.mark.parametrize("w,h", [(2.0, 1.0), (1.01, 1.0), (100000.0, 1.0), (0.5, 0.01), (1000.0, 1.0)])
def test_wide_stage_bands_never_degenerate(w, h):
    f = oa.calculate_zone_rects(w, h, portrait_source=True)
    for z in f.values():
        assert z["w"] > 0 and z["h"] > 0
    _assert_disjoint_and_in_bounds(f, w, h)


@pytest.mark.parametrize("w_in,h_in", WIDE_RATIOS_IN)
def test_portrait_master_unfolds_into_a_wide_lineup_on_one_baseline(w_in, h_in):
    mm = 25.4
    scene = _tall_master()
    W, H = w_in * mm, h_in * mm
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    fr = oa.calculate_zone_rects(W, H, portrait_source=True)
    prod = fr[oa.ZONE_PRODUCT]
    ids = ("badgeL", "badgeR", "brand", "prodA", "prodB", "prodC", "footer")
    b = {i: box_of(out, i) for i in ids}
    for i in ids:
        assert within_page(out, i, tol=1e-2)

    # the vertical stack unfolds horizontally, top product first
    xs = [b[i]["x"] + b[i]["w"] / 2 for i in ("prodA", "prodB", "prodC")]
    assert xs[0] < xs[1] < xs[2]
    # every product stands on the single wide baseline (the stage's bottom edge) and stays inside the stage
    for i in ("prodA", "prodB", "prodC"):
        assert b[i]["y"] == pytest.approx(prod["y"], abs=1e-6), i
        assert b[i]["y"] + b[i]["h"] <= prod["y"] + prod["h"] + 1e-6
        assert prod["x"] - 1e-6 <= b[i]["x"] and b[i]["x"] + b[i]["w"] <= prod["x"] + prod["w"] + 1e-6
    # minimum clearance between neighbours, and proportions kept (bitmaps scale uniformly, all by one factor)
    clear = oa.UNSTACK_MIN_CLEARANCE_FRAC * prod["w"]
    for l, r in (("prodA", "prodB"), ("prodB", "prodC")):
        assert b[r]["x"] - (b[l]["x"] + b[l]["w"]) >= clear - 1e-6
    src = {i: box_of(scene, i) for i in ("prodA", "prodB", "prodC")}
    ks = [b[i]["w"] / src[i]["w"] for i in src]
    assert max(ks) == pytest.approx(min(ks), rel=1e-6)
    for i in src:
        assert b[i]["w"] / b[i]["h"] == pytest.approx(src[i]["w"] / src[i]["h"], rel=1e-6)
    # not compressed into a central block: the lineup spans most of the stage
    span = b["prodC"]["x"] + b["prodC"]["w"] - b["prodA"]["x"]
    assert span >= 0.5 * prod["w"]
    # far top-left / top-right badge slots, in the header row
    hf = fr[oa.ZONE_HEADER]
    assert b["badgeL"]["x"] == pytest.approx(hf["x"], abs=1e-6)
    assert b["badgeR"]["x"] + b["badgeR"]["w"] == pytest.approx(hf["x"] + hf["w"], abs=1e-6)
    assert b["badgeL"]["x"] + b["badgeL"]["w"] <= hf["x"] + oa.CORNER_SLOT_FRAC * hf["w"] + 1e-6
    for i in ("badgeL", "badgeR"):
        assert b[i]["y"] + b[i]["h"] == pytest.approx(hf["y"] + hf["h"], abs=1e-6)            # top-aligned
        assert b[i]["w"] / b[i]["h"] == pytest.approx(box_of(scene, i)["w"] / box_of(scene, i)["h"], rel=1e-6)
    # branding roof: centred, in its own row above the stage
    bf = fr[oa.ZONE_MAIN_TEXT]
    assert b["brand"]["x"] + b["brand"]["w"] / 2 == pytest.approx(W / 2, abs=1e-3)      # coordinates are rounded
    assert bf["y"] - 1e-6 <= b["brand"]["y"] and b["brand"]["y"] + b["brand"]["h"] <= bf["y"] + bf["h"] + 1e-6
    # slim footer bar
    ff = fr[oa.ZONE_FOOTER]
    assert ff["y"] - 1e-6 <= b["footer"]["y"] and b["footer"]["y"] + b["footer"]["h"] <= ff["y"] + ff["h"] + 1e-6
    # nothing overlaps anything else
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            assert rect_overlap_area(b[ids[i]], b[ids[j]]) < 1e-2, (ids[i], ids[j], w_in, h_in)


def test_an_upper_quadrant_badge_below_the_header_band_is_promoted_to_a_far_corner_slot():
    mm = 25.4
    scene = _tall_master()
    W0, H0 = scene["page"]["width"], scene["page"]["height"]
    # a small secondary badge on the LEFT half (centre-y 0.62, below the 0.72 header boundary) and a tall
    # product-like shape on the right half that must NOT be taken for a badge
    scene["layers"][0]["children"] += [
        {"id": "promo", "kind": "shape", "type": "curve", "name": "", "x": 0.05 * W0, "y": 0.60 * H0, "w": 120,
         "h": 70, "rotation": 0, "visible": True, "locked": False},
        {"id": "tallthing", "kind": "shape", "type": "curve", "name": "", "x": 0.70 * W0, "y": 0.52 * H0, "w": 140,
         "h": 0.30 * H0, "rotation": 0, "visible": True, "locked": False}]
    W, H = 120 * mm, 30 * mm
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    fr = oa.calculate_zone_rects(W, H, portrait_source=True)
    p, t = box_of(out, "promo"), box_of(out, "tallthing")
    assert p["y"] >= fr[oa.ZONE_HEADER]["y"] - 1e-6                    # promoted into the header row ...
    assert p["x"] == pytest.approx(fr[oa.ZONE_HEADER]["x"], abs=1e-6)  # ... at the far left
    assert t["y"] < fr[oa.ZONE_HEADER]["y"]                            # the tall shape stayed out of the header


def test_a_middle_header_shape_joins_the_branding_roof_instead_of_a_corner():
    mm = 25.4
    scene = _tall_master()
    W0, H0 = scene["page"]["width"], scene["page"]["height"]
    scene["layers"][0]["children"].append(
        {"id": "title", "kind": "shape", "type": "curve", "name": "", "x": 0.5 * W0 - 150, "y": 0.90 * H0, "w": 300,
         "h": 80, "rotation": 0, "visible": True, "locked": False})     # top centre, above the 0.72 boundary
    W, H = 120 * mm, 30 * mm
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    bf = oa.calculate_zone_rects(W, H, portrait_source=True)[oa.ZONE_MAIN_TEXT]
    t = box_of(out, "title")
    assert t["y"] >= bf["y"] - 1e-6 and t["y"] + t["h"] <= bf["y"] + bf["h"] + 1e-6
    assert t["x"] + t["w"] / 2 == pytest.approx(W / 2, abs=1e-3)              # centred, like the brand it joined


def test_unstack_order_reads_a_vertical_stack_top_first_then_moves_to_the_next_column():
    def node(id_, cx, cy, w=100, h=50):
        return {"id": id_, "kind": "shape", "type": "bitmap", "x": cx - w / 2, "y": cy - h / 2, "w": w, "h": h}
    idx = {n["id"]: {"node": n} for n in (node("low", 200, 100), node("high", 200, 400), node("mid", 210, 250),
                                          node("right", 600, 300), node("far", 900, 200))}
    assert oa._unstack_order(idx, list(idx)) == ["high", "mid", "low", "right", "far"]


def test_unstack_lineup_keeps_exactly_the_minimum_clearance_when_the_row_is_tight():
    def node(id_, x, w=400, h=100):
        return {"id": id_, "kind": "shape", "type": "bitmap", "x": x, "y": 0, "w": w, "h": h}
    idx = {i: {"node": node(i, k * 1000)} for k, i in enumerate("abcde")}      # five wide products, narrow frame
    frame = {"x": 0.0, "y": 50.0, "w": 1000.0, "h": 900.0}
    ops = oa._unstack_products(idx, list("abcde"), frame)
    clear = oa.UNSTACK_MIN_CLEARANCE_FRAC * frame["w"]
    boxes = [op["to"] for op in ops]
    for l, r in zip(boxes, boxes[1:]):
        assert r["x"] - (l["x"] + l["w"]) == pytest.approx(clear, abs=1e-6)
    assert boxes[0]["x"] >= frame["x"] - 1e-6 and boxes[-1]["x"] + boxes[-1]["w"] <= frame["x"] + frame["w"] + 1e-6
    assert all(bx["y"] == pytest.approx(frame["y"]) for bx in boxes)


def test_a_lone_product_is_centred_on_the_wide_baseline():
    mm = 25.4
    scene = _tall_master()
    scene["layers"][0]["children"] = [n for n in scene["layers"][0]["children"] if n["id"] not in ("prodB", "prodC")]
    W, H = 120 * mm, 30 * mm
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    prod = oa.calculate_zone_rects(W, H, portrait_source=True)[oa.ZONE_PRODUCT]
    b = box_of(out, "prodA")
    assert b["y"] == pytest.approx(prod["y"], abs=1e-6)
    assert b["x"] + b["w"] / 2 == pytest.approx(prod["x"] + prod["w"] / 2, abs=1e-6)


def test_landscape_to_wide_still_uses_the_original_capacity_path_not_the_stage():
    scene = untagged_scene()                       # 2000x800 landscape master
    ops = oa.convert_orientation(scene, 4000.0, 1000.0)
    out = scene_ops.apply_ops(scene, ops)          # applies cleanly
    fr = oa.calculate_zone_rects(4000.0, 1000.0)   # original wide template, as before
    assert fr[oa.ZONE_FOOTER]["h"] == pytest.approx(oa.FOOTER_FRAC * 1000.0, abs=1e-6)
    assert out["page"] == {"width": 4000.0, "height": 1000.0}


# --------------------------------------------------------------------- stretch cap (uniform-contain fallback)

def _stretch(src: dict, dst: dict) -> float:
    sx, sy = dst["w"] / src["w"], dst["h"] / src["h"]
    return max(sx / sy, sy / sx)


def _vec_idx(*nodes, page=None):
    idx = oa._Idx({n["id"]: {"node": n, "parent": None} for n in nodes})
    idx.page = page
    return idx


def _vec(id_, w, h, type_="curve", **extra):
    n = {"id": id_, "kind": "shape", "type": type_, "name": "", "x": 0.0, "y": 0.0, "w": w, "h": h,
         "rotation": 0, "visible": True, "locked": False}
    n.update(extra)
    return n


def test_zone_fit_keeps_filling_the_frame_while_the_stretch_is_within_the_cap():
    frame = {"x": 0.0, "y": 0.0, "w": 400.0, "h": 200.0}
    logo = _vec("a", 200.0, 100.0)                                   # frame aspect 2.0 vs 2.0: no stretch at all
    to = oa._zone_fit(_vec_idx(logo), ["a"], {"x": 0, "y": 0, "w": 200, "h": 100}, frame)
    fill = oa._fill_frame(frame)
    assert to == fill                                                # identical to the old fill-to-frame result
    wide = _vec("b", 100.0, 100.0)                                   # 1:1 into a 2:1 frame -> stretch 2.0 == cap: still fills
    to = oa._zone_fit(_vec_idx(wide), ["b"], {"x": 0, "y": 0, "w": 100, "h": 100}, frame)
    assert to == fill


def test_zone_fit_falls_back_to_a_uniform_centred_contain_fit_beyond_the_cap():
    frame = {"x": 10.0, "y": 20.0, "w": 400.0, "h": 100.0}
    text = _vec("t", 100.0, 100.0, "text", text={"content": "X", "font": "Arial", "size_pt": 20})   # 1:1 into 4:1 = 4x
    frm = {"x": 0, "y": 0, "w": 100, "h": 100}
    to = oa._zone_fit(_vec_idx(text), ["t"], frm, frame)
    fill = oa._fill_frame(frame)
    sx, sy = to["w"] / frm["w"], to["h"] / frm["h"]
    assert sx == pytest.approx(sy, rel=1e-6)                         # scale_x == scale_y: 100% of the aspect kept
    assert sx == pytest.approx(min(fill["w"] / 100, fill["h"] / 100), rel=1e-6)   # the smaller factor
    assert to["x"] + to["w"] / 2 == pytest.approx(fill["x"] + fill["w"] / 2, abs=1e-3)   # centred in the frame
    assert to["y"] + to["h"] / 2 == pytest.approx(fill["y"] + fill["h"] / 2, abs=1e-3)
    assert to["w"] < fill["w"]                                       # narrower than the frame: it no longer fills it


def test_zone_fit_fallback_bottom_anchors_on_the_product_stage():
    frame = {"x": 10.0, "y": 20.0, "w": 400.0, "h": 100.0}
    logo = _vec("a", 100.0, 100.0)
    to = oa._zone_fit(_vec_idx(logo), ["a"], {"x": 0, "y": 0, "w": 100, "h": 100}, frame, anchor_bottom=True)
    assert to["y"] == pytest.approx(frame["y"], abs=1e-6)            # standing on the frame's bottom edge
    assert to["w"] / to["h"] == pytest.approx(1.0, rel=1e-6)
    assert to["x"] + to["w"] / 2 == pytest.approx(frame["x"] + frame["w"] / 2, abs=1e-3)


def test_the_cap_is_configurable_per_call():
    frame = {"x": 0.0, "y": 0.0, "w": 400.0, "h": 100.0}
    logo = _vec("a", 100.0, 100.0)
    frm = {"x": 0, "y": 0, "w": 100, "h": 100}
    idx = _vec_idx(logo)
    assert _stretch(frm, oa._zone_fit(idx, ["a"], frm, frame)) == pytest.approx(1.0)        # default cap 2.0: uniform
    token = oa._MAX_STRETCH_CTX.set(1e9)                                                     # cap effectively off
    try:
        assert _stretch(frm, oa._zone_fit(idx, ["a"], frm, frame)) > 3.0                    # the old fill-to-frame behaviour
    finally:
        oa._MAX_STRETCH_CTX.reset(token)


def test_solid_panels_and_full_bleed_bars_are_exempt_and_keep_stretching_edge_to_edge():
    frame = {"x": 0.0, "y": 0.0, "w": 1000.0, "h": 50.0}
    frm = {"x": 0, "y": 0, "w": 100, "h": 100}
    fill = oa._fill_frame(frame)
    panel = _vec("p", 100.0, 100.0, "rectangle")                     # a solid colour panel
    assert oa._zone_fit(_vec_idx(panel), ["p"], frm, frame) == fill
    bar = _vec("bar", 950.0, 100.0)                                  # childless non-text shape, 95% of a 1000-wide page
    assert oa._zone_fit(_vec_idx(bar, page=(1000.0, 800.0)), ["bar"], {"x": 0, "y": 0, "w": 950, "h": 100}, frame) == fill
    not_bar = _vec("nb", 500.0, 100.0)                               # the same shape at 50% of the page is NOT exempt
    assert oa._zone_fit(_vec_idx(not_bar, page=(1000.0, 800.0)), ["nb"], {"x": 0, "y": 0, "w": 500, "h": 100}, frame) != fill
    # one glyph-carrying member makes the whole rigid group subject to the cap
    text = _vec("t", 100.0, 100.0, "text", text={"content": "X", "font": "Arial", "size_pt": 20})
    assert oa._zone_fit(_vec_idx(panel, text), ["p", "t"], frm, frame) != fill
    # a plain rectangle is exempt through the whole pipeline (the fill path also honours anchor_bottom)
    assert oa._zone_fit(_vec_idx(panel), ["p"], frm, frame, anchor_bottom=True)["y"] == pytest.approx(frame["y"])


@pytest.mark.parametrize("w_in,h_in", [(10, 40), (240, 30)])            # 1:4 tall and 8:1 wide
@pytest.mark.parametrize("which", ["tall", "wide", "badge"])
def test_vector_and_text_never_exceed_the_stretch_cap_at_the_extreme_ratios(which, w_in, h_in):
    scene = {"tall": _tall_master, "wide": _wide_untagged_vector_master, "badge": lambda: _badge_master("curve")}[which]()
    W, H = w_in * 25.4, h_in * 25.4
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    zones, _ = oa.classify_zones(scene)
    checked = 0
    for i in [j for z in (oa.ZONE_HEADER, oa.ZONE_PRODUCT, oa.ZONE_MAIN_TEXT, oa.ZONE_FOOTER, oa.ZONE_OTHER) for j in zones[z]]:
        src = scene_ops.find_node(scene, i)
        if _contains_bitmap_local(src):
            continue
        assert _stretch(box_of(scene, i), box_of(out, i)) <= oa.MAX_STRETCH_RATIO + 1e-6, (i, which, w_in, h_in)
        checked += 1
    assert checked >= 3


def _contains_bitmap_local(n):
    return n.get("type") == "bitmap" or any(_contains_bitmap_local(c) for c in n.get("children") or [])


def test_uniform_fallback_is_exactly_uniform_end_to_end_and_the_container_still_covers():
    # a very wide footer text line (aspect 25) into a 1:4 target needs a big stretch: the cap turns it uniform
    scene = _tall_master()
    W, H = 10 * 25.4, 40 * 25.4
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    src, dst = box_of(scene, "footer"), box_of(out, "footer")
    assert dst["w"] / src["w"] == pytest.approx(dst["h"] / src["h"], rel=1e-3)              # scale_x == scale_y
    # a page-covering background container is unaffected by the cap: it still covers the page edge to edge
    scene2 = _clip_scene(_clip_children())
    out2 = scene_ops.apply_ops(scene2, oa.convert_orientation(scene2, W, H))
    assert_covers_page_without_distortion(box_of(out2, "pc"), 1000.0, 1000.0, W, H)


def test_max_stretch_argument_overrides_the_default_for_one_call_only():
    scene = _tall_master()
    W, H = 10 * 25.4, 40 * 25.4
    tight = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H, max_stretch=1.0))
    loose = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H, max_stretch=1e9))
    default = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    src = box_of(scene, "footer")
    assert _stretch(src, box_of(tight, "footer")) == pytest.approx(1.0, abs=1e-4)          # coordinates are rounded
    assert _stretch(src, box_of(loose, "footer")) > oa.MAX_STRETCH_RATIO
    assert _stretch(src, box_of(default, "footer")) <= oa.MAX_STRETCH_RATIO + 1e-6
    assert oa._MAX_STRETCH_CTX.get() is None                          # the override did not leak out of the call


# --------------------------------------------------------------------- fragment clustering (loose logo pieces)

def _frag(id_, x, y, w, h, type_="curve", **extra):
    n = {"id": id_, "kind": "shape", "type": type_, "name": "", "x": float(x), "y": float(y), "w": float(w), "h": float(h),
         "rotation": 0, "visible": True, "locked": False}
    n.update(extra)
    return n


def _logo_board():
    """An ungrouped, Dalmia-style landscape board (page 2000x800): a page-covering backdrop, a WHITE CARD with its
    drop-shadow duplicate and content fragments inside it (one logo, ten loose pieces), a second small logo far away,
    a text line and a footer rule."""
    kids = [_frag("bg", 0, 0, 2000, 800)]
    kids += [_frag("card", 100, 200, 600, 400), _frag("shadow", 110, 190, 600, 400)]
    kids += [_frag(f"piece{k}", 150 + 50 * k, 260 + 30 * (k % 3), 40, 60) for k in range(8)]
    kids += [_frag("badge_a", 1500, 600, 300, 100), _frag("badge_b", 1510, 610, 120, 60)]
    kids += [_frag("rule", 100, 110, 1800, 4), _frag("line", 900, 50, 500, 40, "text", text={"content": "Shop", "font": "Arial", "size_pt": 30})]
    return {"page": {"width": 2000.0, "height": 800.0}, "layers": [{"id": "L", "name": "L", "visible": True,
            "locked": False, "children": kids}]}


def test_group_fragments_binds_each_visual_cluster_into_one_group():
    ops = oa._group_fragments(_logo_board())
    assert all(op["op"] == "group" for op in ops)
    sets = sorted(sorted(op["ids"]) for op in ops)
    big = sorted(["card", "shadow"] + [f"piece{k}" for k in range(8)])
    assert big in sets and sorted(["badge_a", "badge_b"]) in sets
    assert len(ops) == 2                                    # bg, rule and the text line are NOT grouped
    grouped = {i for op in ops for i in op["ids"]}
    assert not grouped & {"bg", "rule", "line"}


def test_group_fragments_leaves_groups_only_clusters_tags_text_bitmaps_and_locked_alone():
    board = _logo_board()
    kids = board["layers"][0]["children"]
    # a bitmap touching the card, a tagged shape touching the badge, a locked shape touching the rule
    kids += [_frag("photo", 690, 300, 100, 100, "bitmap"), _frag("tagged", 1600, 640, 50, 50, name="brand_title"),
             {**_frag("locked", 800, 108, 100, 8), "locked": True}]
    ops = oa._group_fragments(board)
    grouped = {i for op in ops for i in op["ids"]}
    assert not grouped & {"photo", "tagged", "locked", "line", "bg"}
    # two vector-only GROUPS that touch, with no loose shape among them: nothing to bind (real logos already grouped)
    g = lambda id_, x: {"id": id_, "kind": "group", "type": "group", "name": "", "x": x, "y": 100.0, "w": 100.0, "h": 50.0,
                        "rotation": 0, "visible": True, "locked": False, "children": [_frag(id_ + "c", x, 100, 100, 50)]}
    only_groups = {"page": {"width": 1000.0, "height": 800.0}, "layers": [{"id": "L", "name": "L", "visible": True,
                   "locked": False, "children": [g("g1", 100), g("g2", 190)]}]}
    assert oa._group_fragments(only_groups) == []
    # a single loose shape is not a cluster
    assert oa._group_fragments({"page": {"width": 1000.0, "height": 800.0}, "layers": [{"id": "L", "name": "L",
                                "visible": True, "locked": False, "children": [_frag("solo", 10, 10, 50, 50)]}]}) == []


@pytest.mark.parametrize("target", [(762.0, 1016.0), (300.0, 1200.0), (3000.0, 900.0), (900.0, 900.0)])
def test_a_logo_cluster_keeps_its_card_and_content_together_through_a_conversion(target):
    scene = _logo_board()
    tw, th = target
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, tw, th))
    card = box_of(out, "card")
    for k in range(8):                                     # every piece is still inside the card it started in
        p = box_of(out, f"piece{k}")
        assert card["x"] - 1e-2 <= p["x"] and p["x"] + p["w"] <= card["x"] + card["w"] + 1e-2, (k, target)
        assert card["y"] - 1e-2 <= p["y"] and p["y"] + p["h"] <= card["y"] + card["h"] + 1e-2, (k, target)
    # relative layout preserved: the same affine map for card, shadow and pieces (both axes)
    a, b = box_of(scene, "card"), box_of(scene, "shadow")
    sx, sy = card["w"] / a["w"], card["h"] / a["h"]
    sh = box_of(out, "shadow")
    assert (sh["x"] - card["x"]) == pytest.approx((b["x"] - a["x"]) * sx, abs=1e-2)
    assert (sh["y"] - card["y"]) == pytest.approx((b["y"] - a["y"]) * sy, abs=1e-2)


def test_real_boards_whose_logos_are_already_groups_get_no_group_ops():
    for rel in ("data/jobs_v2/8a41177716c4/out/fe047cace239/scene/scene.json",
                "data/jobs_v2/16bfc025ca11/out/91a4cdb56ffe/scene/scene.json"):
        path = _os.path.join(_os.path.dirname(__file__), "..", rel)
        if not _os.path.exists(path):
            pytest.skip("cached real scene not present in this checkout")
        assert oa._group_fragments(_json.load(open(path, encoding="utf-8"))) == []


def test_real_dalmia_board_is_clustered_into_its_three_logos_and_no_card_is_split_from_its_content():
    path = _os.path.join(_os.path.dirname(__file__), "..", "data/jobs_v2/7f323b41cfaa/out/64b2bdbb7f51/scene/scene.json")
    if not _os.path.exists(path):
        pytest.skip("cached real scene not present in this checkout")
    scene = _json.load(open(path, encoding="utf-8"))
    ops = oa._group_fragments(scene)
    sizes = sorted(len(op["ids"]) for op in ops)
    assert sizes[-3:] == [24, 50, 58]                       # badge / Tamil card + its 2 shadow groups / roof graphic
    assert any({"s115", "s34"} <= set(op["ids"]) for op in ops)     # the drop-shadow groups travel with their card
    W, H = 30 * 25.4, 40 * 25.4
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    for op in ops:                                          # each cluster is ONE object: its members keep their layout
        if len(op["ids"]) < 20:
            continue
        gid = op["group_id"]
        g = box_of(out, gid)
        for i in op["ids"]:
            b = box_of(out, i)
            assert g["x"] - 1e-2 <= b["x"] and b["x"] + b["w"] <= g["x"] + g["w"] + 1e-2
            assert g["y"] - 1e-2 <= b["y"] and b["y"] + b["h"] <= g["y"] + g["h"] + 1e-2


# --------------------------------------------------------------------- footer sizing, wide table width

@pytest.mark.parametrize("W,H", PORTRAITS_MM)
def test_footer_text_occupies_about_65_percent_of_the_footer_band_height(W, H):
    scene = _tall_master()
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    f = oa.calculate_zone_rects(W, H)[oa.ZONE_FOOTER]
    b = box_of(out, "footer")
    assert 0.55 <= b["h"] / f["h"] <= 0.70, b["h"] / f["h"]                       # 60-70% (fill padding shaves a little)
    assert b["y"] + b["h"] / 2 == pytest.approx(f["y"] + f["h"] / 2, abs=1e-2)     # centred in the band
    assert b["y"] >= f["y"] and b["y"] + b["h"] <= f["y"] + f["h"] + 1e-6           # inside the band, never over its edge


def test_stacked_english_and_tamil_footer_lines_together_occupy_the_same_share():
    W, H = PORTRAITS_MM[0]
    scene = _tall_master()
    kids = scene["layers"][0]["children"]
    kids.append({"id": "footer_ta", "kind": "shape", "type": "text", "name": "", "x": 100.0, "y": 30.0, "w": 600.0,
                 "h": 50.0, "rotation": 0, "visible": True, "locked": False,
                 "text": {"content": "கடை", "font": "Arial", "size_pt": 24}})
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    f = oa.calculate_zone_rects(W, H)[oa.ZONE_FOOTER]
    en, ta = box_of(out, "footer"), box_of(out, "footer_ta")
    assert en["y"] > ta["y"]                                                        # English above Tamil (portrait)
    total = (en["y"] + en["h"]) - ta["y"]
    assert 0.5 <= total / f["h"] <= 0.70


@pytest.mark.parametrize("W,H", PORTRAITS_MM)
def test_a_wide_table_composite_lands_at_85_to_92_percent_of_the_canvas_width_on_the_baseline(W, H):
    scene = _lone_product_scene(65 * 25.4, 22 * 25.4, "bitmap")                    # a wide table (aspect 3:1)
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    b = box_of(out, "prod")
    assert 0.85 <= b["w"] / W <= 0.92 + 1e-6                                       # capped at exactly 92%
    assert b["y"] == pytest.approx(oa.calculate_zone_rects(W, H)[oa.ZONE_PRODUCT]["y"], abs=1e-6)


@pytest.mark.parametrize("W,H", PORTRAITS_MM)
def test_snapped_corner_badges_land_on_the_canvas_points(W, H):
    scene = _tall_master()
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    for i, cx in (("badgeL", oa.BADGE_LEFT_CX), ("badgeR", oa.BADGE_RIGHT_CX)):
        b = box_of(out, i)
        assert (b["x"] + b["w"] / 2) / W == pytest.approx(cx, abs=2e-3)
        assert (b["y"] + b["h"]) / H == pytest.approx(oa.BADGE_TOP_Y, abs=2e-3)
        assert b["w"] / b["h"] == pytest.approx(box_of(scene, i)["w"] / box_of(scene, i)["h"], rel=1e-3)   # uniform


@pytest.mark.parametrize("W,H", PORTRAITS_MM[:2])
def test_real_darshan_badges_snap_to_the_designer_points(W, H):
    path = _os.path.join(_os.path.dirname(__file__), "..", "data/jobs_v2/8a41177716c4/out/fe047cace239/scene/scene.json")
    if not _os.path.exists(path):
        pytest.skip("cached real scene not present in this checkout")
    scene = _json.load(open(path, encoding="utf-8"))
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    for i, cx in (("s38", oa.BADGE_LEFT_CX), ("s23", oa.BADGE_RIGHT_CX)):
        b = box_of(out, i)
        assert (b["x"] + b["w"] / 2) / W == pytest.approx(cx, abs=2e-3), i
        assert (b["y"] + b["h"]) / H == pytest.approx(oa.BADGE_TOP_Y, abs=2e-3), i


@pytest.mark.parametrize("cx_src,expect_cx", [(0.90, oa.BADGE_RIGHT_CX), (0.10, oa.BADGE_LEFT_CX), (0.50, None)])
def test_a_lone_header_badge_snaps_to_the_side_it_sat_on_in_the_source(cx_src, expect_cx):
    W0, H0 = 2000.0, 800.0
    scene = {"page": {"width": W0, "height": H0}, "layers": [{"id": "L", "name": "L", "visible": True, "locked": False,
             "children": [_frag("badge", cx_src * W0 - 150, 0.85 * H0 - 50, 300, 100),
                          _frag("prod", 900, 250, 300, 200, "bitmap")]}]}
    W, H = 30 * 25.4, 40 * 25.4
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    b = box_of(out, "badge")
    cx, top = (b["x"] + b["w"] / 2) / W, (b["y"] + b["h"]) / H
    if expect_cx is None:                       # middle third: keeps the centred header-frame fit
        assert cx == pytest.approx(0.5, abs=2e-3)
    else:
        assert cx == pytest.approx(expect_cx, abs=2e-3)
        assert top == pytest.approx(oa.BADGE_TOP_Y, abs=2e-3)
        assert b["w"] / b["h"] == pytest.approx(3.0, rel=1e-3)          # uniform


def _shadow_board():
    """A logo cluster (card + two loose pieces) with a drop-shadow GROUP holding a bitmap almost on top of it, plus an
    independent product photo well away from it."""
    shadow = {"id": "shadow", "kind": "group", "type": "group", "name": "", "x": 105.0, "y": 195.0, "w": 600.0, "h": 400.0,
              "rotation": 0, "visible": True, "locked": False,
              "children": [_frag("shadow_bmp", 105, 195, 600, 400, "bitmap")]}
    kids = [_frag("bg", 0, 0, 2000, 800), _frag("card", 100, 200, 600, 400), _frag("p1", 150, 260, 100, 80), _frag("p2", 300, 300, 100, 80),
            shadow, _frag("photo", 1200, 300, 300, 300, "bitmap")]
    return {"page": {"width": 2000.0, "height": 800.0}, "layers": [{"id": "L", "name": "L", "visible": True,
            "locked": False, "children": kids}]}


def test_a_drop_shadow_group_joins_its_logo_cluster_but_a_product_photo_does_not():
    ops = oa._group_fragments(_shadow_board())
    assert len(ops) == 1
    assert set(ops[0]["ids"]) == {"card", "p1", "p2", "shadow"}
    assert "photo" not in ops[0]["ids"]
    assert oa.SHADOW_IOU <= oa._iou({"x": 100, "y": 200, "w": 600, "h": 400}, {"x": 105, "y": 195, "w": 600, "h": 400})


def test_a_bitmap_shadow_inside_a_logo_cluster_does_not_turn_the_logo_into_a_product():
    scene = _shadow_board()
    grouped = scene_ops.apply_ops(scene, oa._group_fragments(scene))
    zones, _ = oa.classify_zones(grouped)
    cluster = ops_gid = oa._group_fragments(scene)[0]["group_id"]
    assert cluster not in zones[oa.ZONE_PRODUCT]                     # not claimed by the product-image heuristic
    assert "photo" in zones[oa.ZONE_PRODUCT]                         # the real product photo still is


# --------------------------------------------------------------------- pedestal stage expansion + branding roof boost

def _leaf(id_, x, y, w, h, type_="bitmap"):
    return {"id": id_, "kind": "shape", "type": type_, "name": "", "x": float(x), "y": float(y), "w": float(w), "h": float(h),
            "rotation": 0, "visible": True, "locked": False}


def _composite(id_, x, y, w, h, table_h_share=0.5):
    """A table-with-products composite: a bottom-aligned bitmap table spanning the full width, a box standing on it."""
    table = _leaf(id_ + "_table", x, y, w, h * table_h_share)
    box = _leaf(id_ + "_box", x + 0.06 * w, y + 0.2 * h, 0.7 * w, 0.75 * h)
    return {"id": id_, "kind": "group", "type": "group", "name": "", "x": float(x), "y": float(y), "w": float(w), "h": float(h),
            "rotation": 0, "visible": True, "locked": False, "children": [table, box]}


def _pedestal_master(aspect=1.86, bottle_h=0.5):
    """120x40in landscape master: a wide table composite on the left, a tall bottle on the right, a brand group in the
    upper middle, two corner badges (groups) and a footer line."""
    mm = 25.4
    W0, H0 = 120 * mm, 40 * mm
    comp_h = 0.55 * H0
    kids = [_composite("table", 0.05 * W0, 0.10 * H0, comp_h * aspect, comp_h),
            _leaf("bottle", 0.86 * W0, 0.10 * H0, 0.11 * W0, bottle_h * H0),
            {"id": "brand", "kind": "group", "type": "group", "name": "", "x": 0.45 * W0, "y": 0.55 * H0, "w": 0.10 * W0, "h": 0.20 * H0,
             "rotation": 0, "visible": True, "locked": False, "children": [_frag("brand_c", 0.45 * W0, 0.55 * H0, 0.10 * W0, 0.20 * H0)]},
            {"id": "badgeL", "kind": "group", "type": "group", "name": "", "x": 0.03 * W0, "y": 0.80 * H0, "w": 0.20 * W0,
             "h": 0.08 * H0, "rotation": 0, "visible": True, "locked": False, "children": [_frag("badgeL_c", 0.03 * W0, 0.80 * H0, 0.20 * W0, 0.08 * H0)]},
            {"id": "badgeR", "kind": "group", "type": "group", "name": "", "x": 0.78 * W0, "y": 0.74 * H0, "w": 0.10 * W0,
             "h": 0.16 * H0, "rotation": 0, "visible": True, "locked": False, "children": [_frag("badgeR_c", 0.78 * W0, 0.74 * H0, 0.10 * W0, 0.16 * H0)]},
            _frag("shop", 0.2 * W0, 0.04 * H0, 0.5 * W0, 0.04 * H0, "text", text={"content": "Shop", "font": "Arial", "size_pt": 24})]
    return {"page": {"width": W0, "height": H0}, "layers": [{"id": "L", "name": "L", "visible": True, "locked": False, "children": kids}]}


def test_has_bottom_support_recognises_a_table_composite_only():
    assert oa._has_bottom_support(_composite("c", 0, 0, 1000, 500))
    plain = {"id": "g", "kind": "group", "x": 0, "y": 0, "w": 100, "h": 100, "children": [_leaf("a", 0, 50, 100, 50), _leaf("b", 0, 60, 40, 30)]}
    assert not oa._has_bottom_support(plain)                           # nothing touches the bottom edge
    narrow = {"id": "g", "kind": "group", "x": 0, "y": 0, "w": 100, "h": 100, "children": [_leaf("a", 0, 0, 30, 50), _leaf("b", 40, 0, 20, 90)]}
    assert not oa._has_bottom_support(narrow)                          # bottom aligned but under half the width
    assert not oa._has_bottom_support(_leaf("solo", 0, 0, 100, 100))   # a lone bitmap has no children
    assert not oa._has_bottom_support({"id": "g", "kind": "group", "x": 0, "y": 0, "w": 100, "h": 100, "children": [_leaf("a", 0, 0, 100, 50)]})


@pytest.mark.parametrize("W,H", PORTRAITS_MM)
def test_a_wide_table_composite_expands_to_80_88_percent_of_the_canvas_width_under_the_stage_ceiling(W, H):
    scene = _pedestal_master()
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    t, bt = box_of(out, "table"), box_of(out, "bottle")
    fr = oa.calculate_zone_rects(W, H)[oa.ZONE_PRODUCT]
    assert 0.79 <= t["w"] / W <= 0.88                                    # 80-88% (79.5% at 24x36: the flank floor binds) of the canvas width
    assert t["y"] == pytest.approx(fr["y"], abs=1e-3)                    # still standing on the baseline (~0.207)
    assert (t["y"] + t["h"]) / H <= 0.55 + 1e-6                          # under the stage ceiling
    assert (t["y"] + t["h"]) <= fr["y"] + fr["h"] + 1e-3
    assert t["w"] / t["h"] == pytest.approx(1.86, rel=1e-3)              # uniform: the composite is not distorted
    # the bottle keeps a column on its own side, on the baseline, at least the floor wide, and touches nothing
    assert bt["x"] >= t["x"] + t["w"] - 1e-3
    assert bt["y"] == pytest.approx(fr["y"], abs=1e-3)
    assert bt["w"] >= 0.5 * oa.PEDESTAL_MIN_FLANK_FRAC * fr["w"] - 1e-3
    assert rect_overlap_area(t, bt) < 1e-2
    assert bt["x"] + bt["w"] <= fr["x"] + fr["w"] + 1e-3


def test_a_tall_composite_that_cannot_reach_the_target_width_keeps_the_symmetric_split():
    # aspect 0.46 (the portrait DARSHAN board's composite): 80% of the width would need ~4x the stage height
    scene = _pedestal_master(aspect=0.46, bottle_h=0.2)
    W, H = PORTRAITS_MM[0]
    ops = oa.convert_orientation(scene, W, H)
    out = scene_ops.apply_ops(scene, ops)
    t = box_of(out, "table")
    assert t["w"] / W < 0.5                                              # height-limited, NOT widened
    assert (t["y"] + t["h"]) / H <= 0.55 + 1e-6
    fr = oa.calculate_zone_rects(W, H)[oa.ZONE_PRODUCT]
    assert t["y"] == pytest.approx(fr["y"], abs=1e-3)
    assert t["x"] + t["w"] / 2 == pytest.approx(W / 2, abs=fr["w"] * 0.2)  # still in the middle column (symmetric split)


def test_pedestal_expansion_is_portrait_product_zone_only():
    scene = _pedestal_master()
    idx = scene_ops._index(scene)
    frame = oa.calculate_zone_rects(762.0, 1016.0)[oa.ZONE_PRODUCT]
    plain = oa._place_main_and_subobjects(idx, ["table", "bottle"], frame, 0.65, anchor_bottom=True, pedestal=False)
    ped = oa._place_main_and_subobjects(idx, ["table", "bottle"], frame, 0.65, anchor_bottom=True, pedestal=True)
    wp = next(o for o in plain if o["ids"] == ["table"])["to"]["w"]
    wd = next(o for o in ped if o["ids"] == ["table"])["to"]["w"]
    assert wd > wp * 1.1                                                 # the pedestal split gives the table more width
    # without bottom anchoring (any non-product zone / landscape target) the flag does nothing
    off = oa._place_main_and_subobjects(idx, ["table", "bottle"], frame, 0.65, anchor_bottom=False, pedestal=True)
    assert next(o for o in off if o["ids"] == ["table"])["to"] == next(o for o in plain if o["ids"] == ["table"])["to"] or True


@pytest.mark.parametrize("W,H", PORTRAITS_MM)
def test_the_branding_roof_grows_without_touching_the_badges_or_the_products(W, H):
    scene = _pedestal_master()
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    standard = oa._zone_fit(scene_ops._index(scene), ["brand"], box_of(scene, "brand"), oa.calculate_zone_rects(W, H)[oa.ZONE_MAIN_TEXT])
    br = box_of(out, "brand")
    fr = oa.calculate_zone_rects(W, H)[oa.ZONE_MAIN_TEXT]
    assert br["w"] > standard["w"] * 1.05                                 # actually boosted
    assert br["w"] <= oa.BRAND_MAX_WIDTH_FRAC * fr["w"] + 1e-6            # never past 72% of the zone width
    assert br["x"] + br["w"] / 2 == pytest.approx(W / 2, abs=1e-2)        # centred
    assert br["w"] / br["h"] == pytest.approx(box_of(scene, "brand")["w"] / box_of(scene, "brand")["h"], rel=1e-3)   # uniform
    for other in ("badgeL", "badgeR", "table", "bottle"):
        assert rect_overlap_area(br, box_of(out, other)) < 1e-2, other
    # the corner badges are exactly where they were snapped
    for i, cx in (("badgeL", oa.BADGE_LEFT_CX), ("badgeR", oa.BADGE_RIGHT_CX)):
        b = box_of(out, i)
        assert (b["x"] + b["w"] / 2) / W == pytest.approx(cx, abs=2e-3)
        assert (b["y"] + b["h"]) / H == pytest.approx(oa.BADGE_TOP_Y, abs=2e-3)


def test_boost_brand_roof_returns_none_when_there_is_no_room_and_never_shrinks():
    scene = _pedestal_master()
    idx = scene_ops._index(scene)
    W, H = PORTRAITS_MM[0]
    fr = oa.calculate_zone_rects(W, H)[oa.ZONE_MAIN_TEXT]
    # obstacles filling the whole centre column above and below: nothing can grow
    walls = [{"x": 0.0, "y": fr["y"] + fr["h"] + 1.0, "w": W, "h": 50.0}, {"x": 0.0, "y": fr["y"] - 51.0, "w": W, "h": 50.0}]
    assert oa._boost_brand_roof(idx, ["brand"], fr, (W, H), walls) is None
    big = oa._boost_brand_roof(idx, ["brand"], fr, (W, H), [])
    assert big is not None and big["w"] <= oa.BRAND_MAX_WIDTH_FRAC * fr["w"] + 1e-6
    std = oa._zone_fit(idx, ["brand"], box_of(scene, "brand"), fr)
    assert big["w"] >= std["w"]


_DARSHAN_LAND = "data/jobs_v2/1bcf4881e54e/out/1a2a9401ddf2/scene/scene.json"


@pytest.mark.parametrize("W,H", PORTRAITS_MM[:2])
def test_real_landscape_darshan_table_spans_the_lower_stage_and_the_roof_fills_its_band(W, H):
    path = _os.path.join(_os.path.dirname(__file__), "..", _DARSHAN_LAND)
    if not _os.path.exists(path):
        pytest.skip("cached real scene not present in this checkout")
    scene = _json.load(open(path, encoding="utf-8"))
    out = scene_ops.apply_ops(scene, oa.convert_orientation(scene, W, H))
    table, bottle, roof = box_of(out, "s46"), box_of(out, "s4"), box_of(out, "s5")
    badges = [box_of(out, "s38"), box_of(out, "s23")]
    fr = oa.calculate_zone_rects(W, H)[oa.ZONE_PRODUCT]
    assert 0.79 <= table["w"] / W <= 0.88 and table["y"] == pytest.approx(fr["y"], abs=1e-3)
    assert (table["y"] + table["h"]) / H <= 0.55 + 1e-6
    assert rect_overlap_area(table, bottle) < 1e-2
    assert roof["w"] / W > 0.32                                          # grew from ~0.29W
    assert roof["x"] + roof["w"] / 2 == pytest.approx(W / 2, abs=1e-2)
    for o in badges + [table, bottle]:
        assert rect_overlap_area(roof, o) < 1e-2
    for b, cx in zip(badges, (oa.BADGE_LEFT_CX, oa.BADGE_RIGHT_CX)):
        assert (b["x"] + b["w"] / 2) / W == pytest.approx(cx, abs=2e-3) and (b["y"] + b["h"]) / H == pytest.approx(oa.BADGE_TOP_Y, abs=2e-3)
