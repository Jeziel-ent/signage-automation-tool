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
