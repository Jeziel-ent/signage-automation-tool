"""Seeding the correction memory from designer files: units are matched as a whole and moved rigidly."""
from __future__ import annotations

from tools import seed_corrections as sc

PW, PH = 1000.0, 400.0


def _shape(x, y, w, h, kind="curve", path=None):
    return {"name": "", "type": kind, "x": x, "y": y, "w": w, "h": h, "group_path": path or []}


def _dump(*shapes):
    return {"page_mm": {"w": PW, "h": PH}, "shape_count": len(shapes), "shapes": list(shapes)}


def _logo(x, y):
    """A logo of three touching loose curves, 120 x 60 mm, lower-left corner at (x, y)."""
    return [_shape(x, y, 40, 60), _shape(x + 42, y, 40, 60), _shape(x + 84, y, 36, 60)]


def test_a_unit_that_the_designer_moved_is_recorded_for_every_member_with_the_same_shift():
    ours = _dump(*_logo(100, 100))
    real = _dump(*_logo(160, 90))                                # same logo, 60 mm right and 10 mm down
    changes, stats = sc.build_changes(ours, real, PW, PH)
    assert stats["matched"] == 1
    assert stats["units_changed"] == 1
    assert len(changes) == 3
    for c in changes:
        assert round((c["after"]["cx"] - c["before"]["cx"]) * PW, 1) == 60.0
        assert round((c["after"]["cy"] - c["before"]["cy"]) * PH, 1) == -10.0
        assert c["action"] == "moved"


def test_a_unit_the_designer_resized_scales_its_members_rigidly():
    ours = _dump(*_logo(100, 100))
    real = _dump(*[_shape(100 + (s["x"] - 100) * 1.25, 100, s["w"] * 1.25, 75) for s in _logo(100, 100)])
    changes, _ = sc.build_changes(ours, real, PW, PH)
    assert all(c["action"] == "moved+resized" for c in changes)
    assert round(changes[1]["after"]["w"] / changes[1]["before"]["w"], 3) == 1.25      # every member grows by the unit's factor
    assert round(changes[1]["after"]["h"] / changes[1]["before"]["h"], 3) == 1.25


def test_a_unit_already_in_place_and_text_and_background_record_nothing():
    ours = _dump(*_logo(100, 100), _shape(0, 0, 1000, 400, "rectangle"), _shape(300, 20, 200, 30, "text"))
    real = _dump(*_logo(100.5, 100), _shape(0, 0, 1000, 400, "rectangle"), _shape(350, 20, 200, 30, "text"))
    changes, stats = sc.build_changes(ours, real, PW, PH)
    assert changes == []
    assert stats["units_changed"] == 0


def test_units_are_not_matched_across_very_different_sizes_or_far_apart():
    ours = _dump(*_logo(100, 100))
    far = _dump(*_logo(800, 300))                                # 70 % of the page away
    big = _dump(*[_shape(100 + i * 100, 100, 90, 200) for i in range(3)])
    assert sc.build_changes(ours, far, PW, PH)[1]["matched"] == 0
    assert sc.build_changes(ours, big, PW, PH)[1]["matched"] == 0


def test_only_top_level_shapes_of_the_engines_output_are_candidates():
    ours = _dump(*_logo(100, 100), _shape(500, 100, 40, 40, path=["g1"]))
    assert len(sc.ours_objects(ours, PW, PH)) == 3


def test_the_record_applies_back_to_the_engines_own_placement():
    from app import corrections
    ours = _dump(*_logo(100, 100))
    real = _dump(*_logo(160, 90))
    rec, _ = sc.board_record("b", "m.cdr", "02 - board", "GSB", ours, real, PW, PH)
    assert rec["status"] == "approved"
    assert rec["source"] == sc.SOURCE
    assert rec["shop_id"] == "seed:b:02 - board"
    placed = [sc._P(i, "logo", s["x"], s["y"], s["w"], s["h"]) for i, s in enumerate(ours["shapes"])]
    out = corrections.apply_to_placed(placed, PW, PH, [{"record": rec, "shop_id": rec["shop_id"]}])
    assert out["applied"] == 3
    assert (placed[0].x, placed[0].y) == (160, 90)


def test_evaluate_shows_a_gain_when_two_designers_agree_and_none_when_there_is_nothing_to_apply():
    ours = _dump(*_logo(100, 100))
    boards = []
    for stem, dx in (("A", 60), ("B", 60)):
        real = _dump(*_logo(100 + dx, 100))
        rec, _ = sc.board_record("b", "m.cdr", stem, None, ours, real, PW, PH)
        boards.append({"stem": stem, "brand": "b", "master_file": "m.cdr", "pw": PW, "ph": PH, "board_type": None,
                       "ours_dump": ours, "real_dump": real, "record": rec})
    rows = sc.evaluate(boards)
    assert len(rows) == 2
    assert all(r["engine_mean_mm"] > 50 and r["learned_mean_mm"] < 1 for r in rows)
    assert sc.evaluate(boards[:1]) == []                          # one board of a size: nothing to hold out against
