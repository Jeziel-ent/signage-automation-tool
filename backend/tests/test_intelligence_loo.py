"""The leave-one-out tool: a record is predicted from its peers only, and the report says whether learning helped."""
from __future__ import annotations

from tools import intelligence_loo as loo


def _row(sid, after_cx, updated=1.0, brand="Hangyo", master="6 X 3.cdr", w=1000.0, h=400.0):
    change = {"id": "a", "action": "moved", "signature": {"kind": "shape"},
              "before": {"cx": 0.2, "cy": 0.5, "w": 0.2, "h": 0.2}, "after": {"cx": after_cx, "cy": 0.5, "w": 0.2, "h": 0.2}}
    return {"shop_id": sid, "brand": brand, "master_file": master, "page_w_mm": w, "page_h_mm": h, "board_type": "GSB",
            "updated_at": updated, "record": {"changes": [change], "nested": []}}


def test_a_record_with_no_peer_is_not_evaluated():
    assert loo.evaluate([_row("only", 0.3)]) == []


def test_peers_that_agree_pull_the_object_to_the_designers_answer():
    rows = [_row("r1", 0.30, 3), _row("r2", 0.30, 2), _row("r3", 0.30, 1)]
    res = loo.evaluate(rows)
    assert len(res) == 3
    for r in res:
        assert abs(r["engine_mm"] - 100.0) < 1e-6                       # the engine left it 0.1 of 1000 mm away
        assert r["learned_mm"] < 1.0                         # the peers put it where this designer did
    t = loo.totals(res)
    assert t["better"] == 3 and t["worse"] == 0
    assert t["learned_mean_mm"] < t["engine_mean_mm"]


def test_a_peer_of_another_master_or_brand_is_ignored():
    rows = [_row("r1", 0.3), _row("r2", 0.3, master="10 X 3.cdr"), _row("r3", 0.3, brand="Dalmia")]
    assert loo.evaluate(rows) == []


def test_a_peer_that_disagrees_makes_learning_worse_and_the_report_shows_it():
    rows = [_row("r1", 0.30, 1), _row("r2", 0.50, 2)]        # the newest peer moved the object much further
    res = {r["shop_id"]: r for r in loo.evaluate(rows)}
    assert res["r1"]["learned_mm"] > res["r1"]["engine_mm"]
    assert loo.totals(list(res.values()))["worse"] >= 1
