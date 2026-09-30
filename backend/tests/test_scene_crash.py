"""A scene export whose CorelDRAW dies part-way must not be cached (its later objects would have no images and draw nothing
on the canvas); a scene cached that way before is rebuilt once. And the test suite can never kill a real CorelDRAW."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import corel_util, main, scene_export
from tests.test_main_v2 import client  # noqa: F401

RPC_GONE = (-2147023174, "The RPC server is unavailable.", None, None)


def test_the_suite_uses_its_own_corel_tracking_file():
    real = Path(corel_util.__file__).resolve().parents[1] / "data" / "corel_launched_pids.json"
    assert corel_util.PID_FILE.resolve() != real.resolve()


def test_server_gone_detection():
    assert scene_export.server_gone(Exception(*RPC_GONE))
    assert scene_export.server_gone("s99: (-2147023174, 'The RPC server is unavailable.', None, None)")
    assert not scene_export.server_gone(Exception(-2147352567, "Exception occurred.", None, None))  # one shape's own error
    assert not scene_export.server_gone("s5: selection export produced an empty SVG")


class _Page:
    SizeWidth = 100.0
    SizeHeight = 50.0


class _Doc:
    ActivePage = _Page()

    def ExportBitmap(self, *a, **k):
        raise Exception(*RPC_GONE)


def test_export_stops_and_writes_nothing_when_corel_dies(tmp_path, monkeypatch):
    leaves = [({"id": f"s{i}"}, object()) for i in range(5)]
    monkeypatch.setattr(scene_export, "walk_page", lambda page: ([], leaves))
    calls = []

    def export_leaf(doc, node, shape, img_dir):
        calls.append(node["id"])
        if node["id"] == "s2":
            raise Exception(*RPC_GONE)

    monkeypatch.setattr(scene_export, "_export_leaf", export_leaf)
    with pytest.raises(scene_export.CorelGone, match="object 3 of 5"):
        scene_export.export_scene(_Doc(), tmp_path)
    assert calls == ["s0", "s1", "s2"]                      # stopped at the crash, did not grind through the rest
    assert not (tmp_path / "scene.json").exists()


def test_one_bad_shape_is_still_tolerated(tmp_path, monkeypatch):
    leaves = [({"id": f"s{i}"}, object()) for i in range(3)]
    monkeypatch.setattr(scene_export, "walk_page", lambda page: ([], leaves))

    def export_leaf(doc, node, shape, img_dir):
        if node["id"] == "s1":
            raise Exception(-2147352567, "Exception occurred.", None, None)

    class OkDoc(_Doc):
        def ExportBitmap(self, *a, **k):
            class F:
                def Finish(self):
                    pass
            return F()

    monkeypatch.setattr(scene_export, "_export_leaf", export_leaf)
    scene = scene_export.export_scene(OkDoc(), tmp_path)
    assert scene["stats"]["leaf_images"] == 2 and len(scene["stats"]["image_failures"]) == 1
    assert (tmp_path / "scene.json").exists()


def test_a_scene_cached_after_a_crash_is_rebuilt():
    broken = {"version": scene_export.SCENE_VERSION, "layers": [],
              "stats": {"image_failures": ["s99: (-2147023174, 'The RPC server is unavailable.', None, None)"]}}
    fine = {"version": scene_export.SCENE_VERSION, "layers": [], "stats": {"image_failures": ["s5: Exception occurred."]}}
    assert main._scene_cut_short(broken)
    assert not main._scene_cut_short(fine)
    assert not main._scene_cut_short({"layers": []})


def test_editor_rebuilds_a_crash_cut_scene_when_opened(client):  # noqa: F811
    from tests.test_editor_api import _converted_shop, _scene
    job, shop = _converted_shop(client)
    _scene(client, job, shop)
    path = main._scene_dir(job, shop) / "scene.json"
    scene = json.loads(path.read_text(encoding="utf-8"))
    scene["stats"]["image_failures"] = ["s99: (-2147023174, 'The RPC server is unavailable.', None, None)"]
    path.write_text(json.dumps(scene), encoding="utf-8")
    assert client.get(f"/api/editor/{job}/{shop}/scene").status_code == 202          # rebuilding, not served broken
    rebuilt = _scene(client, job, shop)
    assert not main._scene_cut_short(rebuilt)
