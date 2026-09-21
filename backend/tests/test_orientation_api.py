"""POST /api/scene/convert-orientation - a stateless wrapper around orientation_adapter.py +
scene_ops.apply_ops (see main.py's convert_orientation). Not tied to a job/shop id, so the fixture
here is a bare TestClient, not a converted mock shop.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app import scene_ops


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    return TestClient(main_module.app)


def _text(id_, name, x, y, w, h, content, size_pt=24.0):
    return {"id": id_, "kind": "shape", "type": "text", "name": name, "x": x, "y": y, "w": w, "h": h,
            "rotation": 0, "visible": True, "locked": False, "text": {"content": content, "font": "Arial", "size_pt": size_pt}}


def portrait_scene() -> dict:
    return {
        "page": {"width": 400.0, "height": 1000.0},
        "layers": [{"id": "L1", "name": "Layer 1", "visible": True, "locked": False, "children": [
            {"id": "bg", "kind": "shape", "type": "rectangle", "name": "", "x": 0, "y": 0, "w": 400, "h": 1000,
             "rotation": 0, "visible": True, "locked": False},
            _text("brand", "brand_title", 50, 900, 300, 60, "BRAND", 60.0),
            {"id": "pc", "kind": "powerclip", "type": "rectangle", "name": "product_image_1", "x": 50, "y": 500,
             "w": 300, "h": 350, "rotation": 0, "visible": True, "locked": False, "frame_rect": True,
             "children": [{"id": "photo", "kind": "shape", "type": "bitmap", "name": "", "x": 60, "y": 520,
                            "w": 280, "h": 300, "rotation": 0, "visible": True, "locked": False}]},
            _text("title", "product_title", 50, 400, 300, 50, "Widget 3000", 40.0),
            _text("addr", "address", 50, 200, 300, 30, "123 Main St", 20.0),
        ]}],
    }


def test_convert_orientation_returns_the_transformed_scene_and_the_ops_that_produced_it(client):
    scene = portrait_scene()
    r = client.post("/api/scene/convert-orientation", json={"scene": scene, "target_w": 900.0, "target_h": 300.0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"scene", "ops"}
    assert body["scene"]["page"] == {"width": 900.0, "height": 300.0}
    assert body["ops"][0] == {"op": "page", "width": 900.0, "height": 300.0}
    assert all(op["op"] in ("page", "resize") for op in body["ops"])
    # reapplying the returned ops to the ORIGINAL posted scene reproduces the returned scene exactly -
    # i.e. the endpoint's response is internally consistent, not just plausible-looking numbers
    assert scene_ops.apply_ops(scene, body["ops"]) == body["scene"]
    # the request scene itself was not mutated by the endpoint
    assert scene == portrait_scene()


def test_convert_orientation_moves_the_product_image_container_not_the_inner_bitmap(client):
    scene = portrait_scene()
    r = client.post("/api/scene/convert-orientation", json={"scene": scene, "target_w": 900.0, "target_h": 300.0})
    ids_moved = {op["ids"][0] for op in r.json()["ops"] if op["op"] == "resize" and len(op["ids"]) == 1}
    assert "pc" in ids_moved
    assert "photo" not in ids_moved


@pytest.mark.parametrize("target_w,target_h", [(0, 300), (900, 0), (-1, 300), (900, -1)])
def test_convert_orientation_rejects_non_positive_targets(client, target_w, target_h):
    r = client.post("/api/scene/convert-orientation", json={"scene": portrait_scene(), "target_w": target_w, "target_h": target_h})
    assert r.status_code == 422
    assert "positive" in r.json()["detail"]


def test_convert_orientation_rejects_a_malformed_scene(client):
    r = client.post("/api/scene/convert-orientation", json={"scene": {"layers": []}, "target_w": 900.0, "target_h": 300.0})
    assert r.status_code == 422
    assert "malformed scene" in r.json()["detail"]


def test_convert_orientation_leaves_a_locked_shape_untouched_instead_of_500ing(client):
    scene = portrait_scene()
    scene["layers"][0]["children"][1]["locked"] = True  # "brand"
    r = client.post("/api/scene/convert-orientation", json={"scene": scene, "target_w": 900.0, "target_h": 300.0})
    assert r.status_code == 200
    brand = scene_ops.find_node(r.json()["scene"], "brand")
    assert [brand["x"], brand["y"], brand["w"], brand["h"]] == [50.0, 900.0, 300.0, 60.0]


def test_convert_orientation_handles_a_scene_with_no_recognizable_slots(client):
    scene = {"page": {"width": 400.0, "height": 1000.0},
             "layers": [{"id": "L1", "name": "L1", "visible": True, "locked": False, "children": [
                 {"id": "bg", "kind": "shape", "type": "rectangle", "name": "", "x": 0, "y": 0, "w": 400, "h": 1000,
                  "rotation": 0, "visible": True, "locked": False}]}]}
    r = client.post("/api/scene/convert-orientation", json={"scene": scene, "target_w": 900.0, "target_h": 300.0})
    assert r.status_code == 200
    assert scene_ops.find_node(r.json()["scene"], "bg")["w"] == 900.0
