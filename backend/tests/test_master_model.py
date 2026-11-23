from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import master_model as mm
from spatial_fixtures import build, extraction, obj, sign_10x8


def test_normalized_geometry_keeps_exact_millimetres():
    m = build()
    o = m.get("3")
    assert o.box_mm == {"x": 1000.0, "y": 1200.0, "width": 1600.0, "height": 300.0}
    n = o.normalized
    assert n["x"] == pytest.approx(1000 / 3048, abs=1e-5) and n["height"] == pytest.approx(300 / 2438.4, abs=1e-5)
    assert m.page == {"width_mm": 3048.0, "height_mm": 2438.4, "unit": "mm", "origin": "bottom-left"}
    assert m.orientation == "landscape"


def test_roles_regions_and_modes():
    m = build()
    assert m.get("0").role == "bg" and m.get("0").adaptation_mode == mm.STRETCH
    assert m.get("5").role == "fixed" and m.get("5").adaptation_mode == mm.FIXED          # name tag fixed_*
    assert m.get("3").adaptation_mode == mm.TEXT_FIT and m.get("3").type == "text"
    assert m.get("1").adaptation_mode == mm.UNIFORM_SCALE
    badge = m.get("6")
    assert badge.adaptation_mode == mm.ANCHOR and set(badge.constraints["anchor"]) == {"right", "top"}
    assert m.get("1").region == "header" and m.get("4").region in ("product", "footer")
    assert m.get("1").h_region == "left" and m.get("0").region == "background"
    for o in m.top_level():
        assert o.adaptation_mode in mm.MODES and 0 < o.mode_confidence <= 1 and o.mode_reason


def test_adaptive_bands_split_at_gaps_and_ignore_page_spanning_objects():
    m = build()
    cols, rows = m.bands["columns"], m.bands["rows"]
    assert len(cols) >= 1 and len(rows) >= 2                                            # the bg does not fuse everything
    assert all(0 <= c["x0"] < c["x1"] <= 1 for c in cols)
    assert m.get("0").bands == {"columns": [], "rows": []} or m.get("0").bands["rows"]   # bg may overlap bands; it must not create them
    assert m.get("4").bands["rows"] and m.get("4").bands["columns"]


def test_relations_containment_text_on_alignment_and_overlap():
    m = build()
    kinds = {r["kind"] for r in m.relations}
    assert {"contains", "text_on"} <= kinds
    assert any(r["kind"] == "text_on" and r["text"] == "3" and r["surface"] == "2" for r in m.relations)
    assert any(r["kind"] == "contains" and r["outer"] == "2" and r["inner"] == "3" for r in m.relations)


def test_alignment_equal_gap_and_repeats():
    objs = [obj(0, "bg", "rectangle", 0, 0, 3000, 1000)]
    for i in range(4):
        objs.append(obj(i + 1, f"p{i}", "group", 200 + i * 600, 300, 400, 300))          # same size, same bottom, gap 200
    m = mm.build_model(extraction(3000, 1000, objs))
    assert any(r["kind"] == "aligned" and r["edge"] == "bottom" and len(r["ids"]) == 4 for r in m.relations)
    eg = [r for r in m.relations if r["kind"] == "equal_gap" and r["axis"] == "x"]
    assert eg and eg[0]["gap_mm"] == pytest.approx(200, abs=0.5) and len(eg[0]["ids"]) == 4
    assert any(r["kind"] == "repeat" and len(r["ids"]) == 4 for r in m.relations)
    assert all(m.get(str(i)).adaptation_mode == mm.TILE for i in range(1, 5))


def test_loose_fragments_form_a_visual_unit():
    objs = [obj(0, "bg", "rectangle", 0, 0, 3000, 1000)]
    for i in range(5):
        objs.append(obj(i + 1, f"c{i}", "curve", 100 + i * 12, 200, 10, 40))
    objs.append(obj(6, "far", "curve", 2500, 800, 30, 30))
    m = mm.build_model(extraction(3000, 1000, objs))
    assert len(m.units) == 1 and sorted(m.units[0]["members"]) == ["1", "2", "3", "4", "5"]


def test_json_round_trip_and_schema_check():
    m = build()
    again = mm.MasterModel.from_dict(json.loads(json.dumps(m.to_dict())))
    assert again.to_dict() == m.to_dict()
    bad = m.to_dict() | {"schema_version": 99}
    with pytest.raises(ValueError, match="schema"):
        mm.MasterModel.from_dict(bad)


def test_validation_rejects_bad_geometry():
    with pytest.raises(ValueError):
        mm.build_model(extraction(0, 100, [obj(0, "a", "curve", 0, 0, 1, 1)]))
    e = sign_10x8()
    e["objects"][1]["w"] = -5
    with pytest.raises(ValueError, match="negative"):
        mm.build_model(e)
    d = build().to_dict()
    d["objects"][1]["id"] = d["objects"][0]["id"]
    with pytest.raises(ValueError, match="duplicate"):
        mm.MasterModel.from_dict(d)


def test_to_scene_is_an_editor_scene_orientation_adapter_accepts():
    from app import orientation_adapter as oa, scene_ops

    m = build()
    scene = m.to_scene()
    assert scene["page"] == {"width": 3048.0, "height": 2438.4}
    zones, _ = oa.classify_zones(scene)
    assert set(zones) >= {"header", "product", "main_text", "footer", "background"}
    ops = oa.convert_orientation(scene, 1000.0, 2000.0)
    final = scene_ops.apply_ops(scene, ops)
    assert final["page"]["width"] == 1000.0


def test_truncated_and_mismatched_shape_sets_warn():
    e = sign_10x8()
    e["truncated"] = True
    e["shape_sets"] = {"equal": False}
    m = mm.build_model(e)
    assert any("object limit" in w for w in m.warnings) and any("layer walk" in w for w in m.warnings)


CACHE = Path(__file__).resolve().parents[1] / "dataset_analysis" / "real_dumps_cache" / "hangyo"


@pytest.mark.skipif(not (CACHE / "10_X_3.json").exists(), reason="cached real dump not in this checkout")
def test_real_hangyo_dump_builds_a_valid_model():
    d = json.loads((CACHE / "10_X_3.json").read_text(encoding="utf8"))
    objs, top = [], -1
    for s in d["shapes"]:
        if s.get("group_path"):
            continue
        top += 1
        e = obj(top, s["name"], s["type"], s["x"], s["y"], s["w"], s["h"])
        if s["type"] == "text":
            e["text"] = {"content": s.get("text"), "font": s.get("font"), "size_pt": s.get("font_size")}
        objs.append(e)
    m = mm.build_model(extraction(d["page_mm"]["w"], d["page_mm"]["h"], objs))
    assert m.orientation == "landscape" and len(m.top_level()) == top + 1
    assert mm.MasterModel.from_dict(json.loads(json.dumps(m.to_dict()))).page == m.page
