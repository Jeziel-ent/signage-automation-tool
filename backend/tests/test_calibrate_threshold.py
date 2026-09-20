"""Step 3: threshold-calibration proposal logic (tools/calibrate_threshold.py) -
pure Python, small synthetic values/labels, no CorelDRAW and no real report data.
"""
from __future__ import annotations

from tools.calibrate_threshold import best_threshold, propose_thresholds


def test_best_threshold_perfectly_separable_higher_is_better():
    values = [0.9, 0.8, 0.95, 0.3, 0.2, 0.4]
    labels = [True, True, True, False, False, False]
    r = best_threshold(values, labels, "higher_is_better")
    assert r["accuracy"] == 1.0 and r["n_correct"] == 6 and r["misclassified"] == []
    assert 0.4 < r["threshold"] < 0.8  # anywhere in the gap is a perfect split


def test_best_threshold_perfectly_separable_lower_is_better():
    values = [1.0, 2.0, 1.5, 8.0, 9.0, 7.5]  # e.g. position error mm: small = OK
    labels = [True, True, True, False, False, False]
    r = best_threshold(values, labels, "lower_is_better")
    assert r["accuracy"] == 1.0
    assert 2.0 < r["threshold"] < 7.5


def test_best_threshold_reports_the_best_achievable_accuracy_when_not_separable():
    values = [1, 2, 3, 4, 5]
    labels = [True, False, True, False, True]  # no threshold does better than 3/5
    r = best_threshold(values, labels, "higher_is_better")
    assert r["accuracy"] == 0.6
    assert r["n_correct"] == 3 and r["n_total"] == 5
    assert len(r["misclassified"]) == 2


def test_best_threshold_ties_prefer_the_middle_of_the_range():
    # value 3 is a mislabelled outlier (True when the pattern says values >= 4 are OK);
    # both t=2.5 and t=4.5 then score the same (4/5) accuracy - expect the one closer to
    # the overall midpoint (3.0), i.e. 2.5, not 4.5, so the proposal isn't glued to one board.
    values = [1, 2, 3, 4, 5]
    labels = [False, False, True, False, True]
    r = best_threshold(values, labels, "higher_is_better")
    assert r["accuracy"] == 0.8
    assert r["threshold"] == 2.5


def test_best_threshold_handles_no_data():
    r = best_threshold([], [], "higher_is_better")
    assert r == {"threshold": None, "accuracy": None, "n_correct": 0, "n_total": 0, "misclassified": []}


def test_best_threshold_everything_passes_or_fails_is_a_valid_answer():
    # every labelled board is OK and every value is (coincidentally) similar - the best
    # boundary really is "nothing is ever rejected" (a threshold below the minimum).
    values = [0.8, 0.81, 0.79]
    labels = [True, True, True]
    r = best_threshold(values, labels, "higher_is_better")
    assert r["accuracy"] == 1.0
    assert r["threshold"] < min(values)


def _card(safe, visual=None, pos_mm=None, size_mm=None, area=None, content=None):
    c = {"safe": safe, "shop": safe, "target": "1000x500mm"}
    if visual is not None:
        c["visual"] = {"combined": visual}
    if pos_mm is not None or size_mm is not None:
        c["geometric"] = {
            "position_error_mm": {"max": pos_mm, "mean": pos_mm},
            "size_error_mm": {"max": size_mm, "mean": size_mm},
            "area_matched_pct": [{"tolerance_mm": t, "pct": p} for t, p in (area or {}).items()],
        }
    if content is not None:
        c["content_check"] = {"overall": content}
    return c


def test_propose_thresholds_ranks_the_perfectly_separating_metric_first():
    cards = [
        _card("a", visual=0.95, pos_mm=1.0, size_mm=1.0, area={2.0: 100, 5.0: 100, 10.0: 100}, content="CONTENT_OK"),
        _card("b", visual=0.90, pos_mm=2.0, size_mm=2.0, area={2.0: 90, 5.0: 100, 10.0: 100}, content="CONTENT_OK"),
        _card("c", visual=0.60, pos_mm=20.0, size_mm=20.0, area={2.0: 10, 5.0: 20, 10.0: 30}, content="CONTENT_FAIL"),
        _card("d", visual=0.55, pos_mm=25.0, size_mm=25.0, area={2.0: 5, 5.0: 10, 10.0: 15}, content="CONTENT_OK"),
    ]
    labels = {"a": True, "b": True, "c": False, "d": False}
    result = propose_thresholds(cards, labels)
    assert result["n_labelled"] == 4 and result["n_ok"] == 2 and result["n_not_ok"] == 2
    assert result["proposals"][0]["accuracy"] == 1.0  # several metrics separate this cleanly; best one is first
    names = {p["metric"] for p in result["proposals"]}
    assert "visual_combined" in names and "geo_max_position_error_mm" in names
    # content check alone gets 3/4 right (misses "d") - reported for comparison, not ranked among threshold proposals
    assert result["content_check_reference"]["accuracy"] == 0.75
    assert result["content_check_reference"]["misclassified"] == ["d"]


def test_propose_thresholds_only_uses_labelled_boards_and_reports_the_rest():
    cards = [_card("a", visual=0.9), _card("b", visual=0.5), _card("unlabelled", visual=0.7)]
    labels = {"a": True, "b": False}
    result = propose_thresholds(cards, labels)
    assert result["unlabelled_boards"] == ["unlabelled"]
    for p in result["proposals"]:
        if p["metric"] == "visual_combined":
            assert p["n_total"] == 2


def test_propose_thresholds_ignores_labels_for_boards_not_in_the_report():
    cards = [_card("a", visual=0.9)]
    labels = {"a": True, "ghost_board": False}
    result = propose_thresholds(cards, labels)
    assert result["unknown_labels_ignored"] == ["ghost_board"]
    assert result["n_labelled"] == 1


def test_propose_thresholds_skips_a_metric_with_too_few_distinct_values():
    cards = [_card("a", visual=0.8), _card("b", visual=0.8)]  # identical values - nothing to split on
    labels = {"a": True, "b": False}
    result = propose_thresholds(cards, labels)
    visual = next(p for p in result["proposals"] if p["metric"] == "visual_combined")
    assert "note" in visual and visual["accuracy"] is None
