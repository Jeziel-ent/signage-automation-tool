"""Correction memory, capture step: the diff between the engine's scene and the designer's edited scene, and its storage on save."""
from __future__ import annotations

import copy

from tests.test_editor_api import _converted_shop, _scene, client  # noqa: F401  (client is a fixture)

from app import corrections


def _node(nid, x, y, w, h, kind="shape", children=None, text=None):
    n = {"id": nid, "kind": kind, "type": kind, "x": x, "y": y, "w": w, "h": h, "visible": True, "locked": False, "rotation": 0}
    if children:
        n["children"] = children
    if text is not None:
        n["text"] = text
    return n


def _scene_of(*nodes, w=1000.0, h=400.0):
    return {"page": {"width": w, "height": h}, "layers": [{"id": "L1", "name": "Layer 1", "visible": True, "locked": False,
                                                           "children": list(nodes)}]}


def _base():
    return _scene_of(_node("a", 100, 100, 200, 100), _node("b", 600, 50, 100, 100, kind="group", children=[_node("c", 600, 50, 50, 50)]))


def test_unchanged_scene_has_no_corrections():
    assert corrections.diff_scenes(_base(), _base())["changes"] == []


def test_a_move_is_recorded_as_page_fractions_with_the_objects_signature():
    edited = copy.deepcopy(_base())
    edited["layers"][0]["children"][0]["x"] += 100            # 10 % of the page width
    d = corrections.diff_scenes(_base(), edited)
    (c,) = d["changes"]
    assert c["id"] == "a" and c["action"] == "moved"
    assert c["before"]["cx"] == 0.2 and c["after"]["cx"] == 0.3
    assert (c["before"]["w"], c["before"]["h"]) == (c["after"]["w"], c["after"]["h"])
    assert c["signature"] == {"kind": "shape", "aspect": 2.0, "n_desc": 0}


def test_a_resize_and_a_group_signature():
    edited = copy.deepcopy(_base())
    g = edited["layers"][0]["children"][1]
    g["x"], g["y"], g["w"], g["h"] = 550, 0, 200, 200         # same centre, twice the size
    (c,) = corrections.diff_scenes(_base(), edited)["changes"]
    assert c["action"] == "resized" and c["signature"]["kind"] == "group" and c["signature"]["n_desc"] == 1
    assert c["after"]["w"] == 0.2 and c["after"]["h"] == 0.5


def test_nudges_below_the_threshold_are_ignored():
    edited = copy.deepcopy(_base())
    edited["layers"][0]["children"][0]["x"] += 1              # 0.1 % of the page
    assert corrections.diff_scenes(_base(), edited)["changes"] == []


def test_hidden_and_deleted_objects_are_recorded():
    edited = copy.deepcopy(_base())
    edited["layers"][0]["children"][0]["visible"] = False
    del edited["layers"][0]["children"][1]
    actions = {c["id"]: c["action"] for c in corrections.diff_scenes(_base(), edited)["changes"]}
    assert actions == {"a": "hidden", "b": "deleted"}


def test_text_edits_are_counted_not_learned():
    base = _scene_of(_node("t", 100, 100, 200, 50, kind="text", text={"content": "OLD"}))
    edited = copy.deepcopy(base)
    edited["layers"][0]["children"][0]["text"] = {"content": "NEW"}
    d = corrections.diff_scenes(base, edited)
    assert d["changes"] == [] and d["text_edits"] == 1


def test_a_page_size_edit_records_nothing():
    edited = copy.deepcopy(_base())
    edited["page"]["width"] = 2000.0
    edited["layers"][0]["children"][0]["x"] += 100
    d = corrections.diff_scenes(_base(), edited)
    assert d["page_changed"] and d["changes"] == []
    assert corrections.build_record({"id": "s"}, None, _base(), edited, None) is None


def test_build_record_carries_the_context():
    edited = copy.deepcopy(_base())
    edited["layers"][0]["children"][0]["x"] += 100
    layout = {"template": {"file": "x.cdr", "exact": True}, "confidence": "GOOD"}
    r = corrections.build_record({"id": "s1", "board_type": "GSB"}, {"brand": "agarpathi", "master_filename": "m.cdr"},
                                 _base(), edited, layout)
    assert (r["shop_id"], r["brand"], r["master_file"], r["board_type"]) == ("s1", "agarpathi", "m.cdr", "GSB")
    assert (r["page_w_mm"], r["page_h_mm"]) == (1000.0, 400.0)
    assert r["template"]["file"] == "x.cdr" and r["confidence"] == "GOOD"


def test_saving_editor_ops_stores_a_pending_correction_and_clearing_them_removes_it(client):  # noqa: F811
    import app.db as db
    job, shop = _converted_shop(client)
    scene = _scene(client, job, shop)
    top = scene["layers"][0]["children"][0]["id"]
    assert db.get_correction(shop) is None
    move = {"op": "move", "ids": [top], "dx": 60, "dy": 0}
    assert client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [move]}).status_code == 200
    c = db.get_correction(shop)
    assert c["status"] == "pending" and c["brand"] == "dalmia"
    assert c["record"]["changes"][0]["id"] == top
    assert client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": []}).status_code == 200
    assert db.get_correction(shop) is None


def test_a_failure_while_recording_never_breaks_saving(client, monkeypatch):  # noqa: F811
    job, shop = _converted_shop(client)
    scene = _scene(client, job, shop)
    import app.main as main
    monkeypatch.setattr(main.corrections, "build_record", lambda *a, **k: 1 / 0)
    top = scene["layers"][0]["children"][0]["id"]
    r = client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": [top], "dx": 60, "dy": 0}]})
    assert r.status_code == 200 and r.json()["saved"] == 1


# ------------------------------------------------------------------ applying learned corrections

from app.layout import Placed  # noqa: E402


def _placed(pid, x, y, w, h, role="logo"):
    return Placed(id=pid, name=pid, role=role, x=x, y=y, w=w, h=h)


def _rec(shop_id="s1", **over):
    base = {"shop_id": shop_id, "brand": "b", "master_file": "m.cdr", "page_w_mm": 1000.0, "page_h_mm": 400.0, "board_type": None,
            "status": "pending", "updated_at": 1.0,
            "record": {"shop_id": shop_id, "changes": [
                {"id": "a", "signature": {"kind": "shape", "aspect": 2.0, "n_desc": 0}, "action": "moved",
                 "before": {"cx": 0.2, "cy": 0.5, "w": 0.2, "h": 0.25}, "after": {"cx": 0.3, "cy": 0.4, "w": 0.2, "h": 0.25}}]}}
    base.update(over)
    return base


def test_apply_moves_the_matching_object_to_the_designers_box():
    logo = _placed("0", 100, 150, 200, 100)                      # exactly the recorded "before" box
    other = _placed("1", 600, 50, 100, 100)
    s = corrections.apply_to_placed([logo, other], 1000.0, 400.0, [_rec()])
    assert s == {"applied": 1, "skipped": 0, "conflicting": 0, "records": ["s1"]}
    assert (logo.x, logo.y, logo.w, logo.h) == (200.0, 110.0, 200.0, 100.0)
    assert (other.x, other.y) == (600, 50) and logo.warnings


def test_apply_ignores_text_unmatched_and_non_geometry_changes():
    t = _placed("t", 100, 150, 200, 100, role="shopname")        # same box, but a shop name is never moved
    s = corrections.apply_to_placed([t], 1000.0, 400.0, [_rec()])
    assert s["applied"] == 0 and s["skipped"] == 1 and (t.x, t.y) == (100, 150)
    rec = _rec()
    rec["record"]["changes"][0]["action"] = "deleted"
    assert corrections.apply_to_placed([_placed("0", 100, 150, 200, 100)], 1000.0, 400.0, [rec])["applied"] == 0


def test_designers_who_agree_are_applied_as_their_median_and_ones_who_disagree_are_not():
    agree = [_rec("a"), _rec("b"), _rec("c")]
    agree[1]["record"]["changes"][0]["after"] = {"cx": 0.305, "cy": 0.4, "w": 0.2, "h": 0.25}
    agree[2]["record"]["changes"][0]["after"] = {"cx": 0.295, "cy": 0.4, "w": 0.2, "h": 0.25}
    logo = _placed("0", 100, 150, 200, 100)
    s = corrections.apply_to_placed([logo], 1000.0, 400.0, agree)
    assert s["applied"] == 1 and s["conflicting"] == 0 and sorted(s["records"]) == ["a", "b", "c"]
    assert round(logo.x + logo.w / 2) == 300                       # the median centre (0.30 of 1000 mm)
    split = [_rec("x"), _rec("y")]
    split[1]["record"]["changes"][0]["after"] = {"cx": 0.9, "cy": 0.9, "w": 0.2, "h": 0.25}
    logo2 = _placed("0", 100, 150, 200, 100)
    s = corrections.apply_to_placed([logo2], 1000.0, 400.0, split)
    assert s["applied"] == 0 and s["conflicting"] == 1 and (logo2.x, logo2.y) == (100, 150)


def test_usable_records_need_the_same_brand_master_size_and_type():
    ok = _rec()
    rows = [ok, _rec("x", brand="other"), _rec("y", master_file="z.cdr"), _rec("w", page_w_mm=1500.0),
            _rec("r", status="rejected"), _rec("t", board_type="GSB")]
    got = corrections.usable_records(rows, "b", "M.CDR", 1002.0, 400.0, None)
    assert {r["shop_id"] for r in got} == {"s1", "t"}      # brand, master (case-insensitive) and size (within 0.5 %) match
    only = corrections.usable_records(rows, "b", "m.cdr", 1000.0, 400.0, "Frontlit")
    assert [r["shop_id"] for r in only] == ["s1"]          # a record for another board type is excluded; one without a type stays


def test_intelligence_toggle_defaults_on_and_off_stops_collection(client):  # noqa: F811
    import app.db as db
    assert client.get("/api/v2/intelligence").json() == {"enabled": True}
    assert client.put("/api/v2/intelligence", json={"enabled": "yes"}).status_code == 422
    assert client.put("/api/v2/intelligence", json={"enabled": False}).json() == {"enabled": False}
    job, shop = _converted_shop(client)
    top = _scene(client, job, shop)["layers"][0]["children"][0]["id"]
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": [top], "dx": 60, "dy": 0}]})
    assert db.get_correction(shop) is None
    client.put("/api/v2/intelligence", json={"enabled": True})
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": [top], "dx": 60, "dy": 0}]})
    assert db.get_correction(shop) is not None


def test_available_counts_corrections_and_convert_with_intelligence_sets_the_flag(client):  # noqa: F811
    import app.db as db
    job, shop = _converted_shop(client)
    assert client.get(f"/api/v2/intelligence/available?ids={shop}").json() == {"available": {}}
    top = _scene(client, job, shop)["layers"][0]["children"][0]["id"]
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": [top], "dx": 60, "dy": 0}]})
    assert client.get(f"/api/v2/intelligence/available?ids={shop}").json() == {"available": {shop: 1}}
    r = client.post(f"/api/v2/shops/{shop}/convert", json={"use_intelligence": True})
    assert r.json() == {"status": "queued", "use_intelligence": True}
    assert db.get_shop(shop)["use_intelligence"] == 1
    assert db.get_editor_ops(shop) == []                       # the old board's edits are discarded with it
    import time
    for _ in range(100):
        if client.get(f"/api/v2/shops/{shop}/status").json()["status"] == "done":
            break
        time.sleep(0.05)
    client.post(f"/api/v2/shops/{shop}/convert")
    assert db.get_shop(shop)["use_intelligence"] == 0          # a plain Convert is the engine alone
