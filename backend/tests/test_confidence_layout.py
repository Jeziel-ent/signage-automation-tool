"""Confidence of an example-library layout (confidence.layout_confidence) and its report/API wiring."""
from app import confidence as C


def example(exact, distance=0.0, extra=0):
    return {"mode": "example", "file": "x.cdr", "W": 3048.0, "H": 1219.2, "exact": exact, "distance": distance, "extra": extra}


def test_same_size_is_good():
    r = C.layout_confidence(example(True))
    assert r["label"] == "GOOD"
    assert "same size" in r["reasons"][0]
    assert "120 x 48 in" in r["reasons"][0]


def test_near_size_is_review_far_size_is_manual():
    assert C.layout_confidence(example(False, 0.10))["label"] == "REVIEW"
    assert C.layout_confidence(example(False, 0.60))["label"] == "MANUAL"


def test_repeated_art_lowers_a_good_label_to_review():
    r = C.layout_confidence(example(True, extra=5))
    assert r["label"] == "REVIEW"
    assert len(r["reasons"]) == 2


def test_rules_fallback_is_manual_and_no_layout_means_no_label():
    r = C.layout_confidence({"mode": "rules", "reason": "nothing fits"})
    assert r["label"] == "MANUAL"
    assert r["reasons"] == ["nothing fits"]
    assert C.layout_confidence(None) is None


def test_compute_layout_records_where_the_layout_came_from(tmp_path, monkeypatch):
    from app import example_layout as X
    from app.layout import Obj, compute_layout
    objs = [Obj("0", "bg", "shape", 0, 0, 3000, 1000)]
    out = compute_layout(objs, 3000, 1000, 3000, 1000, brand_rule={"brand": "zzz", "example_library": True})
    assert out[0].layout_source["mode"] == "rules"                       # no library for that brand: rules, and it says so
    plain = compute_layout(objs, 3000, 1000, 3000, 1000)
    assert plain[0].layout_source is None                                 # brands without the feature are untouched
