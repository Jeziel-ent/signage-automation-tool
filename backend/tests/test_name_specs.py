"""Stage 2 name-spec pooling (example_layout.pooled_text_specs, behind SIGNAGE_NAME_POOL) and the wide-board review flag."""
from __future__ import annotations

import pytest

from app import confidence, example_layout as ex

FT = 304.8


def board(i, w, h, texts, master="m0"):
    return {"file": f"{i:02d} - {w} X {h} Feet - GSB - S{i}.cdr", "W": w * FT, "H": h * FT, "master": master, "els": {}, "texts": texts, "clip": {}}


def tx(cx, cy, w, h, lines=1):
    return {"cx": cx, "cy": cy, "w": w, "h": h, "x": cx - w / 2, "lines": lines}


def lib():
    return {"brand": "t", "masters": [{"id": "m0", "file": "6 X 3.cdr", "page": [6 * FT, 3 * FT], "elements": []}], "boards": [
        board(1, 6, 3, {"name_en": tx(0.5, 0.10, 0.80, 0.12), "name_ta": tx(0.5, 0.15, 0.85, 0.20)}),
        board(2, 6, 3, {"name_en": tx(0.5, 0.12, 0.82, 0.16, 2), "name_ta": tx(0.5, 0.16, 0.85, 0.22, 2)}),
        board(3, 8, 4, {"name_en": tx(0.5, 0.11, 0.78, 0.14), "name_ta": tx(0.5, 0.14, 0.83, 0.18)}),
        board(4, 10, 3, {"name_en": tx(0.5, 0.30, 0.20, 0.30, 2), "name_ta": tx(0.5, 0.70, 0.30, 0.40, 2)}),
    ]}


def test_pooled_spec_is_the_median_of_the_nearest_boards_of_the_family():
    sp = ex.pooled_text_specs(lib(), 7 * FT, 3.5 * FT, k=3)
    en = sp["name_en"]
    assert en["anchor"] == "center" and en["mode"] == "block" and len(en["pooled_from"]) == 3
    assert en["h"] == pytest.approx(0.14 * 3.5 * FT) and en["cy"] == pytest.approx(0.11 * 3.5 * FT)         # medians of 0.12 / 0.16 / 0.14 and 0.10 / 0.12 / 0.11
    assert en["max_lines"] == 2 and en["w_cap"] <= ex.PAGE_FIT * 7 * FT and en["page_w"] == 7 * FT
    assert sp["name_ta"]["line_h"] > 0


def test_pooling_respects_leave_one_out_and_returns_none_without_evidence():
    L = lib()
    only = {f for f in (b["file"] for b in L["boards"])}
    assert ex.pooled_text_specs(L, 6 * FT, 3 * FT, exclude=only) is None
    left = ex.pooled_text_specs(L, 6 * FT, 3 * FT, exclude={L["boards"][0]["file"], L["boards"][1]["file"]})
    assert left["name_en"]["pooled_from"] and L["boards"][0]["file"] not in left["name_en"]["pooled_from"]


def test_the_flag_gates_the_feature(monkeypatch):
    monkeypatch.delenv("SIGNAGE_NAME_POOL", raising=False)
    assert ex.pool_enabled() is False
    monkeypatch.setenv("SIGNAGE_NAME_POOL", "1")
    assert ex.pool_enabled() is True


def test_wide_boards_are_flagged_for_review_in_the_confidence_label():
    layout = {"mode": "example", "exact": True, "W": 20 * FT, "H": 4 * FT, "distance": 0.0, "extra": 0, "review": ["wide board (5.0:1): check the layout"]}
    c = confidence.layout_confidence(layout)
    assert c["label"] == confidence.REVIEW and any("wide board" in r for r in c["reasons"])
    plain = confidence.layout_confidence({**layout, "review": []})
    assert plain["label"] == confidence.GOOD


def test_threshold_is_the_one_stage1_measured():
    assert ex.WIDE_REVIEW_ASPECT == 4.2
