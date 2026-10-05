"""Tests for the Phase 2 example-based layout engine (app/example_engine.py)
- pure Python, small synthetic masters/boards, no CorelDRAW, no real
dataset files.
"""
import pytest
from app.example_engine import (
    Entity, build_examples, master_entities, match_entities, predict_entities, predict_layout,
)
from app.layout import Obj

PAGE = (1000.0, 400.0)
MASTER_OBJS = [
    Obj("bg", "bg", "shape", 0, 0, 1000, 400),
    # two curve fragments close together form one logo_cluster (real logos
    # are ~20-130 fragments; MIN_CLUSTER_SHAPES=2 filters stray single shapes)
    Obj("logo_a", "curve", "curve", 100, 100, 100, 200),
    Obj("logo_b", "curve", "curve", 200, 100, 100, 200),
    Obj("footer", "Authorized Dealer", "text", 10, 10, 80, 20, text="Authorized Dealer"),
    Obj("name", "Shop name", "text", 400, 300, 200, 40, text="MASTER SHOP"),
]
SHOPNAME_HINTS = ["MASTER SHOP"]


def test_master_entities_classifies_each_role():
    ents = master_entities(MASTER_OBJS, *PAGE, SHOPNAME_HINTS)
    kinds = {e.key: e.kind for e in ents}
    assert kinds["bg"] == "bg"
    assert kinds["shopname"] == "shopname"
    assert kinds["text_0"] == "text"
    assert kinds["logo_cluster_0"] == "logo_cluster"


def test_match_entities_finds_shopname_by_relative_height_when_text_changed():
    # board's shop name text no longer matches the master's hint (a
    # different shop) - shopname must still be found by relative height,
    # not lost into the generic "text" bucket
    board_objs = [
        Obj("bg", "bg", "shape", 0, 0, 2000, 400),
        Obj("logo_a", "curve", "curve", 200, 100, 200, 200),
        Obj("logo_b", "curve", "curve", 400, 100, 200, 200),
        Obj("footer", "Authorized Dealer", "text", 20, 10, 160, 20, text="Authorized Dealer"),
        Obj("name", "Shop name", "text", 800, 300, 400, 40, text="A COMPLETELY DIFFERENT SHOP"),
    ]
    m_ents = master_entities(MASTER_OBJS, *PAGE, SHOPNAME_HINTS)
    b_ents = master_entities(board_objs, 2000, 400, SHOPNAME_HINTS)  # hint won't match on this board
    matched = match_entities(m_ents, *PAGE, b_ents, 2000, 400)
    shopname_matches = matched["shopname"]
    assert len(shopname_matches) == 1
    assert shopname_matches[0].text == "A COMPLETELY DIFFERENT SHOP"


def test_build_examples_records_relative_transforms():
    m_ents = master_entities(MASTER_OBJS, *PAGE, SHOPNAME_HINTS)
    board_objs = [
        Obj("bg", "bg", "shape", 0, 0, 2000, 400),
        Obj("logo_a", "curve", "curve", 200, 100, 200, 200),  # same relative size/pos, wider page
        Obj("logo_b", "curve", "curve", 400, 100, 200, 200),
        Obj("footer", "f", "text", 20, 10, 160, 20, text="Authorized Dealer"),
        Obj("name", "n", "text", 800, 300, 400, 40, text="OTHER SHOP"),
    ]
    b_ents = master_entities(board_objs, 2000, 400, SHOPNAME_HINTS)
    examples = build_examples(m_ents, *PAGE, [(2000.0, 400.0, b_ents)])
    logo = examples["boards"][0]["entities"]["logo_cluster_0"]
    assert logo["present"]
    assert logo["repeat_count"] == 1
    assert logo["copies"][0]["cx_frac"] == pytest.approx((200 + 400 / 2) / 2000)
    assert logo["copies"][0]["h_frac"] == pytest.approx(200 / 400)


def test_predict_entities_uses_exact_match_within_tolerance():
    examples = {"boards": [
        {"aspect": 2.5, "entities": {"logo_cluster_0": {"present": True, "repeat_count": 1,
                                                          "copies": [{"cx_frac": 0.3, "cy_frac": 0.5, "w_frac": 0.2, "h_frac": 0.5}]}}},
        {"aspect": 5.0, "entities": {"logo_cluster_0": {"present": True, "repeat_count": 1,
                                                          "copies": [{"cx_frac": 0.5, "cy_frac": 0.5, "w_frac": 0.1, "h_frac": 0.3}]}}},
    ]}
    pred = predict_entities(examples, 2505.0, 1002.0)  # aspect ~2.5, within tolerance of first example
    assert pred["logo_cluster_0"]["copies"][0]["cx_frac"] == pytest.approx(0.3)


def test_predict_entities_interpolates_between_two_examples():
    examples = {"boards": [
        {"aspect": 2.0, "entities": {"k": {"present": True, "repeat_count": 1,
                                            "copies": [{"cx_frac": 0.2, "cy_frac": 0.5, "w_frac": 0.1, "h_frac": 0.4}]}}},
        {"aspect": 4.0, "entities": {"k": {"present": True, "repeat_count": 1,
                                            "copies": [{"cx_frac": 0.8, "cy_frac": 0.5, "w_frac": 0.1, "h_frac": 0.4}]}}},
    ]}
    pred = predict_entities(examples, 3000.0, 1000.0)  # aspect 3.0, exactly midway
    assert pred["k"]["copies"][0]["cx_frac"] == pytest.approx(0.5, abs=0.01)


def test_predict_entities_returns_none_without_examples():
    assert predict_entities({"boards": []}, 1000, 400) is None
    assert predict_entities(None, 1000, 400) is None


def test_predict_layout_uses_brand_override_only_when_tiling_triggers():
    m_ents = master_entities(MASTER_OBJS, *PAGE, SHOPNAME_HINTS)
    examples = build_examples(m_ents, *PAGE, [(1000.0, 400.0, m_ents)])  # trivial single example = master itself
    calls = []

    def fake_override(master_ents, target_w, target_h):
        calls.append((target_w, target_h))
        return {"logo_cluster_0": {"present": True, "repeat_count": 2, "copies": [{}, {}]}}

    from app import example_engine
    example_engine.WIDE_PANEL_OVERRIDES["test_brand"] = fake_override
    try:
        # mild resize: no override should fire
        predict_layout("test_brand", m_ents, *PAGE, examples, 1100.0, 440.0)
        assert calls == []
        # wide resize: override should fire and its result should win
        pred = predict_layout("test_brand", m_ents, *PAGE, examples, 4000.0, 400.0)
        assert calls == [(4000.0, 400.0)]
        assert pred["logo_cluster_0"]["repeat_count"] == 2
    finally:
        del example_engine.WIDE_PANEL_OVERRIDES["test_brand"]
