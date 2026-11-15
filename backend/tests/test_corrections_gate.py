"""Corel Intelligence confidence and quality gate: unsure changes are held back, and a learned move that would push an object off the page or
onto another object is undone."""
from __future__ import annotations

import math

from app import corrections

PW, PH = 1000.0, 400.0


class P:
    def __init__(self, pid, x, y, w, h, role="logo"):
        self.id, self.role, self.x, self.y, self.w, self.h, self.warnings = str(pid), role, x, y, w, h, []


def _rec(sid, before, after, trust_boards=None, updated=1.0, source=None):
    ch = {"id": "a", "action": "moved", "signature": {"kind": "shape", "aspect": before[2] / before[3] * PW / PH, "n_desc": 0},
          "before": dict(zip(("cx", "cy", "w", "h"), before)), "after": dict(zip(("cx", "cy", "w", "h"), after))}
    rec = {"changes": [ch], "nested": []}
    if trust_boards:
        rec["feedback"] = {"boards": trust_boards}
    if source:
        rec["source"] = source
    return {"shop_id": sid, "brand": "B", "master_file": "m.cdr", "page_w_mm": PW, "page_h_mm": PH, "board_type": "GSB", "updated_at": updated, "record": rec}


BEFORE = (0.30, 0.50, 0.20, 0.25)
AFTER = (0.50, 0.50, 0.20, 0.25)


def _obj(cx=0.30, cy=0.50, w=0.20, h=0.25):
    return P("1", (cx - w / 2) * PW, (cy - h / 2) * PH, w * PW, h * PH)


def _vote(editor=True, t=0.5, **extra):
    return ({"cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.25, "_t": t, **extra}, "s", editor)


# ---------------------------------------------------------------- confidence

def test_a_proven_editor_correction_is_more_certain_than_an_unproven_one_and_both_beat_a_designer_file():
    decided = {"cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.25}
    unproven = corrections.confidence_of([_vote(t=0.5)], decided)
    proven = corrections.confidence_of([_vote(t=0.9)], decided)
    seed = corrections.confidence_of([_vote(editor=False, t=0.5)], decided)
    assert proven > unproven > seed > 0
    assert proven <= 0.99


def test_agreeing_records_raise_the_confidence_and_a_disagreeing_one_lowers_it():
    decided = {"cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.25}
    one = corrections.confidence_of([_vote()], decided)
    two = corrections.confidence_of([_vote(), _vote()], decided)
    contested = corrections.confidence_of([_vote(), ({"cx": 0.9, "cy": 0.5, "w": 0.2, "h": 0.25, "_t": 0.5}, "s2", True)], decided)
    assert two > one > contested


def test_other_sizes_fade_with_distance_and_identity_adds_a_little():
    decided = {"cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.25}
    near = corrections.confidence_of([_vote(_fam=True, _w=math.exp(-0.1 / 0.6))], decided)
    far = corrections.confidence_of([_vote(_fam=True, _w=math.exp(-1.0 / 0.6))], decided)
    by_id = corrections.confidence_of([_vote(_fam=True, _w=math.exp(-1.0 / 0.6), _id=True)], decided)
    assert near > far
    assert by_id > far
    assert corrections.confidence_of([], decided) == 0.0


# ---------------------------------------------------------------- held back

def test_a_change_below_the_floor_is_held_back_and_reported_not_applied():
    obj = _obj()
    out = corrections.apply_to_placed([obj], PW, PH, [_rec("r", BEFORE, AFTER)], min_confidence=0.95)
    assert out["applied"] == 0 and out["held_back"] == 1
    assert math.isclose(obj.x, 0.2 * PW) and math.isclose(obj.y, 0.375 * PH)         # untouched
    assert not obj.warnings


def test_the_default_floor_applies_a_single_unproven_editor_correction():
    obj = _obj()
    out = corrections.apply_to_placed([obj], PW, PH, [_rec("r", BEFORE, AFTER)])
    assert out["applied"] == 1 and "held_back" not in out and out["avg_confidence"] > corrections.APPLY_MIN_CONFIDENCE


def test_details_list_every_decided_object_with_its_outcome():
    det: list = []
    corrections.apply_to_placed([_obj()], PW, PH, [_rec("r", BEFORE, AFTER)], details=det)
    assert [(d["id"], d["outcome"]) for d in det] == [("1", "applied")]
    assert 0 < det[0]["confidence"] <= 0.99


# ---------------------------------------------------------------- quality gate

def test_a_learned_move_off_the_page_is_undone():
    obj = _obj()
    out = corrections.apply_to_placed([obj], PW, PH, [_rec("r", BEFORE, (0.98, 0.50, 0.20, 0.25))])      # her box would stick out to the right
    assert out["applied"] == 0 and out["gated"] == 1
    assert all(math.isclose(a, b) for a, b in zip((obj.x, obj.y, obj.w, obj.h), (0.2 * PW, 0.375 * PH, 0.2 * PW, 0.25 * PH)))   # back where the engine put it
    assert "undone" in obj.warnings[-1] and "leaves the page" in obj.warnings[-1]


def test_an_object_that_already_stuck_out_may_be_moved_by_learning():
    obj = P("1", 900.0, 100.0, 200.0, 100.0)                                                              # engine put it 10 % off the page
    rec = _rec("r", (1.0, 0.375, 0.20, 0.25), (0.90, 0.375, 0.20, 0.25))                                  # she pulled it back in
    out = corrections.apply_to_placed([obj], PW, PH, [rec])
    assert out["applied"] == 1 and "gated" not in out


def test_a_move_that_lands_on_another_object_is_undone_but_a_move_into_free_space_is_not():
    other = P("2", 400.0, 150.0, 200.0, 100.0)                                                           # sits where she would put the logo
    obj = _obj()
    out = corrections.apply_to_placed([obj, other], PW, PH, [_rec("r", BEFORE, AFTER)])
    assert out["gated"] == 1 and out["applied"] == 0
    assert "overlaps another object" in obj.warnings[-1]
    free = _obj()
    assert corrections.apply_to_placed([free], PW, PH, [_rec("r", BEFORE, AFTER)])["applied"] == 1


def test_background_and_frame_objects_never_block_a_move():
    bg = P("0", 0.0, 0.0, PW, PH, role="bg")
    obj = _obj()
    assert corrections.apply_to_placed([obj, bg], PW, PH, [_rec("r", BEFORE, AFTER)])["applied"] == 1


def test_pieces_that_move_together_do_not_count_as_overlapping_each_other():
    a = P("1", 150.0, 150.0, 120.0, 100.0)
    b = P("2", 250.0, 150.0, 120.0, 100.0)                                                               # overlaps a by 20 mm already
    rec = _rec("r", (0.21, 0.5, 0.12, 0.25), (0.41, 0.5, 0.12, 0.25))
    ch_b = {**rec["record"]["changes"][0], "id": "b", "before": {"cx": 0.31, "cy": 0.5, "w": 0.12, "h": 0.25}, "after": {"cx": 0.51, "cy": 0.5, "w": 0.12, "h": 0.25}}
    rec["record"]["changes"].append(ch_b)
    out = corrections.apply_to_placed([a, b], PW, PH, [rec])
    assert out["applied"] == 2 and "gated" not in out
    assert math.isclose(b.x - a.x, 100.0)                                                                 # still side by side as before


def test_the_least_certain_change_is_undone_first():
    a = P("1", 200.0, 150.0, 200.0, 100.0)                                                               # two objects both sent to the same spot
    b = P("2", 600.0, 150.0, 200.0, 100.0)
    strong = _rec("strong", (0.30, 0.50, 0.20, 0.25), (0.50, 0.50, 0.20, 0.25), trust_boards={"x": [5, 0]})
    weak = _rec("weak", (0.70, 0.50, 0.20, 0.25), (0.50, 0.50, 0.20, 0.25), trust_boards={"y": [0, 1]})
    out = corrections.apply_to_placed([a, b], PW, PH, [strong, weak])
    assert out["applied"] == 1 and out["gated"] == 1
    assert (a.x, b.x) == (400.0, 600.0)                                                                  # the trusted record's move stays
