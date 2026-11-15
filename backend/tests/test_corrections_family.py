"""Corel Intelligence across sizes: a correction made on another size of the same master teaches this board by its SHIFT, damped and weighted
by how near the size is - and only for objects no record of this size (or a nearby one) covers."""
from __future__ import annotations

import math

from app import corrections
from tools import intelligence_loo as loo


class P:
    """The bits of a layout.Placed that apply_to_placed reads and writes."""

    def __init__(self, oid, cx, cy, w, h, new_w, new_h):
        self.id, self.role, self.w, self.h, self.warnings = oid, "logo", w * new_w, h * new_h, []
        self.x, self.y = cx * new_w - self.w / 2, cy * new_h - self.h / 2


def _row(sid, w, h, shift=(0.05, 0.0), updated=1.0, brand="Hangyo", master="6 X 3.cdr", board_type="GSB", aspect=4.0, before=(0.30, 0.50, 0.20, 0.10)):
    cx, cy, bw, bh = before
    change = {"id": "a", "action": "moved", "signature": {"kind": "shape", "aspect": aspect, "n_desc": 0},
              "before": {"cx": cx, "cy": cy, "w": bw, "h": bh}, "after": {"cx": cx + shift[0], "cy": cy + shift[1], "w": bw, "h": bh}}
    return {"shop_id": sid, "brand": brand, "master_file": master, "page_w_mm": w, "page_h_mm": h, "board_type": board_type,
            "updated_at": updated, "record": {"changes": [change], "nested": []}}


# a 6 x 3 ft board (1829 x 914) is the record; the board being made is 8 x 4 ft (2438 x 1219): same shape, bigger
REC_W, REC_H = 1828.8, 914.4
NEW_W, NEW_H = 2438.4, 1219.2


def test_other_sizes_of_the_same_master_are_family_records_nearest_first():
    near = _row("near", 2000.0, 1000.0)                   # similar shape and size
    far = _row("far", 3600.0, 600.0)                      # a very different (wide) board: too far to teach this one
    other_master = _row("om", REC_W, REC_H, master="10 X 3.cdr")
    got = corrections.usable_records([near, far, other_master], "Hangyo", "6 X 3.cdr", NEW_W, NEW_H, "GSB", family=True)
    assert [r["shop_id"] for r in got] == ["near"]
    assert got[0]["family"] is True and got[0]["_dist"] >= 0


def test_family_records_are_off_unless_asked_for():
    assert corrections.usable_records([_row("r", REC_W, REC_H)], "Hangyo", "6 X 3.cdr", NEW_W, NEW_H, "GSB") == []


def test_exact_size_records_are_not_family_records():
    got = corrections.usable_records([_row("same", NEW_W, NEW_H)], "Hangyo", "6 X 3.cdr", NEW_W, NEW_H, "GSB", family=True)
    assert [("family" in r) for r in got] == [False]


def test_a_shift_made_on_another_size_moves_the_matching_object_here_damped():
    rec = _row("r", REC_W, REC_H, shift=(0.05, 0.02))
    (r,) = corrections.usable_records([rec], "Hangyo", "6 X 3.cdr", NEW_W, NEW_H, "GSB", family=True)
    obj = P("o1", 0.31, 0.50, 0.20, 0.10, NEW_W, NEW_H)     # the engine placed it a little differently than on the record's size
    out = corrections.apply_to_placed([obj], NEW_W, NEW_H, [r])
    assert out["applied"] == 1 and out["family"] == 1
    assert math.isclose((obj.x + obj.w / 2) / NEW_W, 0.31 + corrections.FAMILY_STRENGTH * 0.05, abs_tol=1e-9)
    assert math.isclose((obj.y + obj.h / 2) / NEW_H, 0.50 + corrections.FAMILY_STRENGTH * 0.02, abs_tol=1e-9)
    assert "other sizes" in obj.warnings[0]


def test_an_object_of_a_different_shape_is_not_matched():
    (r,) = corrections.usable_records([_row("r", REC_W, REC_H, aspect=4.0)], "Hangyo", "6 X 3.cdr", NEW_W, NEW_H, "GSB", family=True)
    tall = P("o1", 0.30, 0.50, 0.10, 0.30, NEW_W, NEW_H)    # aspect ~0.7 against the recorded 4.0
    out = corrections.apply_to_placed([tall], NEW_W, NEW_H, [r])
    assert out["applied"] == 0 and out["skipped"] == 0       # unmatched family changes are not counted as skipped


def test_an_object_far_from_the_recorded_place_is_not_matched():
    (r,) = corrections.usable_records([_row("r", REC_W, REC_H)], "Hangyo", "6 X 3.cdr", NEW_W, NEW_H, "GSB", family=True)
    elsewhere = P("o1", 0.80, 0.20, 0.20, 0.10, NEW_W, NEW_H)
    assert corrections.apply_to_placed([elsewhere], NEW_W, NEW_H, [r])["applied"] == 0


def test_a_record_of_this_size_beats_family_records():
    exact = _row("exact", NEW_W, NEW_H, shift=(-0.10, 0.0), before=(0.31, 0.50, 0.20, 0.10))
    other = _row("other", REC_W, REC_H, shift=(0.10, 0.0))
    recs = corrections.usable_records([exact, other], "Hangyo", "6 X 3.cdr", NEW_W, NEW_H, "GSB", family=True)
    obj = P("o1", 0.31, 0.50, 0.20, 0.10, NEW_W, NEW_H)
    out = corrections.apply_to_placed([obj], NEW_W, NEW_H, recs)
    assert "family" not in out
    assert math.isclose((obj.x + obj.w / 2) / NEW_W, 0.21, abs_tol=1e-6)       # the exact-size answer, not 0.31 + 0.085


def test_a_nearer_size_counts_more_than_a_farther_one():
    near = _row("near", 2300.0, 1150.0, shift=(0.04, 0.0))
    far = _row("far", 1500.0, 1100.0, shift=(0.10, 0.0))
    recs = corrections.usable_records([near, far], "Hangyo", "6 X 3.cdr", NEW_W, NEW_H, "GSB", family=True)
    assert [r["shop_id"] for r in recs] == ["near", "far"]
    obj = P("o1", 0.30, 0.50, 0.20, 0.10, NEW_W, NEW_H)
    corrections.apply_to_placed([obj], NEW_W, NEW_H, recs)
    moved = (obj.x + obj.w / 2) / NEW_W - 0.30
    k = corrections.FAMILY_STRENGTH
    assert 0.04 * k < moved < 0.10 * k                       # between the two answers ...
    assert abs(moved - 0.04 * k) < abs(moved - 0.10 * k)     # ... and closer to the nearer size's


def test_nested_learning_never_uses_family_records():
    rec = _row("r", REC_W, REC_H)
    rec["record"]["nested"] = [{"id": "n1", "action": "moved", "before": {"cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1},
                                "after": {"cx": 0.6, "cy": 0.5, "w": 0.1, "h": 0.1}}]
    assert corrections.usable_records([rec], "Hangyo", "6 X 3.cdr", NEW_W, NEW_H, "GSB") == []      # what nested_ops / the scene path asks for


def test_cross_size_leave_one_out_predicts_a_record_from_other_sizes_only():
    rows = [_row("a", 1828.8, 914.4, shift=(0.05, 0.0), updated=3), _row("b", 2400.0, 1200.0, shift=(0.05, 0.0), updated=2),
            _row("c", 3200.0, 1600.0, shift=(0.05, 0.0), updated=1)]
    same = loo.evaluate(rows)
    assert same == []                                        # no two boards share a size: nothing to learn from in "same size" mode
    cross = loo.evaluate(rows, cross=True)
    assert len(cross) == 3
    t = loo.totals(cross)
    assert t["learned_mean_mm"] < t["engine_mean_mm"]       # other sizes pull the object most of the way to the designer's answer
    assert t["better"] == 3 and t["worse"] == 0
