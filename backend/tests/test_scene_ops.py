"""Operation replay (scene_ops.py): the shared golden cases (also run against the
JS reducer in frontend/src/editor/ops.test.mjs) plus properties that must hold
for any op list - purity, determinism, and replay == step-by-step application.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

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
