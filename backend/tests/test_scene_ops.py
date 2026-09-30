"""Operation replay (scene_ops.py): the shared golden cases (also run against the
JS reducer in frontend/src/editor/ops.test.mjs) plus properties that must hold
for any op list - purity, determinism, and replay == step-by-step application.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app import product_engine as pe
from app import scene_ops
from app.scene_ops import OpError, apply_ops

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "ops_golden.json").read_text(encoding="utf-8"))
TOL = 1e-3


def project(node):
    p = [node["id"], node["x"], node["y"], node["w"], node["h"]]
    if node.get("children"):
        p.append([project(c) for c in node["children"]])
    return p


def assert_tree_close(actual, expected, path=""):
    assert len(actual) == len(expected), f"{path}: {[a[0] for a in actual]} != {[e[0] for e in expected]}"
    for a, e in zip(actual, expected):
        assert a[0] == e[0], f"{path}: order/id mismatch {a[0]} != {e[0]}"
        for i, name in enumerate("xywh", start=1):
            assert abs(a[i] - e[i]) < TOL, f"{path}/{a[0]}.{name}: {a[i]} != {e[i]}"
        assert (len(a) > 5) == (len(e) > 5), f"{path}/{a[0]}: children presence differs"
        if len(e) > 5:
            assert_tree_close(a[5], e[5], f"{path}/{a[0]}")


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["name"])
def test_golden_case(case):
    out = apply_ops(GOLDEN["base"], case["ops"])
    layer = out["layers"][0]
    if "expect" in case:
        assert_tree_close([project(n) for n in layer["children"]], case["expect"])
    if "expect_ids" in case:
        assert [n["id"] for n in layer["children"]] == case["expect_ids"]
    if "expect_page" in case:
        assert [out["page"]["width"], out["page"]["height"]] == case["expect_page"]
    for nid, want in case.get("expect_text", {}).items():
        node = scene_ops.find_node(out, nid)
        for k, v in want.items():
            assert node["text"][k] == v
    for nid in case.get("expect_stale", []):
        assert scene_ops.find_node(out, nid).get("stale") is True
    for nid, want in case.get("expect_visible", {}).items():
        target = layer if nid == layer["id"] else scene_ops.find_node(out, nid)
        assert target["visible"] is want


@pytest.mark.parametrize("case", GOLDEN["errors"], ids=lambda c: c["name"])
def test_golden_error(case):
    with pytest.raises(OpError, match=case["error"]):
        apply_ops(GOLDEN["base"], case["ops"])


def test_apply_ops_does_not_mutate_its_input():
    before = copy.deepcopy(GOLDEN["base"])
    for case in GOLDEN["cases"]:
        apply_ops(GOLDEN["base"], case["ops"])
    assert GOLDEN["base"] == before


def test_replay_is_deterministic_and_equals_stepwise_application():
    ops = [
        {"op": "move", "ids": ["g"], "dx": 3, "dy": 4},
        {"op": "resize", "ids": ["g"], "from": {"x": 13, "y": 14, "w": 80, "h": 30}, "to": {"x": 13, "y": 14, "w": 40, "h": 30}},
        {"op": "order", "id": "a", "mode": "front"},
        {"op": "group", "ids": ["a", "t"], "group_id": "G2"},
        {"op": "visibility", "id": "G2", "visible": False},
    ]
    once = apply_ops(GOLDEN["base"], ops)
    assert once == apply_ops(GOLDEN["base"], ops)
    step = copy.deepcopy(GOLDEN["base"])
    for op in ops:
        step = apply_ops(step, [op])
    assert step == once


def test_ops_survive_a_json_round_trip():
    ops = [c["ops"][0] for c in GOLDEN["cases"][:5]]
    assert apply_ops(GOLDEN["base"], json.loads(json.dumps(ops))) == apply_ops(GOLDEN["base"], ops)


def test_error_message_names_the_failing_op_index():
    ops = [{"op": "move", "ids": ["a"], "dx": 1, "dy": 1}, {"op": "move", "ids": ["zzz"], "dx": 1, "dy": 1}]
    with pytest.raises(OpError, match=r"op #1 \(move\)"):
        apply_ops(GOLDEN["base"], ops)


def test_locked_objects_and_layers_reject_edits():
    scene = copy.deepcopy(GOLDEN["base"])
    scene["layers"][0]["children"][0]["locked"] = True
    with pytest.raises(OpError, match="locked"):
        apply_ops(scene, [{"op": "move", "ids": ["a"], "dx": 1, "dy": 1}])
    scene = copy.deepcopy(GOLDEN["base"])
    scene["layers"][0]["locked"] = True
    with pytest.raises(OpError, match="locked layer"):
        apply_ops(scene, [{"op": "delete", "ids": ["a"]}])


def test_missing_field_is_an_operror_not_a_keyerror():
    with pytest.raises(OpError, match="missing field"):
        apply_ops(GOLDEN["base"], [{"op": "move", "ids": ["a"]}])


@pytest.mark.parametrize("case", GOLDEN["layer_cases"], ids=lambda c: c["name"])
def test_golden_layer_order(case):
    out = apply_ops(GOLDEN["base2"], case["ops"])
    assert [l["id"] for l in out["layers"]] == case["expect_layers"]
    for nid, box in case.get("expect_boxes", {}).items():
        n = scene_ops.find_node(out, nid)
        assert [n["x"], n["y"], n["w"], n["h"]] == box


@pytest.mark.parametrize("case", GOLDEN["layer_errors"], ids=lambda c: c["name"])
def test_golden_layer_errors(case):
    with pytest.raises(OpError, match=case["error"]):
        apply_ops(GOLDEN["base2"], case["ops"])


@pytest.mark.parametrize("case", GOLDEN["powerclip_cases"], ids=lambda c: c["name"])
def test_golden_powerclip_text_case(case):
    out = apply_ops(GOLDEN["base3"], case["ops"])
    for nid, want in case.get("expect_text", {}).items():
        node = scene_ops.find_node(out, nid)
        for k, v in want.items():
            assert node["text"][k] == v
    for nid, box in case.get("expect_boxes", {}).items():
        n = scene_ops.find_node(out, nid)
        assert [n["x"], n["y"], n["w"], n["h"]] == pytest.approx(box, abs=TOL), nid
    for nid in case.get("expect_stale", []):
        assert scene_ops.find_node(out, nid).get("stale") is True
    for pid, want in case.get("expect_children", {}).items():
        parent = next((l for l in out["layers"] if l["id"] == pid), None) or scene_ops.find_node(out, pid)
        assert [c["id"] for c in parent["children"]] == want, pid


@pytest.mark.parametrize("case", GOLDEN["powerclip_errors"], ids=lambda c: c["name"])
def test_golden_powerclip_errors(case):
    with pytest.raises(OpError, match=case["error"]):
        apply_ops(GOLDEN["base3"], case["ops"])


def test_a_locked_powerclip_still_rejects_moving_its_child():
    scene = copy.deepcopy(GOLDEN["base3"])
    scene["layers"][0]["children"][0]["locked"] = True
    with pytest.raises(OpError, match="locked"):
        apply_ops(scene, [{"op": "move", "ids": ["pcc"], "dx": 1, "dy": 1}])


def test_moving_a_powerclip_child_does_not_change_its_container_box():
    out = apply_ops(GOLDEN["base3"], [{"op": "move", "ids": ["pcc"], "dx": 100, "dy": 100}])
    pc = scene_ops.find_node(out, "pc")
    assert [pc["x"], pc["y"], pc["w"], pc["h"]] == [10, 10, 50, 30]   # the clip frame never follows its contents


# ------------------------------------------------------ product slots (app/product_engine.py)

def test_map_slots_matches_the_golden_mapping_and_warnings():
    slots, warnings = pe.map_slots(GOLDEN["product_base"])
    got = [[s.slot_id, s.kind, s.node_id, s.container_id] for s in slots]
    assert got == GOLDEN["product_slots"]["slots"]
    assert warnings == GOLDEN["product_slots"]["warnings"]
    # hidden shapes (directly, or via a hidden ancestor group) are never slots
    ids = {s.node_id for s in slots}
    assert "hiddenimg" not in ids
    assert "hiddenphoto" not in ids


@pytest.mark.parametrize("case", GOLDEN["product_cases"], ids=lambda c: c["name"])
def test_golden_product_case(case):
    out = apply_ops(GOLDEN["product_base"], case["ops"])
    for nid, box in case.get("expect_boxes", {}).items():
        n = scene_ops.find_node(out, nid)
        assert [n["x"], n["y"], n["w"], n["h"]] == pytest.approx(box, abs=TOL), nid
    for nid, want in case.get("expect_text", {}).items():
        node = scene_ops.find_node(out, nid)
        for k, v in want.items():
            assert node["text"][k] == v
    for nid, want in case.get("expect_asset", {}).items():
        assert scene_ops.find_node(out, nid)["image_asset"] == want
    for nid, box in case.get("expect_slot_frame", {}).items():
        n = scene_ops.find_node(out, nid)
        assert [n["slot_frame"]["x"], n["slot_frame"]["y"], n["slot_frame"]["w"], n["slot_frame"]["h"]] == pytest.approx(box, abs=TOL), nid
    for nid in case.get("expect_no_slot_frame", []):
        assert "slot_frame" not in scene_ops.find_node(out, nid)
    for nid in case.get("expect_stale", []):
        assert scene_ops.find_node(out, nid).get("stale") is True


@pytest.mark.parametrize("case", GOLDEN["product_errors"], ids=lambda c: c["name"])
def test_golden_product_error(case):
    with pytest.raises(OpError, match=case["error"]):
        apply_ops(GOLDEN["product_base"], case["ops"])


def test_swap_image_does_not_mutate_the_powerclip_frame_itself():
    out = apply_ops(GOLDEN["product_base"], [{"op": "swap_image", "id": "pcimg", "asset": {"name": "x.png", "w": 10, "h": 10}, "fit": "cover"}])
    pc = scene_ops.find_node(out, "pc2")
    assert [pc["x"], pc["y"], pc["w"], pc["h"]] == [250, 60, 40, 40]


def test_update_slot_op_builds_the_same_op_swap_image_would():
    direct = pe.swap_image_op(GOLDEN["product_base"], "gA", {"name": "y.png", "w": 20, "h": 10}, fit="contain")
    via_slot = pe.update_slot_op(GOLDEN["product_base"], "product_image:gA", asset={"name": "y.png", "w": 20, "h": 10}, fit="contain")
    assert via_slot["asset"] == direct["asset"]
    assert via_slot["frame"] == direct["frame"]
    assert via_slot["fit"] == direct["fit"] == "contain"


def _find(scene, node_id):
    for n in scene_ops.iter_nodes(scene):
        if n["id"] == node_id:
            return n
    raise KeyError(node_id)


@pytest.mark.parametrize("case", GOLDEN["text_format_cases"], ids=lambda c: c["name"])
def test_golden_text_format(case):
    t = _find(apply_ops(GOLDEN["base"], case["ops"]), "t")
    assert t["text"] == case["expect_text"]
    assert bool(t.get("stale")) == case["expect_stale"]


@pytest.mark.parametrize("case", GOLDEN["text_format_errors"], ids=lambda c: c["name"])
def test_golden_text_format_errors(case):
    with pytest.raises(OpError, match=case["error"]):
        apply_ops(GOLDEN["base"], case["ops"])
