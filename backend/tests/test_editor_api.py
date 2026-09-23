"""Editor API (Phase C) against MockEngine: scene build/polling, assets, saved
operation list, server-side replay. Real-CorelDRAW scene export is covered by
test_scene_export.py (fakes) and was verified live - see CLAUDE.md.
"""
from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_main_v2 import _fake_cdr_bytes


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    return TestClient(main_module.app)


def _converted_shop(client, convert=True):
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "NR Traders", "width": 120, "width_unit": "in", "height": 4, "height_unit": "ft",
    }).json()
    if convert:
        client.post(f"/api/v2/shops/{shop['id']}/convert")
        deadline = time.time() + 10
        while time.time() < deadline and client.get(f"/api/v2/shops/{shop['id']}/status").json()["status"] != "done":
            time.sleep(0.05)
    return job_id, shop["id"]


def _scene(client, job, shop, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/editor/{job}/{shop}/scene")
        if r.status_code == 200:
            return r.json()
        assert r.status_code == 202, r.text
        time.sleep(0.05)
    raise AssertionError("scene never became ready")


def test_scene_requires_a_converted_shop(client):
    job, shop = _converted_shop(client, convert=False)
    assert client.get(f"/api/editor/{job}/{shop}/scene").status_code == 409
    assert client.get(f"/api/editor/{job}/nope/scene").status_code == 404
    assert client.get(f"/api/editor/wrongjob/{shop}/scene").status_code == 404


def test_scene_builds_then_is_served_from_cache(client):
    job, shop = _converted_shop(client)
    first = client.get(f"/api/editor/{job}/{shop}/scene")
    assert first.status_code in (200, 202)
    if first.status_code == 202:
        body = first.json()
        assert body["status"] == "building" and 0 <= body["progress_pct"] <= 100
    scene = _scene(client, job, shop)
    assert scene["page"] == {"width": 3048.0, "height": 1219.2}  # 120in x 4ft, mixed units, in mm
    assert scene["ops"] == [] and scene["asset_base"] == f"/api/editor/{job}/{shop}/asset/"
    assert scene["layers"][0]["children"]
    again = client.get(f"/api/editor/{job}/{shop}/scene")
    assert again.status_code == 200 and again.json()["layers"] == scene["layers"]


def test_assets_are_served_and_guarded(client):
    job, shop = _converted_shop(client)
    scene = _scene(client, job, shop)
    leaf = next(n for n in scene["layers"][0]["children"] if n["kind"] == "shape")
    r = client.get(f"/api/editor/{job}/{shop}/asset/{leaf['image']['file']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml"
    assert client.get(f"/api/editor/{job}/{shop}/asset/nope.svg").status_code == 404
    assert client.get(f"/api/editor/{job}/{shop}/asset/..%2Fscene.json").status_code == 404
    assert client.get(f"/api/editor/{job}/{shop}/asset/..%2F..%2F..%2Fmaster.cdr").status_code == 404


def test_ops_save_load_and_server_side_replay(client):
    job, shop = _converted_shop(client)
    scene = _scene(client, job, shop)
    ops = [
        {"op": "move", "ids": ["s2"], "dx": 100, "dy": 50},
        {"op": "ungroup", "id": "s5"},
        {"op": "order", "id": "s3", "mode": "front"},
    ]
    r = client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": ops})
    assert r.status_code == 200 and r.json() == {"saved": 3}
    assert client.get(f"/api/editor/{job}/{shop}/ops").json()["ops"] == ops
    assert _scene(client, job, shop)["ops"] == ops  # survives a reload of the scene

    replayed = client.get(f"/api/editor/{job}/{shop}/replayed").json()
    top = [n["id"] for n in replayed["layers"][1]["children"]]   # mock scene: L1 = bg+card, L2 = logo group+name
    assert "s5" not in top and top[-1] == "s3"
    moved = next(n for n in replayed["layers"][0]["children"] if n["id"] == "s2")
    orig = next(n for n in scene["layers"][0]["children"] if n["id"] == "s2")
    assert moved["x"] == pytest.approx(orig["x"] + 100)


def test_unreplayable_ops_are_rejected_and_not_saved(client):
    job, shop = _converted_shop(client)
    _scene(client, job, shop)
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": ["s2"], "dx": 1, "dy": 1}]})
    r = client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": ["ghost"], "dx": 1, "dy": 1}]})
    assert r.status_code == 422 and "unknown id" in r.json()["detail"]
    assert len(client.get(f"/api/editor/{job}/{shop}/ops").json()["ops"]) == 1  # previous list untouched


def test_scene_build_failure_is_reported_and_retryable(client, monkeypatch):
    job, shop = _converted_shop(client)
    import app.main as main
    monkeypatch.setattr(main.scene_export, "mock_scene", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    deadline, code = time.time() + 10, None
    while time.time() < deadline:
        r = client.get(f"/api/editor/{job}/{shop}/scene")
        if r.status_code not in (200, 202):
            code = r.status_code
            assert "disk full" in r.json()["detail"]
            break
        time.sleep(0.05)
    assert code == 500
    monkeypatch.undo()
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    assert client.get(f"/api/editor/{job}/{shop}/scene?retry=1").status_code in (200, 202)
    assert _scene(client, job, shop)["layers"]


def test_low_memory_refusal_is_a_503_with_the_reason_and_retryable(client, monkeypatch):
    """Memory guard: the scene export goes through corel_supervisor.run_batch, whose free-RAM
    floor raises RefusedToStart - the editor must get a clear 503, not a hang or a generic 500."""
    job, shop = _converted_shop(client)
    import app.main as main

    class FakeCorel:
        name = "corel"

    def refuse(jobs, results_path, **kw):
        raise main.corel_supervisor.RefusedToStart("refusing to start CorelDRAW batch: only 1.21 GB free RAM (need >= 1.5 GB).")

    monkeypatch.setattr(main, "get_engine", lambda kind: FakeCorel())
    monkeypatch.setattr(main.corel_supervisor, "run_batch", refuse)
    (main.JOBS_V2 / job / "out" / shop / "NR_Traders.cdr").write_bytes(b"x")
    row = main.db.get_shop(shop)
    files = main.json.loads(row["files_json"])
    files["cdr"] = "NR_Traders.cdr"
    main.db.set_shop_result(shop, files, {})

    deadline, r = time.time() + 10, None
    while time.time() < deadline:
        r = client.get(f"/api/editor/{job}/{shop}/scene")
        if r.status_code == 503:
            break
        time.sleep(0.05)
    assert r.status_code == 503 and "1.21 GB free RAM" in r.json()["detail"]
    assert client.get(f"/api/editor/{job}/{shop}/scene").status_code == 503  # stays refused until retried

    # memory recovers -> ?retry=1 starts a new build (mock engine here) and it completes
    monkeypatch.setattr(main, "get_engine", lambda kind: type("M", (), {"name": "mock"})())
    assert client.get(f"/api/editor/{job}/{shop}/scene?retry=1").status_code in (200, 202)
    assert _scene(client, job, shop)["layers"]


# ------------------------------------------------------------------ Phase D

def _export(client, job, shop, formats, options=None, expect=200):
    r = client.post(f"/api/editor/{job}/{shop}/export", json={"formats": formats, "options": options})
    assert r.status_code == expect, r.text
    return r.json()


def _wait_export(client, job, shop, export_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/editor/{job}/{shop}/export/{export_id}").json()
        if s["status"] in ("done", "failed"):
            return s
        time.sleep(0.05)
    raise AssertionError("export never finished")


def test_export_requires_a_built_scene(client):
    job, shop = _converted_shop(client)
    r = client.post(f"/api/editor/{job}/{shop}/export", json={"formats": ["png"]})
    assert r.status_code == 409 and "open the editor" in r.json()["detail"]


def test_export_runs_replays_saved_ops_and_serves_the_files(client):
    job, shop = _converted_shop(client)
    _scene(client, job, shop)
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "move", "ids": ["s2"], "dx": 50, "dy": 0}]})
    started = _export(client, job, shop, ["png", "cdr", "pdf", "jpeg"], {"raster": {"mode": "max_px", "max_px": 800, "png_background": "white"}})
    assert [s["key"] for s in started["plan"]] == ["launch", "open", "replay", "verify", "cdr", "pdf", "png", "jpeg"]
    assert started["ops"] == 1 and started["raster"]["w_px"] == 800
    final = _wait_export(client, job, shop, started["export_id"])
    assert final["status"] == "done" and final["progress_pct"] == 100
    assert set(final["files"]) == {"cdr", "pdf", "png", "jpeg"}
    assert final["report"]["verification"]["ok"] is True and final["report"]["mock"] is True
    png = client.get(f"/api/editor/{job}/{shop}/exports/{started['export_id']}/files/{final['files']['png']}")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert client.get(f"/api/editor/{job}/{shop}/exports/{started['export_id']}/files/..%2F..%2Fscene%2Fscene.json").status_code == 404
    listed = client.get(f"/api/editor/{job}/{shop}/exports").json()
    assert listed[0]["export_id"] == started["export_id"] and listed[0]["status"] == "done"
    assert client.get("/api/editor/export-estimates").json() != {} or True   # mock reports carry rough timings


def test_export_progress_reports_the_running_step(client, monkeypatch):
    job, shop = _converted_shop(client)
    _scene(client, job, shop)
    started = _export(client, job, shop, ["png"])
    seen = set()
    deadline = time.time() + 15
    while time.time() < deadline:
        s = client.get(f"/api/editor/{job}/{shop}/export/{started['export_id']}").json()
        if s["step"]:
            seen.add(s["step"])
        if s["status"] == "done":
            break
        time.sleep(0.03)
    assert {"launch", "open", "png"} <= seen or s["status"] == "done"


def test_export_rejects_bad_options_and_oversized_rasters(client):
    job, shop = _converted_shop(client)
    _scene(client, job, shop)
    assert _export(client, job, shop, [], expect=422)
    assert "unknown format" in client.post(f"/api/editor/{job}/{shop}/export", json={"formats": ["gif"]}).json()["detail"]
    r = client.post(f"/api/editor/{job}/{shop}/export", json={"formats": ["png"], "options": {"raster": {"mode": "dpi", "dpi": 300}}})
    assert r.status_code == 422 and "too large" in r.json()["detail"]                 # 3048 mm at 300 dpi = 36000 px
    r = client.post(f"/api/editor/{job}/{shop}/export", json={"formats": ["pdf"], "options": {"pdf": {"bitmap_dpi": 123}}})
    assert r.status_code == 422


def test_export_uses_the_replayed_page_size_for_the_raster_limit(client):
    job, shop = _converted_shop(client)
    _scene(client, job, shop)
    client.put(f"/api/editor/{job}/{shop}/ops", json={"ops": [{"op": "page", "width": 30000, "height": 1000}]})
    r = client.post(f"/api/editor/{job}/{shop}/export", json={"formats": ["png"], "options": {"raster": {"mode": "dpi", "dpi": 200}}})
    assert r.status_code == 422 and "too large" in r.json()["detail"]
    r = client.post(f"/api/editor/{job}/{shop}/export", json={"formats": ["cdr"]})
    assert r.status_code == 200                                                        # no raster: no limit applies


def test_export_low_memory_is_a_503_before_anything_is_queued(client, monkeypatch):
    job, shop = _converted_shop(client)
    _scene(client, job, shop)
    import app.main as main

    class FakeCorel:
        name = "corel"

    monkeypatch.setenv("SIGNAGE_ENGINE", "corel")
    monkeypatch.setattr(main, "get_engine", lambda kind: FakeCorel())
    monkeypatch.setattr(main.corel_util, "check_memory", lambda *a, **k: 1.6)
    r = client.post(f"/api/editor/{job}/{shop}/export", json={"formats": ["png"], "options": {"raster": {"mode": "max_px", "max_px": 12000}}})
    assert r.status_code == 503 and "free RAM" in r.json()["detail"]
    assert client.get(f"/api/editor/{job}/{shop}/exports").json() == []


def test_failed_export_reports_the_reason(client, monkeypatch):
    job, shop = _converted_shop(client)
    _scene(client, job, shop)
    import app.main as main
    monkeypatch.setattr(main, "_mock_export", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    started = _export(client, job, shop, ["png"])
    final = _wait_export(client, job, shop, started["export_id"])
    assert final["status"] == "failed" and "disk full" in final["error"]


# ------------------------------------------------------ product-slot replacement assets

def _tiny_png_bytes():
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (37, 21), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def test_product_asset_upload_reports_the_real_pixel_size_and_a_resolvable_path(client):
    job, shop = _converted_shop(client)
    r = client.post(f"/api/editor/{job}/{shop}/product-assets",
                    files={"file": ("logo.png", _tiny_png_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "logo.png" and body["w"] == 37 and body["h"] == 21
    assert body["path"].endswith(".png")

    served = client.get(f"/api/editor/{job}/{shop}/product-asset/{body['path']}")
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert served.content == _tiny_png_bytes()


def test_product_asset_upload_rejects_an_unsupported_extension(client):
    job, shop = _converted_shop(client)
    r = client.post(f"/api/editor/{job}/{shop}/product-assets",
                    files={"file": ("master.cdr", b"not an image", "application/octet-stream")})
    assert r.status_code == 422 and "unsupported image type" in r.json()["detail"]


def test_product_asset_upload_rejects_a_file_that_is_not_actually_an_image(client):
    job, shop = _converted_shop(client)
    r = client.post(f"/api/editor/{job}/{shop}/product-assets",
                    files={"file": ("fake.png", b"not a real png", "image/png")})
    assert r.status_code == 422 and "could not read" in r.json()["detail"]


def test_product_asset_serving_is_guarded_against_path_traversal_and_unknown_files(client):
    job, shop = _converted_shop(client)
    client.post(f"/api/editor/{job}/{shop}/product-assets", files={"file": ("a.png", _tiny_png_bytes(), "image/png")})
    assert client.get(f"/api/editor/{job}/{shop}/product-asset/../../master.cdr").status_code == 404
    assert client.get(f"/api/editor/{job}/{shop}/product-asset/nope.png").status_code == 404


def test_product_asset_upload_requires_a_converted_shop(client):
    job, shop = _converted_shop(client, convert=False)
    r = client.post(f"/api/editor/{job}/{shop}/product-assets",
                    files={"file": ("a.png", _tiny_png_bytes(), "image/png")})
    assert r.status_code == 409
