"""Corel Intelligence tied to master ELEMENTS: a correction remembers which master element it was about (`master_id`), so a later board finds
that element by identity even when the engine placed it somewhere else; ambiguous elements (tile copies) and text fall back to the box rule."""
from __future__ import annotations

import copy
import math

from app import corrections
from tests.test_corrections import _base


class P:
    def __init__(self, pid, cx, cy, w, h, pw, ph, role="logo"):
        self.id, self.role, self.w, self.h, self.warnings = str(pid), role, w * pw, h * ph, []
        self.x, self.y = cx * pw - self.w / 2, cy * ph - self.h / 2


PW, PH = 1000.0, 400.0


def _record(master_id="7", shift=0.10, sid="r1", w=PW, h=PH):
    ch = {"id": "a", "action": "moved", "signature": {"kind": "shape", "aspect": 2.0, "n_desc": 0},
          "before": {"cx": 0.30, "cy": 0.50, "w": 0.20, "h": 0.25}, "after": {"cx": 0.30 + shift, "cy": 0.50, "w": 0.20, "h": 0.25}}
    if master_id is not None:
        ch["master_id"] = master_id
    return {"shop_id": sid, "brand": "Hangyo", "master_file": "6 X 3.cdr", "page_w_mm": w, "page_h_mm": h, "board_type": "GSB", "updated_at": 1.0,
            "record": {"changes": [ch], "nested": []}}


def _cx(p):
    return (p.x + p.w / 2) / PW


def test_the_same_element_is_found_by_identity_when_the_box_is_where_the_designer_saw_it():
    p = P(7, 0.30, 0.50, 0.20, 0.25, PW, PH)
    out = corrections.apply_to_placed([p], PW, PH, [_record()])
    assert out["applied"] == 1 and out["identity"] == 1
    assert math.isclose(_cx(p), 0.40, abs_tol=1e-9)                     # her box, as before


def test_an_element_the_engine_now_places_elsewhere_takes_her_shift_not_her_box():
    moved = P(7, 0.55, 0.50, 0.20, 0.25, PW, PH)                         # 25 % of the page away from where the record saw it
    out = corrections.apply_to_placed([moved], PW, PH, [_record()])
    assert out["applied"] == 1 and out["identity"] == 1
    assert math.isclose(_cx(moved), 0.65, abs_tol=1e-9)                  # 0.55 + her 0.10 shift (her absolute box 0.40 would be a jump back)


def test_without_identity_the_far_object_is_simply_not_found_by_the_box_rule():
    moved = P(7, 0.55, 0.50, 0.20, 0.25, PW, PH)
    out = corrections.apply_to_placed([moved], PW, PH, [_record(master_id=None)])
    assert out["applied"] == 0 and out["skipped"] == 1 and "identity" not in out


def test_a_tiled_element_is_not_identified_by_id_and_falls_back_to_the_box():
    a, b = P("7_tile0", 0.30, 0.50, 0.20, 0.25, PW, PH), P("7_tile1", 0.80, 0.50, 0.20, 0.25, PW, PH)
    out = corrections.apply_to_placed([a, b], PW, PH, [_record()])
    assert out["applied"] == 1 and "identity" not in out
    assert math.isclose(_cx(a), 0.40, abs_tol=1e-9) and math.isclose(_cx(b), 0.80, abs_tol=1e-9)    # only the copy sitting where she saw it


def test_text_is_never_taken_by_identity():
    t = P(7, 0.30, 0.50, 0.20, 0.25, PW, PH, role="shopname")
    assert corrections.apply_to_placed([t], PW, PH, [_record()])["applied"] == 0


def test_identity_also_finds_the_element_on_another_board_size():
    rec = _record(w=1828.8, h=914.4)                                     # corrected on a 6 x 3 ft board; this board is 8 x 4 ft
    (fam,) = corrections.usable_records([rec], "Hangyo", "6 X 3.cdr", 2438.4, 1219.2, "GSB", family=True)
    far = P(7, 0.62, 0.20, 0.20, 0.25, 2438.4, 1219.2)                   # nowhere near the recorded box: only the id says it is the same element
    out = corrections.apply_to_placed([far], 2438.4, 1219.2, [fam])
    assert out["applied"] == 1 and out["identity"] == 1 and out["family"] == 1
    assert math.isclose((far.x + far.w / 2) / 2438.4, 0.62 + corrections.FAMILY_STRENGTH * 0.10, abs_tol=1e-9)


def test_an_id_match_of_a_very_different_shape_is_refused_for_other_sizes():
    rec = _record(w=1828.8, h=914.4)
    (fam,) = corrections.usable_records([rec], "Hangyo", "6 X 3.cdr", 2438.4, 1219.2, "GSB", family=True)
    swapped = P(7, 0.30, 0.50, 0.05, 0.50, 2438.4, 1219.2)               # a thin tall shape under the same id: another element
    assert corrections.apply_to_placed([swapped], 2438.4, 1219.2, [fam])["applied"] == 0


# ---------------------------------------------------------------- capture: tying a change to its master element

def _changes():
    edited = copy.deepcopy(_base())
    edited["layers"][0]["children"][0]["x"] += 100                      # node "a": 100,100 200x100 on a 1000 x 400 page
    return corrections.diff_scenes(_base(), edited)["changes"]


def _obj(oid, x, y, w, h, role="logo"):
    return {"id": oid, "role": role, "x": x, "y": y, "w": w, "h": h}


def test_a_change_is_tied_to_the_one_object_with_the_same_box():
    changes = _changes()
    assert corrections.attach_identity(changes, [_obj("7", 100, 100, 200, 100), _obj("8", 600, 50, 100, 100)], PW, PH) == 1
    assert changes[0]["master_id"] == "7"


def test_two_objects_in_the_same_place_are_ambiguous_and_nothing_is_tied():
    changes = _changes()
    assert corrections.attach_identity(changes, [_obj("7", 100, 100, 200, 100), _obj("9", 100, 100, 200, 100)], PW, PH) == 0
    assert "master_id" not in changes[0]


def test_tile_copies_and_text_are_not_tied():
    changes = _changes()
    assert corrections.attach_identity(changes, [_obj("7_tile0", 100, 100, 200, 100), _obj("7_tile1", 700, 100, 200, 100)], PW, PH) == 0
    assert corrections.attach_identity(_changes(), [_obj("7", 100, 100, 200, 100, role="shopname")], PW, PH) == 0


def test_learn_from_save_ties_the_corrections_of_a_plain_board():
    edited = copy.deepcopy(_base())
    edited["layers"][0]["children"][0]["x"] += 100
    objs = [_obj("7", 100, 100, 200, 100)]
    rec, _ = corrections.learn_from_save({"id": "s1"}, {"brand": "Hangyo", "master_filename": "6 X 3.cdr"}, _base(), edited, None, [], objects=objs)
    assert rec["changes"][0]["master_id"] == "7"
    rec2, _ = corrections.learn_from_save({"id": "s1"}, {"brand": "Hangyo"}, _base(), edited, None, [])        # no objects (an older report): still recorded
    assert "master_id" not in rec2["changes"][0]


def test_a_rebased_correction_keeps_the_master_id_of_the_record_it_corrects():
    source = _record(master_id="7", shift=0.10)
    source["record"]["changes"][0]["after"] = {"cx": 0.40, "cy": 0.50, "w": 0.20, "h": 0.25}
    changes = [{"id": "z", "action": "moved", "signature": {"kind": "shape"}, "before": {"cx": 0.40, "cy": 0.50, "w": 0.20, "h": 0.25},
                "after": {"cx": 0.50, "cy": 0.50, "w": 0.20, "h": 0.25}}]
    base = {"page": {"width": PW, "height": PH}, "layers": [{"id": "L1", "children": []}]}
    out, verdicts = corrections.rebase_and_feedback(changes, [source], base)
    assert out[0]["before"]["cx"] == 0.30 and out[0]["master_id"] == "7"
    assert verdicts == {"r1": [0, 1]}
