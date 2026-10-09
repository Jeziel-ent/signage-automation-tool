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
    assert g["w"] == pytest.approx(600, abs=0.5)         # fitted INSIDE her 600 x 100 box, keeping this name's 8:1 shape - never wider than her box
    assert g["h"] == pytest.approx(75, abs=0.5)
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


def test_a_name_is_never_made_larger_than_the_designers_box_even_when_her_box_holds_two_lines():
    """Found on a real Hangyo board: she set the Tamil name on two lines (box 1682 x 293 mm); the next board's ONE-line name (1682 x 173) was scaled to
    her height - 1.7 times too wide - and ran off its white panel."""
    fresh = _board()
    block = fresh["layers"][0]["children"][0]["children"][1]
    block["w"] = block["children"][0]["w"] = 800.0
    block["h"] = block["children"][0]["h"] = 100.0
    ops, _ = corrections.nested_ops(fresh, [_approved([{"op": "resize", "ids": ["s97"], "from": {"x": 100, "y": 800, "w": 800, "h": 100},
                                                        "to": {"x": 100, "y": 750, "w": 800, "h": 170}}])])
    n = scene_ops.apply_ops(fresh, ops)["layers"][0]["children"][0]["children"][1]["children"][0]
    assert n["w"] <= 800.5                                                  # her width, not 800 x 1.7
    assert n["h"] <= 170.5 and n["w"] / n["h"] == pytest.approx(8.0, rel=0.01)


def test_designers_who_disagree_are_not_applied_and_a_different_structure_is_skipped():
    a = _approved(DESIGNER_OPS)
    a["record"]["source"] = "designer-dataset"
    b = _approved([{"op": "resize", "ids": ["s96"], "from": {"x": 100, "y": 800, "w": 800, "h": 100}, "to": {"x": 400, "y": 300, "w": 500, "h": 100}}])
    b["record"]["source"] = "designer-dataset"
    _, summary = corrections.nested_ops(_board(), [a, b])
    assert summary["conflicting"] >= 1
    other = {"page": {"width": 1000.0, "height": 2000.0}, "layers": [{"id": "L1", "children": [_node("s95", 0, 0, 1000, 2000)]}]}
    assert corrections.nested_ops(other, [_approved(DESIGNER_OPS)])[1]["applied"] == 0
    ops, summary = corrections.nested_ops(other, [_approved(DESIGNER_OPS)])
    assert ops == []
    assert summary["skipped"] == 2


def test_summarize_lists_nested_changes_with_their_style():
    row = {"shop_id": "x", "brand": "b", "master_file": "m", "page_w_mm": 1000.0, "page_h_mm": 2000.0,
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


def test_recapture_rereads_stored_records_from_their_saved_edits(client):  # noqa: F811
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
    assert db.get_correction(a)["status"] == "approved"      # legacy column: every record is used


# ------------------------------------------------------------------ the number of lines a name is set on

def test_balanced_lines_breaks_at_spaces_and_keeps_the_longest_line_short():
    assert corrections.balanced_lines("ஏசியன் ஜூஸ் பார்", 2) == "ஏசியன்\nஜூஸ் பார்"
    assert corrections.balanced_lines("A B CCCC DD", 2) == "A B\nCCCC DD"
    assert corrections.balanced_lines("ONE TWO THREE", 3) == "ONE\nTWO\nTHREE"
    assert corrections.balanced_lines("ONE TWO", 5) == "ONE\nTWO"            # no more lines than words
    assert corrections.balanced_lines("ONEWORD", 2) is None                  # nothing to break at
    assert corrections.balanced_lines("A\r\nB  C", 2) == "A\nB C"
    assert corrections.balanced_lines("A B", 1) is None


TWO_LINE_OPS = [
    {"op": "text", "id": "s97", "content": "NEW CHENNAI\nBAKERY"},
    {"op": "resize", "ids": ["s96"], "from": {"x": 100, "y": 800, "w": 800, "h": 100}, "to": {"x": 200, "y": 700, "w": 600, "h": 160}},
]


def test_the_number_of_lines_a_name_was_set_on_is_recorded():
    d = corrections.diff_scenes(_board(), _edited(TWO_LINE_OPS))
    block = next(c for c in d["nested"] if c["id"] == "s96")
    assert block["lines"] == 2
    one = corrections.diff_scenes(_board(), _edited([{"op": "text", "id": "s97", "content": "OTHER NAME"}]))
    assert one["nested"] == []                                                # same line count: nothing learned from a rename


def test_a_fresh_board_gets_the_same_number_of_lines_inside_her_box():
    fresh = _board()
    fresh["layers"][0]["children"][0]["children"][1]["children"][0]["text"]["content"] = "SRI GANESHA KITCHEN"
    ops, summary = corrections.nested_ops(fresh, [_approved(TWO_LINE_OPS)])
    assert summary["applied"] >= 1
    assert ops[0] == {"op": "text", "id": "s97", "content": "SRI GANESHA\nKITCHEN"}      # the content op comes first
    got = scene_ops.apply_ops(fresh, ops)
    block = next(n for n in _walk(got) if n["id"] == "s96")
    text = next(n for n in _walk(got) if n["id"] == "s97")
    assert text["text"]["content"] == "SRI GANESHA\nKITCHEN" and text["stale"] is True
    assert block["w"] <= 600 * corrections.LINED_FILL_MAX + 0.5                  # kept near her 600 mm box (a rough guard, see LINED_FILL_MAX)
    assert block["y"] + block["h"] / 2 == pytest.approx(780, abs=0.5)             # centred on her box
    assert block["h"] == pytest.approx(160, abs=0.5) or block["h"] < 160          # per-line height: hers, or smaller to stay inside


def test_a_longer_name_gets_a_smaller_line_height_so_it_stays_inside_her_box():
    long_fresh = _board()
    block0 = long_fresh["layers"][0]["children"][0]["children"][1]
    block0["children"][0]["text"]["content"] = "SRI GANESHA KITCHEN AND CATERING SERVICES PRIVATE LIMITED CHENNAI"
    block0["w"] = block0["children"][0]["w"] = 2400.0          # the one-line name is ~3x as wide as the short one (the board's own measured box)
    ops, _ = corrections.nested_ops(long_fresh, [_approved(TWO_LINE_OPS)])
    block = next(n for n in _walk(scene_ops.apply_ops(long_fresh, ops)) if n["id"] == "s96")
    assert block["w"] <= 600 * corrections.LINED_FILL_MAX + 0.5
    assert block["h"] < 160


def test_no_break_is_made_when_the_board_already_has_that_many_lines_or_the_name_is_one_word():
    two = _board()
    two["layers"][0]["children"][0]["children"][1]["children"][0]["text"]["content"] = "ALREADY\nTWO"
    ops, _ = corrections.nested_ops(two, [_approved(TWO_LINE_OPS)])
    assert not any(o["op"] == "text" and o.get("content") for o in ops)
    one = _board()
    one["layers"][0]["children"][0]["children"][1]["children"][0]["text"]["content"] = "BAKERY"
    ops, _ = corrections.nested_ops(one, [_approved(TWO_LINE_OPS)])
    assert not any(o["op"] == "text" and o.get("content") for o in ops)


def test_the_newest_editor_save_wins_over_an_older_one_with_a_different_line_count():
    three = [{"op": "text", "id": "s97", "content": "NEW\nCHENNAI\nBAKERY"},
             {"op": "resize", "ids": ["s96"], "from": {"x": 100, "y": 800, "w": 800, "h": 100}, "to": {"x": 200, "y": 700, "w": 600, "h": 160}}]
    ops, summary = corrections.nested_ops(_board(), [_approved(TWO_LINE_OPS), _approved(three)])      # newest first: the 2-line save
    assert summary["conflicting"] == 0
    assert ops[0] == {"op": "text", "id": "s97", "content": "NEW CHENNAI\nBAKERY"}
    older_first = corrections.nested_ops(_board(), [_approved(three), _approved(TWO_LINE_OPS)])[0]
    assert older_first[0]["content"].count("\n") == 2                                                  # whichever record is first (newest) decides
    files = []
    for each in (TWO_LINE_OPS, three):
        rec = _approved(each)
        rec["record"]["source"] = "designer-dataset"
        files.append(rec)
    _, summary = corrections.nested_ops(_board(), files)
    assert summary["conflicting"] >= 1                                                                  # records learned from designer files must agree


def _walk(scene):
    stack = [n for l in scene["layers"] for n in l["children"]]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.get("children") or [])


def test_tamil_words_are_broken_by_visual_width_not_by_code_points():
    """A designer set "ஸ்ரீ சாய் கஃபே" as "ஸ்ரீ சாய்" / "கஃபே"; counting code points tied that with "ஸ்ரீ" / "சாய் கஃபே" and took the wrong one."""
    assert corrections.balanced_lines("ஸ்ரீ சாய் கஃபே", 2) == "ஸ்ரீ சாய்\nகஃபே"


def test_a_re_broken_name_gets_her_line_height_when_the_estimate_says_it_fits():
    """Found on a real board: capping at 80 % of her box (and padding the estimate) halved the Tamil name - her own edit had 693 mm of ink in a
    915 mm box. Within the guard the name now takes HER line height."""
    fresh = _board()
    fresh["layers"][0]["children"][0]["children"][1]["children"][0]["text"]["content"] = "ஸ்ரீ சாய் கஃபே"
    ops, _ = corrections.nested_ops(fresh, [_approved(TWO_LINE_OPS)])
    block = next(n for n in _walk(scene_ops.apply_ops(fresh, ops)) if n["id"] == "s96")
    assert 0.85 * 160 <= block["h"] <= 160.5                                         # (almost) her per-line height - not halved as before
    assert block["w"] <= 600 * corrections.LINED_FILL_MAX + 0.5

# ------------------------------------------------------------------ hidden / deleted objects are learned too, and ranked 8:2

def _find_node(scene, nid):
    return next((n for n in _walk(scene) if n["id"] == nid), None)


def test_a_hidden_object_inside_a_group_is_recorded_and_hidden_again_on_the_next_board():
    ops = [{"op": "visibility", "id": "s97", "visible": False}]
    d = corrections.diff_scenes(_board(), _edited(ops))
    gone = next(c for c in d["nested"] if c["id"] == "s97")
    assert gone["action"] == "hidden" and gone["after"] is None
    got, summary = corrections.nested_ops(_board(), [_approved(ops)])
    assert got == [{"op": "visibility", "id": "s97", "visible": False}] and summary["applied"] == 1
    assert _find_node(scene_ops.apply_ops(_board(), got), "s97")["visible"] is False


def test_a_deleted_object_is_recorded_and_deleted_again_but_an_ungrouped_group_is_not_a_deletion():
    ops = [{"op": "delete", "ids": ["s94"]}]
    d = corrections.diff_scenes(_board(), _edited(ops))
    assert any(c["id"] == "s94" and c["action"] == "deleted" for c in d["nested"])
    got, _ = corrections.nested_ops(_board(), [_approved(ops)])
    assert _find_node(scene_ops.apply_ops(_board(), got), "s94") is None
    ungrouped = corrections.diff_scenes(_board(), _edited([{"op": "ungroup", "id": "s96"}]))
    assert not any(c["action"] == "deleted" for c in ungrouped["nested"] + ungrouped["changes"])    # its text is still on the board


def test_a_top_level_hide_is_replayed_through_the_same_path():
    rec = {"shop_id": "t", "record": {"changes": [{"id": "s95", "signature": {"kind": "group", "aspect": 0.5, "n_desc": 3}, "before": {"cx": 0.5, "cy": 0.5, "w": 1.0, "h": 1.0},
                                                    "after": None, "action": "hidden"}], "nested": []}}
    assert corrections.has_scene_changes([rec])
    got, summary = corrections.nested_ops(_board(), [rec])
    assert got == [{"op": "visibility", "id": "s95", "visible": False}] and summary["applied"] == 1


def test_nested_boxes_are_blended_80_20_newest_first():
    older = _approved([{"op": "move", "ids": ["s96"], "dx": 50, "dy": 0}])
    newest = _approved([{"op": "move", "ids": ["s96"], "dx": 150, "dy": 0}])
    ch = corrections._agreed_change(corrections._nested_requests([newest, older])["s96"])
    # centres: newest 0.65 + ... in fractions of the 1000 mm page; blend = 0.8 * newest + 0.2 * mean(newest, older)
    n, o = (c["after"]["cx"] for c in (corrections._nested_requests([newest])["s96"][0], corrections._nested_requests([older])["s96"][0]))
    assert ch["after"]["cx"] == pytest.approx(0.8 * n + 0.2 * (n + o) / 2, abs=1e-5)
    assert n > o


# ---------------------------------------------------------------- improvements: name length, median, nearby sizes, fit width
def test_the_length_of_the_name_she_broke_is_recorded_and_a_much_shorter_name_stays_on_one_line():
    d = corrections.diff_scenes(_board(), _edited(TWO_LINE_OPS))
    block = next(c for c in d["nested"] if c["id"] == "s96")
    assert block["name_chars"] == len("NEW CHENNAI BAKERY")
    short = _board()
    short["layers"][0]["children"][0]["children"][1]["children"][0]["text"]["content"] = "SRI AMMAN"        # 9 of her 18 characters
    ops, _ = corrections.nested_ops(short, [_approved(TWO_LINE_OPS)])
    assert not any(o["op"] == "text" and o.get("content") for o in ops)                                    # stays on one line
    ok = _board()
    ok["layers"][0]["children"][0]["children"][1]["children"][0]["text"]["content"] = "SRI GANESHA STORES"    # about as long as hers
    ops, _ = corrections.nested_ops(ok, [_approved(TWO_LINE_OPS)])
    assert ops[0]["op"] == "text" and "\n" in ops[0]["content"]


def test_old_records_without_a_name_length_still_break_any_name():
    rec = _approved(TWO_LINE_OPS)
    for c in rec["record"]["nested"]:
        c.pop("name_chars", None)
    short = _board()
    short["layers"][0]["children"][0]["children"][1]["children"][0]["text"]["content"] = "SRI AMMAN"
    ops, _ = corrections.nested_ops(short, [rec])
    assert ops[0]["op"] == "text" and "\n" in ops[0]["content"]


def test_the_resize_of_a_name_block_carries_the_width_the_export_must_keep():
    ops, _ = corrections.nested_ops(_board(), [_approved(DESIGNER_OPS)])
    resize = next(o for o in ops if o["op"] == "resize" and o["ids"] == ["s96"])
    assert resize["fit_w"] == pytest.approx(600 * corrections.FIT_FRAC, abs=0.01)
    scene_ops.apply_ops(_board(), ops)                                    # the extra key is harmless to the editor's op engine


def _near(rec, w, h):
    rec = copy.deepcopy(rec)
    rec["page_w_mm"], rec["page_h_mm"] = w, h
    return rec


def test_a_nearby_size_record_is_used_but_a_different_shape_is_not():
    from tests.test_corrections import _rec
    exact = _rec("e")
    near = _near(_rec("n"), 1000.0, 410.0)             # aspect 2.44 vs 2.5 (2.4 %), area +2.5 %
    far = _near(_rec("f"), 1000.0, 600.0)
    got = corrections.usable_records([far, near, exact], "b", "m.cdr", 1000.0, 400.0, None)
    assert [r["shop_id"] for r in got] == ["e", "n"]                                     # exact size first, then the nearby one
    assert got[1]["nearby"] is True and "nearby" not in got[0]
    wide = _near(_rec("w"), 1000.0, 290.0)             # same area band but aspect 3.4: not nearby
    assert corrections.usable_records([wide], "b", "m.cdr", 1000.0, 400.0, None) == []


def test_a_nearby_record_stands_in_when_there_is_no_exact_one_and_counts_half_next_to_one():
    from tests.test_corrections import _rec, _placed
    near = _near(_rec("n"), 1000.0, 410.0)
    near["nearby"] = True
    logo = _placed("0", 100, 150, 200, 100)
    s = corrections.apply_to_placed([logo], 1000.0, 400.0, [near])
    assert s["applied"] == 1 and logo.x + logo.w / 2 == pytest.approx(300.0)             # alone it is applied as it is
    exact = _rec("e")
    other = _near(_rec("o"), 1000.0, 410.0)
    other["nearby"] = True
    other["record"]["changes"][0]["after"] = {"cx": 0.9, "cy": 0.4, "w": 0.2, "h": 0.25}
    logo2 = _placed("0", 100, 150, 200, 100)
    corrections.apply_to_placed([logo2], 1000.0, 400.0, [exact, other])
    # exact 0.3 leads; the steady part is the half-weighted average (0.3 + 0.5 * 0.9) / 1.5 = 0.5: 0.8 * 0.3 + 0.2 * 0.5 = 0.34
    assert logo2.x + logo2.w / 2 == pytest.approx(340.0, abs=0.01)


def test_the_steady_part_of_three_corrections_is_their_median():
    boxes = [{"cx": v, "cy": 0.5, "w": 0.2, "h": 0.2} for v in (0.30, 0.31, 0.95)]      # newest first; the oldest edit was a mistake
    got = corrections.blend_boxes(boxes)
    assert got["cx"] == pytest.approx(0.8 * 0.30 + 0.2 * 0.31)                            # a plain average would have been 0.52
