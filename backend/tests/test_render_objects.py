"""The single-object render: CorelDRAW draws just the edited text objects so the editor can show the real lettering."""
from __future__ import annotations

import time

from tests.test_editor_api import _converted_shop, _scene, client  # noqa: F401  (client is a fixture)
from tests.test_scene_export import _sample_doc

from app import export_replay, scene_export


def _text_ids(scene):
    return [n["id"] for n in export_replay.scene_ops.iter_nodes(scene) if n.get("type") == "text"]


def test_render_candidates_keep_only_text_leaves_and_flag_powerclip_children():
    scene = {"layers": [{"id": "L1", "children": [
        {"id": "a", "type": "text", "kind": "shape"},
        {"id": "g", "type": "group", "kind": "group", "children": [{"id": "b", "type": "text", "kind": "shape"}]},
        {"id": "pc", "type": "rectangle", "kind": "powerclip", "children": [{"id": "c", "type": "text", "kind": "shape"}]},
        {"id": "d", "type": "curve", "kind": "shape"}]}]}
    assert export_replay.render_candidates(scene, ["a", "b", "c", "d", "zzz"]) == {"a": False, "b": False, "c": True}


def test_plan_render_steps_ends_at_100_and_skips_replay_without_edits():
    assert [s["key"] for s in export_replay.plan_render_steps(3, 2)] == ["launch", "open", "replay", "render"]
    plan = export_replay.plan_render_steps(0, 1)
    assert [s["key"] for s in plan] == ["launch", "open", "render"] and plan[-1]["endPct"] == 100


def test_render_objects_writes_one_png_per_text_with_its_real_box(tmp_path):
    doc, s = _sample_doc()
    scene = scene_export.export_scene(doc, tmp_path / "scene")
    tid = f"s{s['top_text'].StaticID}"
    out = tmp_path / "render"
    out.mkdir()
    report = {"warnings": []}
    steps = []
    files = export_replay.render_objects(doc, scene, [tid, "s999"], out, report, steps.append)
    assert files == {tid: f"{tid}.png"} and (out / f"{tid}.png").is_file()
    r = report["renders"][tid]
    assert (r["x"], r["y"], r["w"], r["h"]) == (10.0, 10.0, 200.0, 40.0)
    assert any("s999" in w for w in report["warnings"])
    assert steps == ["render 1/1"]


def _wait(client, job, shop, export_id):  # noqa: F811
    for _ in range(200):
        st = client.get(f"/api/editor/{job}/{shop}/export/{export_id}").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError("render did not finish")


def test_render_endpoint_queues_a_formatless_job_that_is_not_listed_as_an_export(client):  # noqa: F811
    job, shop = _converted_shop(client)
    scene = _scene(client, job, shop)
    ids = _text_ids(scene)
    assert ids, "the mock scene should carry a text object"
    r = client.post(f"/api/editor/{job}/{shop}/render", json={"ids": ids + ["nope"], "ops": []})
    assert r.status_code == 200
    body = r.json()
    assert body["ids"] == ids
    st = _wait(client, job, shop, body["export_id"])
    assert st["status"] == "done"
    assert client.get(f"/api/editor/{job}/{shop}/exports").json() == []


def test_render_endpoint_rejects_non_text_and_bad_edits(client):  # noqa: F811
    job, shop = _converted_shop(client)
    scene = _scene(client, job, shop)
    shape = next(n for n in export_replay.scene_ops.iter_nodes(scene) if n.get("type") != "text" and not n.get("children"))
    assert client.post(f"/api/editor/{job}/{shop}/render", json={"ids": [shape["id"]], "ops": []}).status_code == 422
    ids = _text_ids(scene)
    assert client.post(f"/api/editor/{job}/{shop}/render", json={"ids": ids, "ops": [{"op": "move", "ids": ["nope"], "dx": 1, "dy": 1}]}).status_code == 422
    assert client.get(f"/api/editor/{job}/{shop}/render/xxxx/a.png").status_code == 404
