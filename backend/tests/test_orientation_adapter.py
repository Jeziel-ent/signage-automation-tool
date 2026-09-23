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


# ------------------------------------------ untagged-master fallback (Y-banded "other" absorption)

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
    scene = portrait_scene()
    scene["layers"][0]["children"].append(
        {"id": "deco", "kind": "shape", "type": "curve", "name": "", "x": 200, "y": 500, "w": 20, "h": 20,
         "rotation": 0, "visible": True, "locked": False})
    ops = oa.convert_orientation(scene, 900.0, 300.0)
    out = scene_ops.apply_ops(scene, ops)
    b = box_of(out, "deco")
    cx, cy = (b["x"] + b["w"] / 2) / 900.0, (b["y"] + b["h"] / 2) / 300.0
    assert cx == pytest.approx(0.525, abs=1e-3)   # unchanged: still the plain proportional fallback
    assert cy == pytest.approx(0.51, abs=1e-3)


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


def test_convert_orientation_still_stretches_a_text_only_zone_non_uniformly(converted):
    # "brand" (the header zone, text only - no bitmap anywhere in it) keeps the earlier fill-to-zone
    # behaviour: its aspect ratio is free to change so it occupies the full zone bounds.
    scene, _, out = converted
    orig = box_of(scene, "brand")
    new = box_of(out, "brand")
    assert new["w"] / new["h"] != pytest.approx(orig["w"] / orig["h"], rel=1e-3)


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
    frame = oa.calculate_zone_rects(900.0, 300.0)[oa.ZONE_PRODUCT]
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
    scene = portrait_scene()
    scene["layers"][0]["children"].append(
        {"id": "deco", "kind": "shape", "type": "curve", "name": "", "x": 200, "y": 500, "w": 20, "h": 20,
         "rotation": 0, "visible": True, "locked": False})
    ops = oa.convert_orientation(scene, 900.0, 300.0)
    out = scene_ops.apply_ops(scene, ops)
    b = box_of(out, "deco")
    cx, cy = (b["x"] + b["w"] / 2) / 900.0, (b["y"] + b["h"] / 2) / 300.0
    assert cx == pytest.approx(0.525, abs=1e-3)   # (200 + 20/2) / 400 on the original page
    assert cy == pytest.approx(0.51, abs=1e-3)    # (500 + 20/2) / 1000 on the original page
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
    assert zones[oa.ZONE_PRODUCT] == []
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
