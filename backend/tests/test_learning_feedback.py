"""Corel Intelligence learns from what happens AFTER a lesson is used: records are trusted or distrusted by whether designers keep what
they taught, a save on a learned board is judged against the board as it was left (no echo), and top-level corrections are re-based onto
the engine's own placement."""
from __future__ import annotations

import copy
import time

import pytest

from tests.test_corrections_nested import _board, _node, _wait_done
from tests.test_editor_api import _converted_shop, _scene, client  # noqa: F401  (client is a fixture)

from app import corrections, scene_ops


def _row(shop_id="src", confirmed=0, disputed=0, boards=None, **rec):
    fb = {"boards": boards} if boards is not None else {"confirmed": confirmed, "disputed": disputed}
    return {"shop_id": shop_id, "brand": "b", "master_file": "m.cdr", "page_w_mm": 1000, "page_h_mm": 2000, "board_type": None,
            "record": {"shop_id": shop_id, "changes": [], "nested": [], "feedback": fb, **rec}, "updated_at": 1}


# ------------------------------------------------------------------ trust

def test_an_unproven_record_sits_in_the_middle_and_confirmations_raise_it():
    assert corrections.trust(_row()) == 0.5
    assert corrections.trust(_row(confirmed=3)) == pytest.approx(0.8)
    assert corrections.trust(_row(disputed=3)) == pytest.approx(0.2)


def test_tallies_are_summed_over_boards():
    row = _row(boards={"x": [2, 0], "y": [1, 1]})
    assert corrections.feedback_of(row) == {"confirmed": 3, "disputed": 1}


def test_a_record_the_designers_keep_correcting_is_paused_until_confirmations_win_it_back():
    assert corrections.quarantined(_row(disputed=2)) is True            # trust 0.25
    assert corrections.quarantined(_row(disputed=1)) is False
    assert corrections.quarantined(_row(confirmed=3, disputed=3)) is False   # trust 0.44
    assert corrections.quarantined(_row(confirmed=1, disputed=3)) is False   # 2/6 = 0.33 >= 0.3


def test_paused_records_are_not_used_for_a_board():
    good, bad = _row("good"), _row("bad", disputed=3)
    used = corrections.usable_records([good, bad], "b", "m.cdr", 1000, 2000, None)
    assert [r["shop_id"] for r in used] == ["good"]


def test_a_distrusted_record_cannot_lead_or_steady_a_blend_while_a_trusted_one_exists():
    newest_bad = {"cx": 0.9, "cy": 0.5, "w": 0.1, "h": 0.1, "_t": 0.2}
    older_good = {"cx": 0.3, "cy": 0.5, "w": 0.1, "h": 0.1, "_t": 0.8}
    assert corrections.blend_boxes([newest_bad, older_good]) == {"cx": 0.3, "cy": 0.5, "w": 0.1, "h": 0.1}


def test_with_only_distrusted_records_the_blend_still_answers():
    a = {"cx": 0.9, "cy": 0.5, "w": 0.1, "h": 0.1, "_t": 0.2}
    b = {"cx": 0.3, "cy": 0.5, "w": 0.1, "h": 0.1, "_t": 0.2}
    out = corrections.blend_boxes([a, b])
    assert out["cx"] == pytest.approx(0.8 * 0.9 + 0.2 * 0.6)


def test_trust_weights_the_steady_part_of_a_two_record_blend():
    a = {"cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1, "_t": 0.9}
    b = {"cx": 0.1, "cy": 0.5, "w": 0.1, "h": 0.1, "_t": 0.6}
    mid = (0.5 * 0.9 + 0.1 * 0.6) / 1.5
    assert corrections.blend_boxes([a, b])["cx"] == pytest.approx(0.8 * 0.5 + 0.2 * mid, abs=1e-5)


def test_untrusted_marks_do_not_change_an_ordinary_blend():
    boxes = [{"cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1}, {"cx": 0.3, "cy": 0.5, "w": 0.1, "h": 0.1}]
    assert corrections.blend_boxes(boxes)["cx"] == pytest.approx(0.8 * 0.5 + 0.2 * 0.4)


# ------------------------------------------------------------------ the learned baseline and the nested verdicts

LEARNED = [{"op": "resize", "ids": ["s96"], "from": {"x": 100, "y": 800, "w": 800, "h": 100}, "to": {"x": 150, "y": 900, "w": 600, "h": 100}}]


def _after(ops):
    return scene_ops.apply_ops(copy.deepcopy(_board()), ops)


def test_the_baseline_is_the_board_with_the_learned_ops_applied():
    b = corrections.learned_baseline(_board(), LEARNED)
    node = next(n for n in corrections._all_nodes(b).values() if n["id"] == "s96")
    assert (node["x"], node["w"]) == (150, 600)
    assert corrections._all_nodes(_board())["s96"]["w"] == 800           # the input is not touched


def test_a_learned_op_that_cannot_be_replayed_leaves_the_base_alone():
    bad = [{"op": "resize", "ids": ["nope"], "from": {"x": 0, "y": 0, "w": 1, "h": 1}, "to": {"x": 0, "y": 0, "w": 2, "h": 2}}]
    assert corrections.learned_baseline(_board(), bad) is not None


def test_nested_feedback_confirms_what_was_kept_and_disputes_what_was_changed_again():
    baseline = corrections.learned_baseline(_board(), LEARNED)
    assert corrections.nested_feedback(baseline, _after(LEARNED), LEARNED) == (1, 0)
    moved_again = _after(LEARNED + [{"op": "move", "ids": ["s96"], "dx": 60, "dy": 0}])
    assert corrections.nested_feedback(baseline, moved_again, LEARNED) == (0, 1)


def test_a_learned_change_the_designer_undid_counts_as_disputed():
    baseline = corrections.learned_baseline(_board(), LEARNED)
    assert corrections.nested_feedback(baseline, _board(), LEARNED) == (0, 1)       # saved without the learned edit


def test_showing_a_hidden_object_again_disputes_the_lesson_that_hid_it():
    hide = [{"op": "visibility", "id": "s96", "visible": False}]
    baseline = corrections.learned_baseline(_board(), hide)
    shown = _after([])
    assert corrections.nested_feedback(baseline, shown, hide) == (0, 1)


# ------------------------------------------------------------------ top-level re-basing

def _top_src(before_cx=0.5, after_cx=0.6):
    return {"shop_id": "src", "record": {"changes": [{"id": "a", "action": "moved", "signature": {"kind": "shape"},
                                                      "before": {"cx": before_cx, "cy": 0.5, "w": 0.2, "h": 0.2},
                                                      "after": {"cx": after_cx, "cy": 0.5, "w": 0.2, "h": 0.2}}]}}


def _top_scene(cx):
    return {"page": {"width": 1000.0, "height": 1000.0},
            "layers": [{"id": "L", "children": [_node("a", cx * 1000 - 100, 400, 200, 200)]}]}


def test_a_correction_that_starts_where_a_lesson_put_the_object_goes_back_to_the_engines_placement():
    again = [{"id": "a", "action": "moved", "before": {"cx": 0.6, "cy": 0.5, "w": 0.2, "h": 0.2}, "after": {"cx": 0.8, "cy": 0.5, "w": 0.2, "h": 0.2}}]
    changes, verdicts = corrections.rebase_and_feedback(again, [_top_src()], _top_scene(0.6))
    assert changes[0]["before"]["cx"] == 0.5 and changes[0]["after"]["cx"] == 0.8      # engine-raw -> the designer's final
    assert verdicts == {"src": [0, 1]}


def test_an_object_still_where_the_lesson_put_it_confirms_that_lesson():
    changes, verdicts = corrections.rebase_and_feedback([], [_top_src()], _top_scene(0.6))
    assert changes == [] and verdicts == {"src": [1, 0]}


def test_a_lesson_that_never_reached_this_board_is_neither_confirmed_nor_disputed():
    changes, verdicts = corrections.rebase_and_feedback([], [_top_src()], _top_scene(0.3))
    assert verdicts == {}


# ------------------------------------------------------------------ one save = one verdict, no echo

def _shop():
    return {"id": "board", "board_type": None}


def _layout(ops=None, records=None, nested_sources=None):
    return {"intelligence": {"learned_ops": ops or [], "records": records or [], "nested_sources": nested_sources or []}}


def test_saving_a_learned_board_unchanged_records_nothing_and_confirms_the_source():
    base = _board()
    edited = _after(LEARNED)                                             # exactly what Corel Intelligence did, nothing more
    record, verdicts = corrections.learn_from_save(_shop(), {"brand": "b", "master_filename": "m.cdr"}, base, edited,
                                                    _layout(LEARNED, nested_sources=["src"]), [])
    assert record is None                                                # before: the learned edit came back as a 'new' correction
    assert verdicts == {"src": [1, 0]}


def test_only_what_the_designer_added_on_top_is_recorded():
    extra = [{"op": "text", "id": "s97", "bold": True}]            # a style the lesson did not teach
    record, verdicts = corrections.learn_from_save(_shop(), {"brand": "b", "master_filename": "m.cdr"}, _board(), _after(LEARNED + extra),
                                                    _layout(LEARNED, nested_sources=["src"]), [])
    assert [c["id"] for c in record["nested"]] == ["s97"]                # s96 (learned) is not repeated
    assert verdicts == {"src": [1, 0]}


def test_correcting_a_learned_object_is_recorded_and_disputes_the_lesson():
    change = [{"op": "move", "ids": ["s96"], "dx": 0, "dy": -80}]
    record, verdicts = corrections.learn_from_save(_shop(), {"brand": "b", "master_filename": "m.cdr"}, _board(), _after(LEARNED + change),
                                                    _layout(LEARNED, nested_sources=["src"]), [])
    assert [c["id"] for c in record["nested"]] == ["s96"]
    assert verdicts == {"src": [0, 1]}


def test_a_board_made_without_learning_is_recorded_exactly_as_before():
    record, verdicts = corrections.learn_from_save(_shop(), {"brand": "b", "master_filename": "m.cdr"}, _board(), _after(LEARNED), None, [])
    assert [c["id"] for c in record["nested"]] == ["s96"] and verdicts == {}


def test_a_board_never_judges_its_own_record():
    _, verdicts = corrections.learn_from_save(_shop(), {"brand": "b", "master_filename": "m.cdr"}, _board(), _after(LEARNED),
                                              _layout(LEARNED, nested_sources=["board"]), [])
    assert verdicts == {}


# ------------------------------------------------------------------ the database keeps one verdict per board

def test_feedback_is_stored_per_board_so_autosaves_do_not_add_up(client):  # noqa: F811
    import app.db as db
    job, a = _converted_shop(client)
    _scene(client, job, a)
    client.put(f"/api/editor/{job}/{a}/ops", json={"ops": [{"op": "move", "ids": ["s3"], "dx": 120, "dy": 0}]})
    for _ in range(3):
        db.set_correction_feedback(a, "boardX", 2, 1)                     # the same board saved three times
    assert corrections.feedback_of(db.get_correction(a)) == {"confirmed": 2, "disputed": 1}
    db.set_correction_feedback(a, "boardY", 1, 0)
    assert corrections.feedback_of(db.get_correction(a)) == {"confirmed": 3, "disputed": 1}
    db.set_correction_feedback(a, "boardX", 0, 0)                        # a clean verdict removes that board's tally
    assert corrections.feedback_of(db.get_correction(a)) == {"confirmed": 1, "disputed": 0}
    assert db.set_correction_feedback("no-such-record", "b", 1, 0) is False


# ------------------------------------------------------------------ the whole loop through the API

def test_the_loop_end_to_end_confirm_then_dispute_then_pause(client):  # noqa: F811
    import app.db as db
    job, a = _converted_shop(client)
    _scene(client, job, a)
    assert client.put(f"/api/editor/{job}/{a}/ops", json={"ops": [{"op": "move", "ids": ["s3"], "dx": 120, "dy": 0}]}).status_code == 200

    def sparkle_board(name):
        b = client.post(f"/api/v2/jobs/{job}/shops", json={"name": name, "width": 120, "width_unit": "in", "height": 4, "height_unit": "ft"}).json()["id"]
        client.post(f"/api/v2/shops/{b}/convert")
        _wait_done(client, b)
        client.post(f"/api/v2/shops/{b}/convert", json={"use_intelligence": True})
        _wait_done(client, b)
        for _ in range(100):
            if db.get_editor_ops(b):
                break
            time.sleep(0.05)
        return b

    b = sparkle_board("Second")
    intel = client.get(f"/api/v2/shops/{b}/status").json()["report"]["layout"]["intelligence"]
    assert intel["nested_sources"] == [a] and [o["ids"] for o in intel["learned_ops"]] == [["s3"]]      # remembered for the next save
    learned = db.get_editor_ops(b)

    # 1) the designer opens board B and saves without touching anything: confirmed, and NO echo record is created
    assert client.put(f"/api/editor/{job}/{b}/ops", json={"ops": learned}).status_code == 200
    assert db.get_correction(b) is None
    assert corrections.feedback_of(db.get_correction(a)) == {"confirmed": 1, "disputed": 0}
    assert corrections.trust(db.get_correction(a)) == pytest.approx(2 / 3)

    # 2) saving the same board again does not count twice
    client.put(f"/api/editor/{job}/{b}/ops", json={"ops": learned})
    assert corrections.feedback_of(db.get_correction(a)) == {"confirmed": 1, "disputed": 0}

    # 3) she moves the learned object again: the lesson is disputed and her correction is recorded (only that)
    again = learned + [{"op": "move", "ids": ["s3"], "dx": -200, "dy": 0}]
    assert client.put(f"/api/editor/{job}/{b}/ops", json={"ops": again}).status_code == 200
    assert corrections.feedback_of(db.get_correction(a)) == {"confirmed": 0, "disputed": 1}
    assert [c["id"] for c in db.get_correction(b)["record"]["nested"]] == ["s3"]

    # 4) a second board where she corrects it again: the lesson is now paused for boards of this size
    c = sparkle_board("Third")
    client.put(f"/api/editor/{job}/{c}/ops", json={"ops": db.get_editor_ops(c) + [{"op": "move", "ids": ["s3"], "dx": -150, "dy": 0}]})
    fb = corrections.feedback_of(db.get_correction(a))
    assert fb == {"confirmed": 0, "disputed": 2}
    assert corrections.quarantined(db.get_correction(a)) is True                 # paused: boards of this size no longer take it from record a
    assert a not in [r["shop_id"] for r in corrections.usable_records(db.list_corrections(), "dalmia", "master.cdr", 3048, 1219.2, None)]
    summary = corrections.summarize(db.get_correction(a))
    assert summary["trust"] == round(corrections.trust(db.get_correction(a)), 2) and "quarantined" in summary
