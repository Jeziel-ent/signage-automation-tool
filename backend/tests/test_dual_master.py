"""Dual-master templates: a landscape and a portrait master per brand, the master picked by the TARGET's
orientation (width / height >= 1.25 = landscape; square, near-square and portrait = portrait), and same-orientation fitting (uniform scale + padding) in
orientation_adapter. Backend API tests use MockEngine and a throwaway SIGNAGE_DATA."""
from __future__ import annotations

import json
import time

import pytest

from app import orientation_adapter as oa
from app import scene_ops
from tests.test_main_v2 import _fake_cdr_bytes, client  # noqa: F401  (fixture re-export)

MM = 25.4


# ----------------------------------------------------------------- pure routing
@pytest.mark.parametrize("w,h,want", [(120 * MM, 48 * MM, "landscape"), (30 * MM, 40 * MM, "portrait"),
                                      (40 * MM, 40 * MM, "portrait"),      # square -> portrait
                                      (100.0, 100.1, "portrait"), (100.1, 100.0, "portrait"),   # near-square -> portrait
                                      (125.0, 100.0, "landscape"), (124.9, 100.0, "portrait"),  # the 1.25 threshold
                                      (11 * 304.8, 6 * 304.8, "landscape"), (5 * 304.8, 5 * 304.8, "portrait"),
                                      (6 * 304.8, 6 * 304.8, "portrait"), (60 * MM, 75 * MM, "portrait"),
                                      (7 * 304.8, 6 * 304.8, "portrait"),  # 1.17 - near-square
                                      (7 * 304.8, 5 * 304.8, "landscape"),  # 1.4
                                      (48 * 25.4, 4 * 304.8, "portrait"),  # 48 in x 4 ft: 1219.1999999999998 vs 1219.2 mm - still square
                                      (4 * 304.8, 48 * 25.4, "portrait")])
def test_target_orientation_rule(w, h, want):
    assert oa.target_orientation(w, h) == want


def test_select_master_picks_the_matching_orientation():
    assert oa.select_master(120 * MM, 48 * MM, "L", "P") == ("landscape", "L", False)
    assert oa.select_master(30 * MM, 40 * MM, "L", "P") == ("portrait", "P", False)


def test_select_master_square_targets_use_the_portrait_master():
    assert oa.select_master(40 * MM, 40 * MM, "L", "P") == ("portrait", "P", False)
    assert oa.select_master(40 * MM, 40 * MM, "L", None) == ("landscape", "L", True)   # no portrait master: fall back, and say so


def test_select_master_falls_back_to_the_other_orientation_and_says_so():
    assert oa.select_master(30 * MM, 40 * MM, "L", None) == ("landscape", "L", True)
    assert oa.select_master(120 * MM, 48 * MM, None, "P") == ("portrait", "P", True)
    with pytest.raises(oa.OpError):
        oa.select_master(1, 2, None, "")


# ----------------------------------------------------------------- API
def _upload(client, orientation=None, brand="dalmia"):
    data = {"brand": brand}
    if orientation:
        data["orientation"] = orientation
    r = client.post("/api/v2/upload", data=data, files={"master": ("m.cdr", _fake_cdr_bytes(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()


def _wait_done(client, shop_id):
    deadline = time.time() + 10
    while time.time() < deadline:
        st = client.get(f"/api/v2/shops/{shop_id}/status").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError("conversion did not finish")


def _report(shop_id):
    import app.db as db
    return json.loads(db.get_shop(shop_id)["report_json"])


def test_upload_records_orientation_and_defaults_to_landscape(client):
    assert _upload(client)["orientation"] == "landscape"
    assert _upload(client, "portrait")["orientation"] == "portrait"
    r = client.post("/api/v2/upload", data={"brand": "d", "orientation": "diagonal"},
                    files={"master": ("m.cdr", _fake_cdr_bytes(), "application/octet-stream")})
    assert r.status_code == 400


def _shop(client, job_id, w, h, unit="in", **extra):
    r = client.post(f"/api/v2/jobs/{job_id}/shops", json={"name": "S", "width": w, "width_unit": unit,
                                                          "height": h, "height_unit": unit, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_landscape_target_converts_from_the_landscape_master_and_portrait_from_the_portrait_one(client):
    land, port = _upload(client, "landscape"), _upload(client, "portrait")
    ids = {"landscape_master_id": land["id"], "portrait_master_id": port["id"]}
    wide = _shop(client, land["id"], 120, 48, **ids)
    tall = _shop(client, land["id"], 30, 40, **ids)
    assert wide["landscape_master_id"] == land["id"] and wide["portrait_master_id"] == port["id"]
    for shop in (wide, tall):
        client.post(f"/api/v2/shops/{shop['id']}/convert")
    assert _wait_done(client, wide["id"])["status"] == "done"
    assert _wait_done(client, tall["id"])["status"] == "done"
    assert _report(wide["id"])["master_used"]["job_id"] == land["id"]
    assert _report(wide["id"])["master_used"]["orientation"] == "landscape"
    assert _report(tall["id"])["master_used"]["job_id"] == port["id"]
    assert _report(tall["id"])["master_used"]["orientation"] == "portrait"


def test_square_targets_convert_from_the_portrait_master_even_in_mixed_units(client):
    land, port = _upload(client, "landscape"), _upload(client, "portrait")
    ids = {"landscape_master_id": land["id"], "portrait_master_id": port["id"]}
    square = _shop(client, land["id"], 40, 40, **ids)
    # 48 in x 4 ft: the worker converts both to mm (1219.1999999999998 vs 1219.2) - still square, still portrait
    mixed = client.post(f"/api/v2/jobs/{land['id']}/shops", json={"name": "M", "width": 48, "width_unit": "in",
                                                                  "height": 4, "height_unit": "ft", **ids}).json()
    for shop in (square, mixed):
        client.post(f"/api/v2/shops/{shop['id']}/convert")
        assert _wait_done(client, shop["id"])["status"] == "done"
        used = _report(shop["id"])["master_used"]
        assert (used["job_id"], used["orientation"], used["fallback"]) == (port["id"], "portrait", False)


def test_selected_master_path_is_the_matching_masters_file(client):
    import app.db as db
    import app.main as main
    land, port = _upload(client, "landscape"), _upload(client, "portrait")
    shop = _shop(client, land["id"], 30, 40, landscape_master_id=land["id"], portrait_master_id=port["id"])
    path, used = main._select_shop_master(db.get_shop(shop["id"]), db.get_job(land["id"]), 30 * MM, 40 * MM)
    assert path == main.Path(db.get_job(port["id"])["master_path"])
    assert path != main.Path(db.get_job(land["id"])["master_path"])
    assert used["orientation"] == "portrait" and used["fallback"] is False


def test_only_one_master_is_used_for_every_size_and_flagged_as_fallback(client):
    land = _upload(client, "landscape")
    tall = _shop(client, land["id"], 30, 40, landscape_master_id=land["id"])
    client.post(f"/api/v2/shops/{tall['id']}/convert")
    assert _wait_done(client, tall["id"])["status"] == "done"
    used = _report(tall["id"])["master_used"]
    assert used["job_id"] == land["id"] and used["fallback"] is True


def test_shop_without_dual_masters_keeps_using_its_own_job_master(client):
    job = _upload(client)
    shop = _shop(client, job["id"], 30, 40)
    client.post(f"/api/v2/shops/{shop['id']}/convert")
    assert _wait_done(client, shop["id"])["status"] == "done"
    assert _report(shop["id"])["master_used"]["job_id"] == job["id"]


def test_shop_rejects_unknown_wrong_orientation_or_other_brand_masters(client):
    land, port = _upload(client, "landscape"), _upload(client, "portrait")
    other = _upload(client, "landscape", brand="agarpathi")
    j = land["id"]
    base = {"name": "S", "width": 1, "height": 1}
    assert client.post(f"/api/v2/jobs/{j}/shops", json={**base, "portrait_master_id": "nope"}).status_code == 404
    # a landscape upload in the portrait slot
    assert client.post(f"/api/v2/jobs/{j}/shops", json={**base, "portrait_master_id": land["id"]}).status_code == 400
    # another brand's master
    assert client.post(f"/api/v2/jobs/{j}/shops", json={**base, "landscape_master_id": other["id"]}).status_code == 400
    assert client.post(f"/api/v2/jobs/{j}/shops", json={**base, "portrait_master_id": port["id"]}).status_code == 200


# ----------------------------------------------------------------- same-orientation fit
def _leaf(i, x, y, w, h, t="curve"):
    return {"id": i, "kind": "shape", "type": t, "name": "", "x": x, "y": y, "w": w, "h": h, "rotation": 0,
            "visible": True, "locked": False}


def _scene(w, h):
    return {"page": {"width": w, "height": h}, "layers": [{"id": "L", "name": "L", "visible": True, "locked": False, "children": [
        _leaf("logo", 0.1 * w, 0.6 * h, 0.3 * w, 0.2 * h), _leaf("prod", 0.5 * w, 0.2 * h, 0.3 * w, 0.4 * h),
        _leaf("txt", 0.1 * w, 0.05 * h, 0.8 * w, 0.06 * h)]}]}


def _box(scene, i):
    return next(n for L in scene["layers"] for n in L["children"] if n["id"] == i)


def test_direction_same_orientation_property():
    assert oa.Direction(1, 2, 3, 4).same_orientation and oa.Direction(2, 1, 4, 3).same_orientation
    assert not oa.Direction(1, 2, 4, 3).same_orientation


@pytest.mark.parametrize("src,dst", [((30 * MM, 40 * MM), (24 * MM, 36 * MM)), ((30 * MM, 40 * MM), (30 * MM, 60 * MM)),
                                     ((120 * MM, 48 * MM), (180 * MM, 48 * MM)), ((120 * MM, 48 * MM), (100 * MM, 60 * MM))])
def test_same_orientation_fit_scales_uniformly_and_pads(src, dst):
    scene = _scene(*src)
    ops = oa.convert_orientation(scene, *dst, same_orientation_fit=True)
    out = scene_ops.apply_ops(scene, ops)
    assert out["page"]["width"] == pytest.approx(dst[0], abs=1e-3)
    k = min(dst[0] / src[0], dst[1] / src[1])
    for i in ("logo", "prod", "txt"):
        b, a = _box(scene, i), _box(out, i)
        assert a["w"] / b["w"] == pytest.approx(k, rel=1e-3) and a["h"] / b["h"] == pytest.approx(k, rel=1e-3)   # uniform
        assert -1e-3 <= a["x"] and a["x"] + a["w"] <= dst[0] + 1e-3
        assert -1e-3 <= a["y"] and a["y"] + a["h"] <= dst[1] + 1e-3
    # relative arrangement is untouched: same offsets between shapes, scaled by k (no unstacking)
    dx_src = _box(scene, "prod")["x"] - _box(scene, "logo")["x"]
    dx_out = _box(out, "prod")["x"] - _box(out, "logo")["x"]
    assert dx_out == pytest.approx(dx_src * k, rel=1e-3)
    # the spare axis is symmetric padding
    xs = [_box(out, i)["x"] for i in ("logo", "prod", "txt")]
    assert min(xs) - (dst[0] - src[0] * k) / 2 == pytest.approx(0.1 * src[0] * k, rel=1e-3)


def test_same_orientation_fit_is_ignored_across_orientations_and_off_by_default():
    scene = _scene(30 * MM, 40 * MM)
    cross = oa.convert_orientation(scene, 120 * MM, 48 * MM, same_orientation_fit=True)
    assert cross == oa.convert_orientation(scene, 120 * MM, 48 * MM)          # falls through to the zone path
    same_default = oa.convert_orientation(scene, 24 * MM, 36 * MM)
    assert same_default != oa.convert_orientation(scene, 24 * MM, 36 * MM, same_orientation_fit=True)


def test_same_orientation_fit_endpoint(client):
    scene = _scene(30 * MM, 40 * MM)
    body = {"scene": scene, "target_w": 24 * MM, "target_h": 36 * MM, "same_orientation_fit": True}
    r = client.post("/api/scene/convert-orientation", json=body)
    assert r.status_code == 200
    k = min(24 / 30, 36 / 40)
    assert _box(r.json()["scene"], "prod")["w"] == pytest.approx(_box(scene, "prod")["w"] * k, rel=1e-3)


def test_a_fallback_master_board_is_flagged_in_its_report():
    import app.main as main
    rep = {"warnings": []}
    main._attach_master_used(rep, {"job_id": "L", "orientation": "landscape", "reason": "...", "fallback": True})
    assert rep["master_used"]["fallback"] and "layout will not fit" in rep["warnings"][0]
    rep = {}
    main._attach_master_used(rep, {"job_id": "P", "orientation": "portrait", "fallback": False})
    assert "warnings" not in rep
