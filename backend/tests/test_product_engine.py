"""Unit tests for app/product_engine.py's own helpers (kind_from_name, aspect_fit, resolve_frame) -
the golden cross-language cases in test_scene_ops.py (test_golden_product_case /
test_map_slots_matches_the_golden_mapping_and_warnings) cover the ops/mapping end to end against the
shared fixture; this file covers the pure math and edge cases that fixture doesn't exercise.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app import product_engine as pe
from app import scene_ops
from app.scene_ops import OpError

GOLDEN_BASE = json.loads((Path(__file__).parent / "fixtures" / "ops_golden.json").read_text(encoding="utf-8"))["product_base"]


def test_kind_from_name_is_case_insensitive_and_prefix_based():
    assert pe.kind_from_name("Product_Image_1") == pe.SLOT_PRODUCT_IMAGE
    assert pe.kind_from_name("BRAND_TITLE") == pe.SLOT_BRAND_TITLE
    assert pe.kind_from_name("addressee") == pe.SLOT_ADDRESS   # prefix match, not exact
    assert pe.kind_from_name("logo") is None
    assert pe.kind_from_name(None) is None
    assert pe.kind_from_name("") is None


def test_is_bitmap():
    assert pe.is_bitmap({"kind": "shape", "type": "bitmap"})
    assert not pe.is_bitmap({"kind": "shape", "type": "curve"})
    assert not pe.is_bitmap({"kind": "group", "type": "bitmap"})


class TestCheckAsset:
    def test_accepts_a_well_formed_asset(self):
        assert pe.check_asset({"name": " a.png ", "w": "100", "h": 50}) == {"name": "a.png", "w": 100.0, "h": 50.0}

    @pytest.mark.parametrize("asset", [None, {}, {"name": ""}, {"name": "a", "w": 0, "h": 10},
                                        {"name": "a", "w": -1, "h": 10}, {"name": "a", "w": "x", "h": 10}])
    def test_rejects_a_malformed_asset(self, asset):
        with pytest.raises(OpError, match="asset must be an object"):
            pe.check_asset(asset)


class TestAspectFit:
    def test_contain_centres_the_whole_image_in_a_wider_frame(self):
        box = pe.aspect_fit(200, 100, {"x": 0, "y": 0, "w": 100, "h": 100})
        assert box == {"x": 0, "y": 25, "w": 100, "h": 50}

    def test_cover_fills_the_frame_and_overflows(self):
        box = pe.aspect_fit(200, 100, {"x": 0, "y": 0, "w": 100, "h": 100}, fit="cover")
        assert box == {"x": -50, "y": 0, "w": 200, "h": 100}

    def test_padding_insets_the_frame_before_fitting(self):
        box = pe.aspect_fit(100, 100, {"x": 0, "y": 0, "w": 100, "h": 100}, padding=10)
        assert box == {"x": 10, "y": 10, "w": 80, "h": 80}

    def test_square_asset_in_a_taller_frame_fits_by_width(self):
        box = pe.aspect_fit(50, 50, {"x": 0, "y": 0, "w": 40, "h": 200})
        assert box == {"x": 0, "y": 80, "w": 40, "h": 40}

    def test_rejects_an_unknown_fit_mode(self):
        with pytest.raises(OpError, match="fit must be"):
            pe.aspect_fit(10, 10, {"x": 0, "y": 0, "w": 10, "h": 10}, fit="stretch")

    def test_rejects_a_non_positive_asset_size(self):
        with pytest.raises(OpError, match="asset must be"):
            pe.aspect_fit(0, 10, {"x": 0, "y": 0, "w": 10, "h": 10})

    def test_rejects_negative_padding(self):
        with pytest.raises(OpError, match="padding must be"):
            pe.aspect_fit(10, 10, {"x": 0, "y": 0, "w": 10, "h": 10}, padding=-1)

    def test_rejects_padding_that_consumes_the_whole_frame(self):
        with pytest.raises(OpError, match="padding leaves no room"):
            pe.aspect_fit(10, 10, {"x": 0, "y": 0, "w": 10, "h": 10}, padding=6)


class TestResolveFrame:
    def _scene(self, **overrides):
        node = {"id": "img", "kind": "shape", "type": "bitmap", "x": 10, "y": 10, "w": 20, "h": 10,
                "rotation": 0, "visible": True, "locked": False}
        node.update(overrides)
        return {"page": {"width": 100, "height": 100},
                "layers": [{"id": "L1", "name": "Layer 1", "visible": True, "locked": False, "children": [node]}]}

    def test_defaults_to_the_node_s_own_box(self):
        idx = scene_ops._index(self._scene())
        frame, clip_id = pe.resolve_frame(idx, "img")
        assert frame == {"x": 10, "y": 10, "w": 20, "h": 10}
        assert clip_id is None

    def test_uses_the_remembered_slot_frame_over_the_current_box(self):
        idx = scene_ops._index(self._scene(slot_frame={"x": 0, "y": 0, "w": 5, "h": 5}))
        frame, clip_id = pe.resolve_frame(idx, "img")
        assert frame == {"x": 0, "y": 0, "w": 5, "h": 5}
        assert clip_id is None

    def test_an_explicit_frame_overrides_both(self):
        idx = scene_ops._index(self._scene(slot_frame={"x": 0, "y": 0, "w": 5, "h": 5}))
        frame, clip_id = pe.resolve_frame(idx, "img", explicit={"x": 1, "y": 2, "w": 3, "h": 4})
        assert frame == {"x": 1, "y": 2, "w": 3, "h": 4}
        assert clip_id is None

    def test_cover_without_a_powerclip_is_refused(self):
        idx = scene_ops._index(self._scene())
        with pytest.raises(OpError, match="needs the image to be inside a PowerClip"):
            pe.resolve_frame(idx, "img", fit="cover")

    def test_an_explicit_frame_with_zero_size_is_refused(self):
        idx = scene_ops._index(self._scene())
        with pytest.raises(OpError, match="frame width/height must be positive"):
            pe.resolve_frame(idx, "img", explicit={"x": 0, "y": 0, "w": 0, "h": 5})

    def test_inside_a_powerclip_the_frame_is_always_the_container_s_box(self):
        scene = {"page": {"width": 100, "height": 100}, "layers": [{"id": "L1", "name": "L1", "visible": True, "locked": False, "children": [
            {"id": "pc", "kind": "powerclip", "type": "rectangle", "x": 0, "y": 0, "w": 50, "h": 50,
             "rotation": 0, "visible": True, "locked": False, "frame_rect": True, "children": [
                {"id": "img", "kind": "shape", "type": "bitmap", "x": 5, "y": 5, "w": 10, "h": 10,
                 "rotation": 0, "visible": True, "locked": False}]},
        ]}]}
        idx = scene_ops._index(scene)
        frame, clip_id = pe.resolve_frame(idx, "img")
        assert frame == {"x": 0, "y": 0, "w": 50, "h": 50}
        assert clip_id == "pc"
        with pytest.raises(OpError, match="frame differs from the PowerClip frame"):
            pe.resolve_frame(idx, "img", explicit={"x": 1, "y": 1, "w": 50, "h": 50})
        # a frame within FRAME_TOL_MM of the real one is accepted, not just an exact match
        frame2, _ = pe.resolve_frame(idx, "img", explicit={"x": 0.1, "y": 0, "w": 50, "h": 50})
        assert frame2 == {"x": 0, "y": 0, "w": 50, "h": 50}


def test_plan_image_swap_reports_the_frame_and_box_without_mutating_the_scene():
    scene = copy.deepcopy(GOLDEN_BASE)
    plan = pe.plan_image_swap(scene, "pimg1", {"name": "a.png", "w": 400, "h": 200})
    assert plan["id"] == "pimg1"
    assert plan["container_id"] is None
    assert plan["frame"] == {"x": 10, "y": 10, "w": 50, "h": 30}
    assert plan["box"] == {"x": 10, "y": 12.5, "w": 50, "h": 25}
    assert scene == GOLDEN_BASE            # read-only


def test_slot_for_node_finds_the_slot_that_owns_a_given_node():
    slot = pe.slot_for_node(GOLDEN_BASE, "pcimg")
    assert slot is not None
    assert slot.kind == pe.SLOT_PRODUCT_IMAGE
    assert slot.container_id == "pc2"
    assert pe.slot_for_node(GOLDEN_BASE, "bg") is None       # the background is never a slot


def test_update_slot_op_rejects_an_unknown_slot_id():
    with pytest.raises(OpError, match="unknown product slot"):
        pe.update_slot_op(GOLDEN_BASE, "product_image:does-not-exist", asset={"name": "a", "w": 1, "h": 1})


def test_swap_image_op_rejects_an_unknown_node_id():
    with pytest.raises(OpError, match="unknown id"):
        pe.swap_image_op(GOLDEN_BASE, "does-not-exist", {"name": "a", "w": 1, "h": 1})
