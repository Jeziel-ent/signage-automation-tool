"""Nested corrections: what a designer changes INSIDE the master's big group (the shop-name block and its text) is learned and re-applied."""
from __future__ import annotations

import copy
import time

import pytest

from tests.test_editor_api import _converted_shop, _scene, client  # noqa: F401  (client is a fixture)

from app import corrections, scene_ops


def _node(nid, x, y, w, h, kind="shape", children=None, text=None):
    n = {"id": nid, "kind": kind, "type": kind, "x": x, "y": y, "w": w, "h": h, "visible": True, "locked": False, "rotation": 0}
    if children:
        n["children"] = children
    if text is not None:
        n["text"] = text
    return n


def _board():
    """A 1000 x 2000 mm board shaped like the real Hangyo 4 X 8 one: ONE big group > a name block group > the name text."""
    name = _node("s97", 100, 800, 800, 100, text={"content": "NEW CHENNAI BAKERY", "font": "Arial", "size_pt": 100.0, "line_spacing": 100.0})
    block = _node("s96", 100, 800, 800, 100, kind="group", children=[name])
    bg = _node("s94", 0, 0, 1000, 2000)                          # keeps the big group's box from following its children
    big = _node("s95", 0, 0, 1000, 2000, kind="group", children=[bg, block])
    return {"page": {"width": 1000.0, "height": 2000.0}, "layers": [{"id": "L1", "name": "Layer 1", "visible": True, "locked": False,
                                                                       "children": [big]}]}


def _edited(ops):
    return scene_ops.apply_ops(copy.deepcopy(_board()), ops)


DESIGNER_OPS = [
    {"op": "resize", "ids": ["s96"], "from": {"x": 100, "y": 800, "w": 800, "h": 100}, "to": {"x": 150, "y": 900, "w": 600, "h": 100}},
    {"op": "text", "id": "s97", "bold": True, "line_spacing": 80.0, "content": "A DIFFERENT NAME"},
]


def test_a_change_inside_a_group_is_now_recorded():
    d = corrections.diff_scenes(_board(), _edited(DESIGNER_OPS))
    assert d["changes"] == []                                   # the big group itself was untouched
    ids = {c["id"]: c for c in d["nested"]}
    assert set(ids) == {"s96", "s97"} or set(ids) == {"s96"} | {"s97"}
    assert ids["s96"]["action"] in ("moved+resized", "resized")
    assert ids["s96"]["path"] == ["s95"]
    assert ids["s97"]["path"] == ["s95", "s96"]


def test_text_style_is_learned_but_the_text_content_is_not():
    d = corrections.diff_scenes(_board(), _edited(DESIGNER_OPS))
    style = next(c for c in d["nested"] if c["id"] == "s97")["style"]
    assert style == {"bold": True, "line_spacing": 80.0}
    assert d["text_edits"] == 1
    assert "content" not in style


def test_a_group_move_alone_does_not_blame_its_children():
    ops = [{"op": "move", "ids": ["s95"], "dx": 50, "dy": 0}]
    d = corrections.diff_scenes(_board(), _edited(ops))
    assert [c["id"] for c in d["changes"]] == ["s95"]
    assert d["nested"] == []


def test_a_font_size_that_only_follows_the_box_is_not_an_explicit_change():
    ops = [{"op": "resize", "ids": ["s97"], "from": {"x": 100, "y": 800, "w": 800, "h": 100}, "to": {"x": 100, "y": 800, "w": 400, "h": 50}}]
    d = corrections.diff_scenes(_board(), _edited(ops))
    assert "size_pt" not in (d["nested"][0].get("style") or {})
    explicit = [{"op": "text", "id": "s97", "size_pt": 60.0}]
    d = corrections.diff_scenes(_board(), _edited(explicit))
    assert d["nested"][0]["style"] == {"size_pt": 60.0}


def _approved(board_edit_ops):
    rec = {"nested": corrections.diff_scenes(_board(), _edited(board_edit_ops))["nested"]}
    return {"shop_id": "r", "status": "approved", "record": rec}


def test_the_learned_changes_replay_on_a_fresh_board_of_the_same_structure():
    ops, summary = corrections.nested_ops(_board(), [_approved(DESIGNER_OPS)])
    assert summary == {"applied": 2, "skipped": 0, "conflicting": 0}
    got = scene_ops.apply_ops(_board(), ops)
    want = _edited(DESIGNER_OPS)

    def find(scene, nid):
        stack = [n for l in scene["layers"] for n in l["children"]]
        while stack:
            n = stack.pop()
            if n["id"] == nid:
                return n
            stack.extend(n.get("children") or [])

    g, w = find(got, "s96"), find(want, "s96")
    assert g["x"] + g["w"] / 2 == pytest.approx(w["x"] + w["w"] / 2, abs=0.5)        # the designer's centre ...
    assert g["y"] + g["h"] / 2 == pytest.approx(w["y"] + w["h"] / 2, abs=0.5)
    assert g["h"] == pytest.approx(w["h"], abs=0.5)                                  # ... and height; the width follows this name's text
    assert g["w"] == pytest.approx(800, abs=0.5)
    assert find(got, "s97")["text"]["bold"] is True
    assert find(got, "s97")["text"]["line_spacing"] == 80.0
    assert find(got, "s97")["text"]["content"] == "NEW CHENNAI BAKERY"          # content is never taught


def test_a_text_object_keeps_its_own_shape_and_takes_the_designers_height_and_centre():
    fresh = _board()
    block = fresh["layers"][0]["children"][0]["children"][1]
    block["w"] = block["children"][0]["w"] = 400.0                # a shorter shop name than the designer's
    ops, _ = corrections.nested_ops(fresh, [_approved([{"op": "resize", "ids": ["s97"], "from": {"x": 100, "y": 800, "w": 800, "h": 100},
                                                        "to": {"x": 100, "y": 900, "w": 800, "h": 200}}])])
    got = scene_ops.apply_ops(fresh, ops)
    n = got["layers"][0]["children"][0]["children"][1]["children"][0]
    assert n["h"] == pytest.approx(200, abs=0.5)
    assert n["w"] / n["h"] == pytest.approx(400 / 100, rel=0.01)             # still the shorter name's shape, scaled uniformly
    assert n["y"] + n["h"] / 2 == pytest.approx(1000, abs=0.5)               # the designer's vertical centre


def test_designers_who_disagree_are_not_applied_and_a_different_structure_is_skipped():
    a = _approved(DESIGNER_OPS)
    b = _approved([{"op": "resize", "ids": ["s96"], "from": {"x": 100, "y": 800, "w": 800, "h": 100}, "to": {"x": 400, "y": 300, "w": 500, "h": 100}}])
    _, summary = corrections.nested_ops(_board(), [a, b])
    assert summary["conflicting"] >= 1
    other = {"page": {"width": 1000.0, "height": 2000.0}, "layers": [{"id": "L1", "children": [_node("s95", 0, 0, 1000, 2000)]}]}
    assert corrections.nested_ops(other, [_approved(DESIGNER_OPS)])[1]["applied"] == 0
    ops, summary = corrections.nested_ops(other, [_approved(DESIGNER_OPS)])
    assert ops == []
    assert summary["skipped"] == 2


def test_summarize_lists_nested_changes_with_their_style():
    row = {"shop_id": "x", "brand": "b", "master_file": "m", "page_w_mm": 1000.0, "page_h_mm": 2000.0, "status": "pending",
           "record": {"changes": [], "nested": corrections.diff_scenes(_board(), _edited(DESIGNER_OPS))["nested"]}}
    out = corrections.summarize(row, "Shop")
    text = next(c for c in out["changes"] if c["id"] == "s97")
    assert text["nested"] is True
    assert text["kind"] == "text"
    assert text["style"]["bold"] is True
    assert "s97" in out["applies"]


def _wait_done(client, shop):  # noqa: F811
    for _ in range(200):
        if client.get(f"/api/v2/shops/{shop}/status").json()["status"] == "done":
            return
        time.sleep(0.05)
    raise AssertionError("conversion did not finish")


def test_a_sparkle_conversion_replays_the_learned_nested_edits_and_publishes_the_files(client):  # noqa: F811
    import app.db as db
    job, a = _converted_shop(client)
    _scene(client, job, a)
    # the designer moves a shape INSIDE the group s5 (mock scene: s5 > s3, s4) and saves
    assert client.put(f"/api/editor/{job}/{a}/ops", json={"ops": [{"op": "move", "ids": ["s3"], "dx": 120, "dy": 0}]}).status_code == 200
    rec = db.get_correction(a)
    assert [c["id"] for c in rec["record"]["nested"]] == ["s3"]
    assert client.put(f"/api/v2/corrections/{a}/status", json={"status": "approved"}).status_code == 200
    # another board of the same size on the same master, converted with Corel Intelligence
    b = client.post(f"/api/v2/jobs/{job}/shops", json={"name": "Second", "width": 120, "width_unit": "in", "height": 4, "height_unit": "ft"}).json()["id"]
    client.post(f"/api/v2/shops/{b}/convert")
    _wait_done(client, b)
    assert client.get(f"/api/v2/intelligence/available?ids={b}").json()["available"] == {b: 1}
    client.post(f"/api/v2/shops/{b}/convert", json={"use_intelligence": True})
    _wait_done(client, b)
    for _ in range(100):                                       # the replay runs right after the conversion, on the same worker
        ops = db.get_editor_ops(b)
        if ops:
            break
        time.sleep(0.05)
    assert [o["ids"] for o in ops] == [["s3"]]
    status = client.get(f"/api/v2/shops/{b}/status").json()
    assert status["report"]["layout"]["intelligence"]["nested"] == {"applied": 1, "skipped": 0, "conflicting": 0}
    assert db.list_exports(b), "the replayed edits are published as the board's new files"


def test_recapture_rereads_pending_records_from_their_saved_edits(client):  # noqa: F811
    import app.db as db
    import app.main as main
    job, a = _converted_shop(client)
    _scene(client, job, a)
    client.put(f"/api/editor/{job}/{a}/ops", json={"ops": [{"op": "move", "ids": ["s3"], "dx": 120, "dy": 0}]})
    old = db.get_correction(a)
    stale = {**old["record"], "nested": []}                      # a record made before the learner could see nested objects
    db.save_correction(stale)
    assert db.get_correction(a)["record"]["nested"] == []
    assert main.recapture_corrections() == {"updated": 1, "skipped": 0}
    assert [c["id"] for c in db.get_correction(a)["record"]["nested"]] == ["s3"]
    assert db.get_correction(a)["status"] == "pending"
