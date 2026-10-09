"""Correction memory, capture step: the diff between the engine's scene and the designer's edited scene, and its storage on save."""
from __future__ import annotations

import copy

import pytest

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
    assert c["id"] == "a"
    assert c["action"] == "moved"
    assert c["before"]["cx"] == 0.2
    assert c["after"]["cx"] == 0.3
    assert (c["before"]["w"], c["before"]["h"]) == (c["after"]["w"], c["after"]["h"])
    assert c["signature"] == {"kind": "shape", "aspect": 2.0, "n_desc": 0}


def test_a_resize_and_a_group_signature():
    from app import scene_ops
    # a real group resize (its children are carried along): same centre, twice the size
    edited = scene_ops.apply_ops(copy.deepcopy(_base()), [{"op": "resize", "ids": ["b"], "from": {"x": 600, "y": 50, "w": 100, "h": 100},
                                                            "to": {"x": 550, "y": 0, "w": 200, "h": 200}}])
    d = corrections.diff_scenes(_base(), edited)
    assert d["nested"] == []                                   # the child just came along with its group
    (c,) = d["changes"]
    assert c["action"] == "resized"
    assert c["signature"]["kind"] == "group"
    assert c["signature"]["n_desc"] == 1
    assert c["after"]["w"] == 0.2
    assert c["after"]["h"] == 0.5


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
    assert d["changes"] == []
    assert d["text_edits"] == 1


def test_a_page_size_edit_records_nothing():
    edited = copy.deepcopy(_base())
    edited["page"]["width"] = 2000.0
    edited["layers"][0]["children"][0]["x"] += 100
    d = corrections.diff_scenes(_base(), edited)
    assert d["page_changed"]
    assert d["changes"] == []
    assert corrections.build_record({"id": "s"}, None, _base(), edited, None) is None


def test_build_record_carries_the_context():
    edited = copy.deepcopy(_base())
    edited["layers"][0]["children"][0]["x"] += 100
    layout = {"template": {"file": "x.cdr", "exact": True}, "confidence": "GOOD"}
    r = corrections.build_record({"id": "s1", "board_type": "GSB"}, {"brand": "agarpathi", "master_filename": "m.cdr"},
                                 _base(), edited, layout)
    assert (r["shop_id"], r["brand"], r["master_file"], r["board_type"]) == ("s1", "agarpathi", "m.cdr", "GSB")
    assert (r["page_w_mm"], r["page_h_mm"]) == (1000.0, 400.0)
    assert r["template"]["file"] == "x.cdr"
    assert r["confidence"] == "GOOD"


def test_saving_editor_ops_stores_a_correction_and_clearing_them_removes_it(client):  # noqa: F811
    import app.db as db
    job, shop = _converted_shop(client)
    scene = _scene(client, job, shop)
    top = scene["layers"][0]["children"][0]["id"]
    assert db.get_correction(shop) is None
    move = {"op": "move", "ids": [top], "dx": 60, "dy": 0}
    assert client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [move]}).status_code == 200
    c = db.get_correction(shop)
    assert c["brand"] == "dalmia"
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
    assert r.status_code == 200
    assert r.json()["saved"] == 1


# ------------------------------------------------------------------ applying learned corrections

from app.layout import Placed  # noqa: E402


def _placed(pid, x, y, w, h, role="logo"):
    return Placed(id=pid, name=pid, role=role, x=x, y=y, w=w, h=h)


def _rec(shop_id="s1", **over):
    base = {"shop_id": shop_id, "brand": "b", "master_file": "m.cdr", "page_w_mm": 1000.0, "page_h_mm": 400.0, "board_type": None,
            "status": "approved", "updated_at": 1.0,
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
    assert (other.x, other.y) == (600, 50)
    assert logo.warnings


def test_apply_ignores_text_unmatched_and_non_geometry_changes():
    t = _placed("t", 100, 150, 200, 100, role="shopname")        # same box, but a shop name is never moved
    s = corrections.apply_to_placed([t], 1000.0, 400.0, [_rec()])
    assert s["applied"] == 0
    assert s["skipped"] == 1
    assert (t.x, t.y) == (100, 150)
    rec = _rec()
    rec["record"]["changes"][0]["action"] = "deleted"
    assert corrections.apply_to_placed([_placed("0", 100, 150, 200, 100)], 1000.0, 400.0, [rec])["applied"] == 0


def _from_files(*recs):
    """Mark records as learned from designers' own files (the ones that must AGREE, instead of the newest editor save winning)."""
    for r in recs:
        r["record"]["source"] = "designer-dataset"
    return list(recs)


def test_designers_who_agree_are_applied_as_their_median_and_ones_who_disagree_are_not():
    agree = _from_files(_rec("a"), _rec("b"), _rec("c"))
    agree[1]["record"]["changes"][0]["after"] = {"cx": 0.305, "cy": 0.4, "w": 0.2, "h": 0.25}
    agree[2]["record"]["changes"][0]["after"] = {"cx": 0.295, "cy": 0.4, "w": 0.2, "h": 0.25}
    logo = _placed("0", 100, 150, 200, 100)
    s = corrections.apply_to_placed([logo], 1000.0, 400.0, agree)
    assert s["applied"] == 1
    assert s["conflicting"] == 0
    assert sorted(s["records"]) == ["a", "b", "c"]
    assert round(logo.x + logo.w / 2) == 300                       # the median centre (0.30 of 1000 mm)
    split = _from_files(_rec("x"), _rec("y"))
    split[1]["record"]["changes"][0]["after"] = {"cx": 0.9, "cy": 0.9, "w": 0.2, "h": 0.25}
    logo2 = _placed("0", 100, 150, 200, 100)
    s = corrections.apply_to_placed([logo2], 1000.0, 400.0, split)
    assert s["applied"] == 0
    assert s["conflicting"] == 1
    assert (logo2.x, logo2.y) == (100, 150)


def test_usable_records_need_the_same_brand_master_size_and_type():
    ok = _rec()
    rows = [ok, _rec("x", brand="other"), _rec("y", master_file="z.cdr"), _rec("w", page_w_mm=1500.0),
            _rec("r", status="rejected"), _rec("p", status="pending"), _rec("t", board_type="GSB")]   # status is ignored: every record is used
    got = corrections.usable_records(rows, "b", "M.CDR", 1002.0, 400.0, None)
    assert {r["shop_id"] for r in got} == {"s1", "r", "p", "t"}      # brand, master (case-insensitive) and size (within 0.5 %) match; no approval step
    only = corrections.usable_records(rows, "b", "m.cdr", 1000.0, 400.0, "Frontlit")
    assert {r["shop_id"] for r in only} == {"s1", "r", "p"}  # a record for another board type is excluded; one without a type stays


def test_collection_is_always_on_and_there_is_no_switch(client):  # noqa: F811
    import app.db as db
    assert client.get("/api/v2/intelligence").status_code in (404, 405)
    job, shop = _converted_shop(client)
    top = _scene(client, job, shop)["layers"][0]["children"][0]["id"]
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": [top], "dx": 60, "dy": 0}]})
    assert db.get_correction(shop) is not None


def test_available_counts_corrections_and_convert_with_intelligence_sets_the_flag(client):  # noqa: F811
    import app.db as db
    job, shop = _converted_shop(client)
    assert client.get(f"/api/v2/intelligence/available?ids={shop}").json() == {"available": {}}
    top = _scene(client, job, shop)["layers"][0]["children"][0]["id"]
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": [top], "dx": 60, "dy": 0}]})
    # a saved edit is used at once: no approval step
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


# ------------------------------------------------------------------ the learned-corrections list

def test_summarize_spells_out_each_change_in_mm():
    row = _rec()
    out = corrections.summarize(row, "SRI KAVI")
    assert out["title"] == "SRI KAVI"
    assert out["source"] == "editor"
    assert out["applies"] == ["a"]
    assert out["changes"][0]["shift"] == {"dx_mm": 100.0, "dy_mm": -40.0, "dw_mm": 0.0, "dh_mm": 0.0}


def test_summarize_marks_dataset_seeds_and_skips_shift_for_hidden():
    row = _rec(shop_id="seed:b:x")
    row["record"]["source"] = "designer-dataset"
    row["record"]["changes"].append({"id": "h", "signature": {"kind": "shape"}, "action": "hidden", "before": {"cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1}, "after": None})
    out = corrections.summarize(row)
    assert out["source"] == "designer-dataset"
    assert out["changes"][1]["shift"] is None
    assert out["applies"] == ["a", "h"]                     # a hidden object is replayed too (as a visibility op)


def test_learned_list_endpoint(client):  # noqa: F811
    job, shop = _converted_shop(client)
    top = _scene(client, job, shop)["layers"][0]["children"][0]["id"]
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": [top], "dx": 60, "dy": 0}]})
    body = client.get("/api/v2/corrections").json()
    assert list(body) == ["corrections"]
    assert body["corrections"][0]["id"] == shop
    assert body["corrections"][0]["changes"][0]["shift"]["dx_mm"] == pytest.approx(60, abs=1)
    assert client.put(f"/api/v2/corrections/{shop}/status", json={"status": "rejected"}).status_code in (404, 405)   # no approve / reject


def test_an_edit_saved_again_replaces_the_record_and_stays_used(client):  # noqa: F811
    import app.db as db
    job, shop = _converted_shop(client)
    top = _scene(client, job, shop)["layers"][0]["children"][0]["id"]
    ops = [{"op": "move", "ids": [top], "dx": 60, "dy": 0}]
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": ops})
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": ops + [{"op": "move", "ids": [top], "dx": 10, "dy": 0}]})
    assert len(db.list_corrections()) == 1
    assert client.get(f"/api/v2/intelligence/available?ids={shop}").json() == {"available": {shop: 1}}


def test_a_correction_the_engine_already_produces_is_reported_in_place_not_applied():
    rec = _rec()
    rec["record"]["changes"][0].update(before={"cx": 0.5, "cy": 0.5, "w": 0.988, "h": 1.0}, after={"cx": 0.5, "cy": 0.5, "w": 1.0, "h": 1.0})
    bg = _placed("0", 0, 0, 1000, 400)                            # the engine already fills the page, as the designer did
    s = corrections.apply_to_placed([bg], 1000.0, 400.0, [rec])
    assert s["applied"] == 0
    assert s["in_place"] == 1
    assert s["records"] == ["s1"]
    assert (bg.x, bg.y, bg.w, bg.h) == (0, 0, 1000, 400)
    assert not bg.warnings


def test_a_before_box_just_over_the_old_tolerance_still_matches():
    logo = _placed("0", 100, 150, 200 * 0.985, 100)               # 1.5 % of the page narrower than recorded: matched since 2 %
    s = corrections.apply_to_placed([logo], 1000.0, 400.0, [_rec()])
    assert s["applied"] == 1
    assert s["skipped"] == 0


def test_delete_a_correction_endpoint(client):  # noqa: F811
    from app import db
    db.save_correction({"shop_id": "gone-1", "brand": "b", "master_file": "m.cdr", "page_w_mm": 1000.0, "page_h_mm": 400.0,
                        "board_type": None, "changes": [], "nested": []})
    assert client.delete("/api/v2/corrections/gone-1").json() == {"deleted": "gone-1"}
    assert db.get_correction("gone-1") is None
    assert client.delete("/api/v2/corrections/gone-1").status_code == 404


def test_editor_corrections_are_blended_80_percent_newest_and_20_percent_average():
    new, old = _rec("new"), _rec("old")                          # records come newest first
    old["record"]["changes"][0]["after"] = {"cx": 0.9, "cy": 0.9, "w": 0.2, "h": 0.25}
    logo = _placed("0", 100, 150, 200, 100)
    s = corrections.apply_to_placed([logo], 1000.0, 400.0, [new, old])
    assert s["applied"] == 1
    assert s["conflicting"] == 0
    assert sorted(s["records"]) == ["new", "old"]
    # newest (0.3, 0.4), older (0.9, 0.9): average (0.6, 0.65); 0.8 * 0.3 + 0.2 * 0.6 = 0.36, 0.8 * 0.4 + 0.2 * 0.65 = 0.45
    assert round(logo.x + logo.w / 2, 3) == 360.0
    assert round(logo.y + logo.h / 2, 3) == 180.0
    assert corrections.blend_boxes([{"cx": 1.0, "cy": 0.0, "w": 0.5, "h": 0.5}]) == {"cx": 1.0, "cy": 0.0, "w": 0.5, "h": 0.5}   # one record: as made


def test_three_editor_corrections_blend_with_the_newest_leading():
    a, b, c = ({"cx": v, "cy": 0.5, "w": 0.2, "h": 0.2} for v in (0.2, 0.5, 0.8))      # newest first
    got = corrections.blend_boxes([a, b, c])
    assert got["cx"] == pytest.approx(0.8 * 0.2 + 0.2 * 0.5)                           # average of the three is 0.5


def test_an_editor_correction_beats_designer_file_records_but_a_change_only_the_old_one_has_still_applies():
    editor = _rec("ed")
    files = _from_files(_rec("seed"))
    files[0]["record"]["changes"][0]["after"] = {"cx": 0.9, "cy": 0.9, "w": 0.2, "h": 0.25}
    logo = _placed("0", 100, 150, 200, 100)
    assert corrections.apply_to_placed([logo], 1000.0, 400.0, [editor] + files)["records"] == ["ed"]
    other = _rec("old")
    other["record"]["changes"][0].update(id="b", before={"cx": 0.7, "cy": 0.5, "w": 0.2, "h": 0.25}, after={"cx": 0.75, "cy": 0.5, "w": 0.2, "h": 0.25})
    two = [_placed("0", 100, 150, 200, 100), _placed("1", 600, 150, 200, 100)]
    s = corrections.apply_to_placed(two, 1000.0, 400.0, [editor, other])
    assert s["applied"] == 2                                                      # each object takes the vote it has
    assert s["conflicting"] == 0
