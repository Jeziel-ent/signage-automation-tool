"""Corel Intelligence accuracy: the edit cost of a save, the with / without comparison and the stats endpoint."""
from __future__ import annotations

import copy

from tests.test_corrections import _base
from tests.test_editor_api import client  # noqa: F401  (client is a fixture)

from app import corrections, db


def _moved(dx=100.0):
    edited = copy.deepcopy(_base())
    edited["layers"][0]["children"][0]["x"] += dx          # object "a" moves dx mm right (page is 1000 mm wide)
    return edited


def test_an_unchanged_save_costs_nothing():
    score = corrections.save_cost({"id": "s"}, _base(), _base(), None)
    assert score == {"cost": 0.0, "objects": 0, "used_intelligence": False, "learned": 0}


def test_cost_is_the_distance_moved_in_percent_of_the_page():
    score = corrections.save_cost({"id": "s"}, _base(), _moved(100.0), None)
    assert score["objects"] == 1
    assert score["cost"] == 10.0                           # 100 mm of a 1000 mm page


def test_hiding_an_object_counts_a_fixed_cost():
    edited = copy.deepcopy(_base())
    edited["layers"][0]["children"][0]["visible"] = False
    assert corrections.save_cost({"id": "s"}, _base(), edited, None)["cost"] == corrections.HIDE_COST


def test_a_page_size_edit_is_not_scored():
    edited = copy.deepcopy(_base())
    edited["page"]["width"] = 2000.0
    assert corrections.save_cost({"id": "s"}, _base(), edited, None) is None


def test_the_save_of_a_board_made_with_learning_is_scored_against_the_learned_board():
    # learning already moved "a" by 100 mm (a resize-free move op); the designer saved without touching it: cost 0, and it is "used"
    learned_ops = [{"op": "move", "ids": ["a"], "dx": 100.0, "dy": 0.0}]
    from app import scene_ops
    board = scene_ops.apply_ops(_base(), learned_ops)
    layout = {"intelligence": {"learned_ops": learned_ops, "applied": 0, "nested": {"applied": 1}, "records": ["other"]}}
    score = corrections.save_cost({"id": "s"}, _base(), board, layout)
    assert score["used_intelligence"] is True
    assert score["cost"] == 0.0
    assert score["learned"] == 1


def _row(cost, used, brand="Hangyo"):
    return {"edit_cost": cost, "used_intelligence": used, "brand": brand}


def test_summary_compares_boards_with_and_without_learning():
    stats = [_row(10, False), _row(20, False), _row(3, True), _row(0, True), _row(8, True, "Dalmia")]
    out = corrections.summarize_stats(stats, [])
    assert out["without"] == {"boards": 2, "avg_cost": 15.0, "untouched": 0}
    assert out["with"]["boards"] == 3 and out["with"]["avg_cost"] == 3.67 and out["with"]["untouched"] == 1
    assert out["improvement_pct"] == 75.6
    assert out["brands"]["Hangyo"]["with"]["avg_cost"] == 1.5
    assert out["brands"]["Dalmia"]["improvement_pct"] is None          # no board without learning to compare with
    assert out["lessons"] == {"records": 0, "kept": 0, "corrected": 0, "paused": 0}


def test_summary_counts_how_the_lessons_fared():
    rec = {"shop_id": "x", "record": {"feedback": {"boards": {"b1": [2, 0], "b2": [0, 1]}}}}
    quarantined = {"shop_id": "y", "record": {"feedback": {"boards": {"b1": [0, 3]}}}}
    out = corrections.summarize_stats([], [rec, quarantined])
    assert out["lessons"] == {"records": 2, "kept": 2, "corrected": 4, "paused": 1}
    assert out["improvement_pct"] is None


def test_the_stats_table_keeps_one_row_per_board_and_the_endpoint_reports_it(client):
    db.save_correction_stat("s1", "Hangyo", "6 X 3.cdr", 1830.0, 915.0, "GSB", {"cost": 12.5, "objects": 2, "used_intelligence": False, "learned": 0})
    db.save_correction_stat("s2", "Hangyo", "6 X 3.cdr", 1830.0, 915.0, "GSB", {"cost": 2.5, "objects": 1, "used_intelligence": True, "learned": 3})
    db.save_correction_stat("s1", "Hangyo", "6 X 3.cdr", 1830.0, 915.0, "GSB", {"cost": 10.0, "objects": 1, "used_intelligence": False, "learned": 0})   # replaced
    assert len(db.list_correction_stats()) == 2
    r = client.get("/api/v2/intelligence/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["without"]["avg_cost"] == 10.0 and body["with"]["avg_cost"] == 2.5
    assert body["improvement_pct"] == 75.0
