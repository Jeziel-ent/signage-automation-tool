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


# --------------------------------------------------------------------- zone_frames

def test_zone_frames_landscape_are_pairwise_disjoint_and_inside_the_page():
    frames = oa.zone_frames(900.0, 300.0)
    assert set(frames) == {oa.ZONE_HEADER, oa.ZONE_PRODUCT, oa.ZONE_MAIN_TEXT, oa.ZONE_FOOTER}
    names = list(frames)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            assert rect_overlap_area(frames[names[i]], frames[names[j]]) < TOL, (names[i], names[j])
    for f in frames.values():
        assert f["w"] > 0 and f["h"] > 0
        assert f["x"] >= 0 and f["y"] >= 0
        assert f["x"] + f["w"] <= 900.0 + TOL
        assert f["y"] + f["h"] <= 300.0 + TOL


def test_zone_frames_portrait_fallback_is_also_disjoint_and_inside_the_page():
    frames = oa.zone_frames(300.0, 900.0)
    names = list(frames)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            assert rect_overlap_area(frames[names[i]], frames[names[j]]) < TOL, (names[i], names[j])
    for f in frames.values():
        assert f["w"] > 0 and f["h"] > 0
        assert f["x"] + f["w"] <= 300.0 + TOL
        assert f["y"] + f["h"] <= 900.0 + TOL


@pytest.mark.parametrize("w,h", [(0, 100), (100, 0), (-5, 100), (100, -5)])
def test_zone_frames_rejects_non_positive_targets(w, h):
    with pytest.raises(OpError, match="must be positive"):
        oa.zone_frames(w, h)


@pytest.mark.parametrize("w,h", [(50, 50), (10000, 10), (10, 10000), (1.0, 1.0)])
def test_zone_frames_never_degenerates_across_a_wide_range_of_aspect_ratios(w, h):
    frames = oa.zone_frames(w, h)
    for f in frames.values():
        assert f["w"] > 0 and f["h"] > 0


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


def test_convert_orientation_stretches_the_background_to_exactly_fill_the_new_page(converted):
    _, _, out = converted
    bg = box_of(out, "bg")
    assert bg == {"x": 0.0, "y": 0.0, "w": 900.0, "h": 300.0}


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
    assert box_of(out, "bg") == {"x": 0.0, "y": 0.0, "w": 900.0, "h": 300.0}


def test_convert_orientation_to_a_same_size_portrait_target_still_applies_cleanly():
    scene = portrait_scene()
    ops = oa.convert_orientation(scene, scene["page"]["width"], scene["page"]["height"])
    out = scene_ops.apply_ops(scene, ops)          # must not raise
    assert out["page"] == {"width": 400.0, "height": 1000.0}
