"""Step 5: confidence_label (app/confidence.py) - callable at convert time
with no designer file. Pure Python; `bounds` is injected directly in every
test so nothing here depends on the real (and potentially-regenerated)
app/confidence_bounds.json - a separate test at the bottom checks THAT file
still parses and gives sane real-dalmia labels.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.confidence import GOOD, MANUAL, REVIEW, confidence_label

BOUNDS = {
    "dalmia": {
        "master_mm": {"w": 3048.0, "h": 1219.2},  # 120x48in, aspect 2.5
        "regimes": {
            "untiled": {"n_samples": 7, "aspect_min": 2.4, "aspect_max": 2.5,
                       "median_position_error_mm": 115.7, "median_size_error_mm": 139.4,
                       "max_position_error_mm": 533.0, "max_size_error_mm": 1073.0},
            "tiled": {"n_samples": 4, "aspect_min": 3.0, "aspect_max": 4.5,
                     "median_position_error_mm": 427.5, "median_size_error_mm": 1905.0,
                     "max_position_error_mm": 837.7, "max_size_error_mm": 2438.4},
        },
    },
}


def test_good_when_aspect_is_within_the_validated_untiled_range_and_nothing_else_is_wrong():
    r = confidence_label(3048.0, 1219.2, "dalmia", bounds=BOUNDS)  # exactly the master's own size, aspect 2.5
    assert r["label"] == GOOD and r["tiled"] is False and r["regime"] == "untiled"
    assert r["aspect_ratio"] == pytest.approx(2.5)
    assert any("validated" in reason for reason in r["reasons"])


def test_review_when_the_request_would_tile_because_tiled_boards_have_large_historical_error():
    # 216in x 48in on a 120x48in master -> aspect 4.5, tiles, well within the tiled range
    r = confidence_label(5486.4, 1219.2, "dalmia", bounds=BOUNDS)
    assert r["tiled"] is True and r["regime"] == "tiled"
    assert r["label"] == REVIEW
    assert any("historically had a median" in reason for reason in r["reasons"])


def test_manual_when_the_aspect_ratio_is_outside_every_validated_range():
    # a near-square board: aspect ~1.05, not close to either the untiled (2.4-2.5) or tiled (3.0-4.5) range
    r = confidence_label(1280.0, 1219.2, "dalmia", bounds=BOUNDS)
    assert r["label"] == MANUAL
    assert any("outside the validated" in reason and "extrapolation" in reason for reason in r["reasons"])


def test_manual_for_an_unknown_brand_without_a_designer_file():
    r = confidence_label(3000.0, 1200.0, "some_new_brand", bounds=BOUNDS)
    assert r["label"] == MANUAL
    assert "never benchmarked" in r["reasons"][0]


def test_manual_for_non_positive_size():
    for w, h in [(0, 100), (100, 0), (-5, 100)]:
        r = confidence_label(w, h, "dalmia", bounds=BOUNDS)
        assert r["label"] == MANUAL


def test_content_check_failure_can_only_make_the_label_worse_never_better():
    good = confidence_label(3048.0, 1219.2, "dalmia", bounds=BOUNDS)
    assert good["label"] == GOOD
    worse = confidence_label(3048.0, 1219.2, "dalmia", bounds=BOUNDS,
                             content_check={"overall": "CONTENT_FAIL", "shop_name": {"status": "CONTENT_FAIL"}})
    assert worse["label"] == MANUAL
    assert any("content check failed" in r and "shop_name" in r for r in worse["reasons"])
    # a passing content check on an already-GOOD board does not change anything
    still_good = confidence_label(3048.0, 1219.2, "dalmia", bounds=BOUNDS,
                                  content_check={"overall": "CONTENT_OK"})
    assert still_good["label"] == GOOD


def test_layout_check_failure_and_warning_affect_the_label_correctly():
    fail = confidence_label(3048.0, 1219.2, "dalmia", bounds=BOUNDS,
                            layout_checks=[{"check": "within_page", "status": "fail", "detail": "x"}])
    assert fail["label"] == MANUAL and any("within_page" in r for r in fail["reasons"])
    warn = confidence_label(3048.0, 1219.2, "dalmia", bounds=BOUNDS,
                            layout_checks=[{"check": "min_margin", "status": "warn", "detail": "x"},
                                          {"check": "text_legibility", "status": "pass", "detail": "x"}])
    assert warn["label"] == REVIEW and any("min_margin" in r for r in warn["reasons"])


def test_a_manual_from_aspect_range_is_not_downgraded_by_a_clean_content_check():
    r = confidence_label(1280.0, 1219.2, "dalmia", bounds=BOUNDS, content_check={"overall": "CONTENT_OK"})
    assert r["label"] == MANUAL  # aspect-range extrapolation alone is enough; a good content check can't rescue it


def test_no_bounds_for_the_regime_at_all_is_manual():
    thin = {"dalmia": {"master_mm": {"w": 3048.0, "h": 1219.2}, "regimes": {
        "untiled": BOUNDS["dalmia"]["regimes"]["untiled"],
    }}}
    r = confidence_label(5486.4, 1219.2, "dalmia", bounds=thin)  # would tile; no tiled regime known at all
    assert r["label"] == MANUAL and any("no validated tiled samples" in reason for reason in r["reasons"])


def test_real_confidence_bounds_file_is_present_and_gives_sane_labels_for_every_validated_dalmia_board():
    """Not a synthetic-bounds test: checks the actual derived file this session
    (tools/derive_confidence_bounds.py dalmia) - every board that WAS validated
    should come back at least GOOD/REVIEW (never MANUAL purely for being
    'unknown'), since by definition it's inside its own regime's range.
    """
    path = Path(__file__).resolve().parents[1] / "app" / "confidence_bounds.json"
    if not path.exists():
        pytest.skip("confidence_bounds.json not generated in this checkout")
    bounds = json.loads(path.read_text(encoding="utf-8"))
    assert "dalmia" in bounds
    db = bounds["dalmia"]
    for regime_name, rb in db["regimes"].items():
        mid_aspect = (rb["aspect_min"] + rb["aspect_max"]) / 2
        h = 1000.0
        w = h * mid_aspect
        if regime_name == "tiled":
            w = db["master_mm"]["w"] * 4  # comfortably in the tiling regime
            h = w / mid_aspect
        r = confidence_label(w, h, "dalmia", bounds=bounds)
        assert r["label"] in (GOOD, REVIEW), (regime_name, r)
