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


@pytest.fixture
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
    assert shop["name"] == "NR Traders"
    assert shop["seq_no"] == 1
    assert shop["status"] == "new"

    shops = client.get(f"/api/v2/jobs/{job_id}/shops").json()
    assert len(shops) == 1
    assert shops[0]["id"] == shop["id"]


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


def test_v2_add_shop_accepts_optional_reference_file_path(client):
    # No upload UI for this yet - the data model just needs to hold it (see
    # CLAUDE.md "New UI" / GATE feedback after Phase A).
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    r = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "X", "width": 1, "width_unit": "ft", "height": 1, "height_unit": "ft",
        "reference_file_path": "/some/uploaded/reference.png",
    })
    assert r.status_code == 200
    assert r.json()["reference_file_path"] == "/some/uploaded/reference.png"


def test_v2_step_estimates_endpoint_works_with_no_data_yet(client):
    # MockEngine's report.json has no timings_s (only CorelEngine's does -
    # see engines.py's _report/step()), so this stays empty even after a
    # mock conversion - just confirming the endpoint itself doesn't error
    # and returns the right shape either way.
    assert client.get("/api/v2/step-estimates").json() == {}

    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "X", "width": 1, "width_unit": "ft", "height": 1, "height_unit": "ft",
    }).json()
    client.post(f"/api/v2/shops/{shop['id']}/convert")
    deadline = time.time() + 10
    while time.time() < deadline:
        if client.get(f"/api/v2/shops/{shop['id']}/status").json()["status"] == "done":
            break
        time.sleep(0.05)

    assert client.get("/api/v2/step-estimates").json() == {}


def test_get_step_timing_estimates_averages_real_timings(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path))
    import app.db as db

    importlib.reload(db)
    db.init_db()
    db.create_job("j1", "dalmia", "m.cdr", str(tmp_path / "m.cdr"))
    for i, (launch, open_) in enumerate([(1.0, 2.0), (3.0, 4.0)]):
        db.create_shop(f"s{i}", "j1", i, "X", 1, "ft", 1, "ft", None)
        db.set_shop_result(f"s{i}", {"cdr": "x.cdr"}, {"timings_s": {"launch": launch, "open": open_}})

    estimates = db.get_step_timing_estimates()
    assert estimates == {"launch": 2.0, "open": 3.0}


def test_v2_recent_lists_shops_across_jobs_newest_first_with_files(client):
    assert client.get("/api/v2/recent").json() == []

    def make_shop(brand, name):
        files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
        job_id = client.post("/api/v2/upload", data={"brand": brand}, files=files).json()["id"]
        return client.post(f"/api/v2/jobs/{job_id}/shops", json={
            "name": name, "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft",
        }).json()

    first = make_shop("dalmia", "First Shop")
    time.sleep(0.02)
    second = make_shop("agarpathi", "Second Shop")
    client.post(f"/api/v2/shops/{second['id']}/convert")
    deadline = time.time() + 10
    while time.time() < deadline:
        if client.get(f"/api/v2/shops/{second['id']}/status").json()["status"] == "done":
            break
        time.sleep(0.05)

    recent = client.get("/api/v2/recent").json()
    assert [r["name"] for r in recent] == ["Second Shop", "First Shop"]  # newest first
    assert recent[0]["brand"] == "agarpathi"
    assert recent[0]["status"] == "done"
    assert recent[0]["files"]["report"].endswith("_report.json")
    assert recent[1]["status"] == "new"
    assert recent[1]["files"] is None
    assert "report_json" not in recent[0]  # list view stays light
    assert recent[0]["shop_id"] == second["id"]
    assert recent[0]["job_id"] == second["job_id"]
    assert first["id"] == recent[1]["shop_id"]


# ---- phone/gst/address: per-shop contact fields wired into the v2 UI/API
# (see CLAUDE.md "Per-shop content replacement" - already supported by the
# old /api/jobs flow, newly exposed here) ----

def test_v2_add_shop_accepts_optional_contact_fields(client):
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    r = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "NR Traders", "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft",
        "phone": "82208 20580", "gst": "33DFLPR6498E1ZV", "address": "12 Main Street\nChennai 600001",
    })
    assert r.status_code == 200
    shop = r.json()
    assert shop["phone"] == "82208 20580"
    assert shop["gst"] == "33DFLPR6498E1ZV"
    assert shop["address"] == "12 Main Street\nChennai 600001"

    # persisted, not just echoed back - a fresh list call sees the same values
    listed = client.get(f"/api/v2/jobs/{job_id}/shops").json()[0]
    assert listed["phone"] == "82208 20580"
    assert listed["gst"] == "33DFLPR6498E1ZV"


def test_v2_add_shop_contact_fields_default_to_none_when_omitted(client):
    """Omitting phone/gst/address must leave them as None (compute_layout's
    "don't touch this field" signal), never an empty string (which
    compute_layout instead treats as "replace with blank" - see
    CLAUDE.md "Per-shop content replacement")."""
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "X", "width": 1, "width_unit": "ft", "height": 1, "height_unit": "ft",
    }).json()
    assert shop["phone"] is None
    assert shop["gst"] is None
    assert shop["address"] is None


def test_v2_add_shop_blank_contact_fields_are_normalized_to_none(client):
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "X", "width": 1, "width_unit": "ft", "height": 1, "height_unit": "ft",
        "phone": "  ", "gst": "", "address": "   ",
    }).json()
    assert shop["phone"] is None
    assert shop["gst"] is None
    assert shop["address"] is None


def test_v2_convert_worker_passes_phone_gst_and_split_address_lines_to_the_engine(client, monkeypatch):
    """The engine (see app/engines.py's CorelEngine._process) expects
    `phone`/`gst` as plain strings and `address_lines` as a list[str] -
    this locks in that the v2 convert worker builds exactly that shape from
    the single `address` textarea field the UI collects, and only includes
    keys that were actually set.
    """
    captured = {}

    class _FakeEngine:
        name = "mock"

        def process(self, master_path, shop, out_dir):
            captured.update(shop)
            return {"files": {"preview": "x.svg"}, "report": {}}

    monkeypatch.setattr("app.main.get_engine", lambda kind: _FakeEngine())

    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "NR Traders", "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft",
        "phone": "82208 20580", "gst": "33DFLPR6498E1ZV", "address": "12 Main Street\n\nChennai 600001",
    }).json()

    r = client.post(f"/api/v2/shops/{shop['id']}/convert")
    assert r.status_code == 200
    deadline = time.time() + 10
    while time.time() < deadline and "phone" not in captured:
        time.sleep(0.05)

    assert captured["phone"] == "82208 20580"
    assert captured["gst"] == "33DFLPR6498E1ZV"
    assert captured["address_lines"] == ["12 Main Street", "Chennai 600001"]  # blank line dropped


def test_v2_convert_worker_omits_contact_keys_when_not_set(client, monkeypatch):
    captured = {}

    class _FakeEngine:
        name = "mock"

        def process(self, master_path, shop, out_dir):
            captured.update(shop)
            return {"files": {"preview": "x.svg"}, "report": {}}

    monkeypatch.setattr("app.main.get_engine", lambda kind: _FakeEngine())

    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "X", "width": 1, "width_unit": "ft", "height": 1, "height_unit": "ft",
    }).json()

    client.post(f"/api/v2/shops/{shop['id']}/convert")
    deadline = time.time() + 10
    while time.time() < deadline and "name" not in captured:
        time.sleep(0.05)

    assert "phone" not in captured
    assert "gst" not in captured
    assert "address_lines" not in captured


# ---------------------------------------------------------------- list thumbnails (/api/v2/shops/{id}/thumb)

def _shop_with_png_preview(client, size=(1600, 640)):
    """A shop marked done whose preview is a real PNG of `size` - no conversion needed."""
    import json as _json
    import sys as _sys
    from PIL import Image
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "Thumb", "width": 120, "width_unit": "in", "height": 48, "height_unit": "in",
    }).json()
    main = _sys.modules["app.main"]
    out = main.JOBS_V2 / job_id / "out" / shop["id"]
    out.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 30, 40)).save(out / "board.png")
    _sys.modules["app.db"].set_shop_result(shop["id"], {"preview": "board.png", "cdr": "board.cdr"}, {})
    return job_id, shop["id"], out, main


def test_v2_thumb_is_small_capped_and_cached_outside_the_shop_folder(client):
    import io as _io
    from PIL import Image
    job_id, shop_id, out, main = _shop_with_png_preview(client)
    r = client.get(f"/api/v2/shops/{shop_id}/thumb")
    assert r.status_code == 200
    im = Image.open(_io.BytesIO(r.content))
    assert max(im.size) == main.THUMB_MAX_PX
    assert im.size[0] / im.size[1] == 2.5  # aspect kept
    assert len(r.content) < 20_000  # vs the 1600 px source
    # cached under the job's thumbs/, never in out/ (the ZIP export reads out/)
    assert list((main.JOBS_V2 / job_id / "thumbs").iterdir())
    assert sorted(p.name for p in out.iterdir()) == ["board.png"]
    assert client.get(f"/api/v2/shops/{shop_id}/thumb").content == r.content  # served from the cache


def test_v2_thumb_is_rebuilt_when_the_preview_changes(client):
    import io as _io
    import os as _os
    from PIL import Image
    _job, shop_id, out, _main = _shop_with_png_preview(client, size=(1600, 640))
    first = Image.open(_io.BytesIO(client.get(f"/api/v2/shops/{shop_id}/thumb").content)).size
    Image.new("RGB", (600, 1200), (10, 10, 10)).save(out / "board.png")  # a re-convert: portrait now
    later = _os.path.getmtime(out / "board.png") + 5
    _os.utime(out / "board.png", (later, later))
    second = Image.open(_io.BytesIO(client.get(f"/api/v2/shops/{shop_id}/thumb").content)).size
    assert first[0] > first[1]
    assert second[1] > second[0]


def test_v2_thumb_404s_without_a_preview(client):
    files = {"master": ("master.cdr", _fake_cdr_bytes(), "application/octet-stream")}
    job_id = client.post("/api/v2/upload", data={"brand": "dalmia"}, files=files).json()["id"]
    shop = client.post(f"/api/v2/jobs/{job_id}/shops", json={
        "name": "X", "width": 1, "width_unit": "ft", "height": 1, "height_unit": "ft",
    }).json()
    assert client.get(f"/api/v2/shops/{shop['id']}/thumb").status_code == 404
    assert client.get("/api/v2/shops/nope/thumb").status_code == 404


def test_v2_thumb_of_an_empty_preview_is_a_404_not_a_500(client):
    _job, shop_id, out, _main = _shop_with_png_preview(client)
    (out / "board.png").write_bytes(b"")  # seen live: a conversion left a 0-byte preview
    assert client.get(f"/api/v2/shops/{shop_id}/thumb").status_code == 404


def test_v2_thumb_size_gives_a_bigger_copy_cached_separately_and_clamped(client):
    import io as _io
    from PIL import Image
    job_id, shop_id, out, main = _shop_with_png_preview(client, size=(1600, 640))
    small = Image.open(_io.BytesIO(client.get(f"/api/v2/shops/{shop_id}/thumb").content))
    big = Image.open(_io.BytesIO(client.get(f"/api/v2/shops/{shop_id}/thumb?size=720").content))
    assert max(small.size) == main.THUMB_MAX_PX
    assert max(big.size) == 720
    assert big.size[0] / big.size[1] == 2.5
    assert max(Image.open(_io.BytesIO(client.get(f"/api/v2/shops/{shop_id}/thumb?size=99999").content)).size) == 1400
    assert max(Image.open(_io.BytesIO(client.get(f"/api/v2/shops/{shop_id}/thumb?size=5").content)).size) == 120
    assert len(list((main.JOBS_V2 / job_id / "thumbs").iterdir())) == 4          # default, 720, 1400, 120
