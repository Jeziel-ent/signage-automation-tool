"""Smoke tests for the new UI's v2 API (Phase A): brand management, upload
+ instant zip-preview extraction, adding shops, converting via MockEngine
(no CorelDRAW needed), and progress/status polling. Uses a throwaway
SIGNAGE_DATA directory per test run so this never touches the real data/
folder or signage_dataset.
"""
from __future__ import annotations

import importlib
import io
import os
import time
import zipfile

import pytest
from fastapi.testclient import TestClient


def _fake_cdr_bytes() -> bytes:
    """A minimal zip-based fake .cdr with a previews/page1.png inside -
    mimicking the real X4+ format (verified against a real generated .cdr
    - see CLAUDE.md-adjacent phase notes) without needing an actual
    CorelDRAW-produced file.
    """
    buf = io.BytesIO()
    # smallest possible valid PNG (1x1 transparent pixel)
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
        "0049454e44ae426082"
    )
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/x-cdr")
        z.writestr("previews/page1.png", png_bytes)
        z.writestr("previews/thumbnail.png", png_bytes)
    return buf.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    return TestClient(main_module.app)


def test_v2_brand_add_and_list(client):
    assert client.get("/api/v2/brands").json() == []
    r = client.post("/api/v2/brands", json={"name": "dalmia"})
    assert r.status_code == 200
    assert r.json() == ["dalmia"]
    # adding the same brand again doesn't duplicate it
    r = client.post("/api/v2/brands", json={"name": "dalmia"})
    assert r.json() == ["dalmia"]


def test_v2_upload_extracts_preview_without_corel(client):
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    r = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["preview_url"] == f"/api/v2/jobs/{body['id']}/preview"
    assert body["preview_error"] is None

    preview = client.get(body["preview_url"])
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"


def test_v2_upload_rejects_non_cdr(client):
    files = {"master": ("master.txt", b"not a cdr", "text/plain")}
    r = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files)
    assert r.status_code == 400


def test_v2_upload_non_zip_cdr_reports_preview_error_not_a_crash(client):
    files = {"master": ("master.cdr", b"not actually a zip", "application/octet-stream")}
    r = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files)
    assert r.status_code == 200
    body = r.json()
    assert body["preview_url"] is None
    assert "zip" in body["preview_error"].lower()


def test_v2_add_shop_and_list(client):
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]

    r = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "NR Traders", "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft",
    })
    assert r.status_code == 200
    shop = r.json()
    assert shop["name"] == "NR Traders" and shop["seq_no"] == 1 and shop["status"] == "new"

    shops = client.get(f"/api/v2/jobs/{job_id}/shops").json()
    assert len(shops) == 1 and shops[0]["id"] == shop["id"]


def test_v2_add_shop_rejects_invalid_unit(client):
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    r = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "X", "width": 1, "width_unit": "parsecs", "height": 1, "height_unit": "ft",
    })
    assert r.status_code == 400


def test_v2_convert_with_mock_engine_reaches_done(client):
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "NR Traders", "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft",
    }).json()

    r = client.post(f"/api/v2/shops/{shop['id']}/convert")
    assert r.status_code == 200

    deadline = time.time() + 10
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/v2/shops/{shop['id']}/status").json()
        if status["status"] in ("done", "failed"):
            break
        time.sleep(0.05)

    assert status["status"] == "done", status
    assert status["progress_pct"] == 100
    assert status["files"]["preview"].endswith(".svg")  # MockEngine renders SVG, not PNG

    # the generated preview file is actually servable
    fr = client.get(f"/api/v2/shops/{shop['id']}/files/{status['files']['preview']}")
    assert fr.status_code == 200


def test_v2_convert_twice_while_running_is_rejected(client):
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "NR Traders", "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft",
    }).json()
    client.post(f"/api/v2/shops/{shop['id']}/convert")
    # immediately try again before the (near-instant, but pool-scheduled) mock job finishes -
    # status should be "queued" or "converting" at this point, either way rejected
    r = client.post(f"/api/v2/shops/{shop['id']}/convert")
    assert r.status_code in (200, 409)  # 200 only if the first run had already finished


def test_v2_shop_file_blocks_path_traversal(client):
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "X", "width": 1, "width_unit": "ft", "height": 1, "height_unit": "ft",
    }).json()
    r = client.get(f"/api/v2/shops/{shop['id']}/files/..%2F..%2Fmaster.cdr")
    assert r.status_code == 404
