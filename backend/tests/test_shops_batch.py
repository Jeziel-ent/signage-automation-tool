"""POST /api/v2/jobs/{id}/shops/batch - the Excel/CSV import endpoint."""
from __future__ import annotations

from tests.test_dual_master import _upload
from tests.test_main_v2 import client  # noqa: F401


def _row(name, w=12, h=4, unit="ft", **extra):
    return {"name": name, "width": w, "width_unit": unit, "height": h, "height_unit": unit, **extra}


def test_batch_adds_all_rows_in_order_with_contact_fields(client):
    job = _upload(client)["id"]
    rows = [_row(f"Shop {i}", phone="9876543210" if i == 0 else "", gst="", address="Addr" if i == 1 else "") for i in range(5)]
    r = client.post(f"/api/v2/jobs/{job}/shops/batch", json={"shops": rows})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == []
    assert [s["name"] for s in body["added"]] == [f"Shop {i}" for i in range(5)]
    assert [s["seq_no"] for s in body["added"]] == [1, 2, 3, 4, 5]
    assert body["added"][0]["phone"] == "9876543210"
    assert body["added"][1]["address"] == "Addr"
    assert body["added"][2]["phone"] is None
    assert body["added"][2]["gst"] is None  # "" -> None, never blank text
    assert [s["name"] for s in client.get(f"/api/v2/jobs/{job}/shops").json()] == [f"Shop {i}" for i in range(5)]


def test_batch_reports_bad_rows_and_keeps_the_good_ones(client):
    job = _upload(client)["id"]
    rows = [_row("ok"), {"name": "", "width": 1, "height": 1}, _row("bad unit", unit="yard"), _row("zero", w=0), "junk", _row("ok2")]
    body = client.post(f"/api/v2/jobs/{job}/shops/batch", json={"shops": rows}).json()
    assert [s["name"] for s in body["added"]] == ["ok", "ok2"]
    assert [e["index"] for e in body["errors"]] == [1, 2, 3, 4]
    assert all(e["reason"] for e in body["errors"])


def test_batch_applies_dual_master_ids_and_validates_them(client):
    land, port = _upload(client, "landscape"), _upload(client, "portrait")
    body = client.post(f"/api/v2/jobs/{land['id']}/shops/batch", json={
        "shops": [_row("a"), _row("b")], "landscape_master_id": land["id"], "portrait_master_id": port["id"]}).json()
    assert all(s["landscape_master_id"] == land["id"] and s["portrait_master_id"] == port["id"] for s in body["added"])
    bad = client.post(f"/api/v2/jobs/{land['id']}/shops/batch", json={"shops": [_row("c")], "portrait_master_id": land["id"]})
    assert bad.status_code == 400
    assert len(client.get(f"/api/v2/jobs/{land['id']}/shops").json()) == 2          # nothing added by the rejected batch


def test_batch_rejects_empty_oversized_and_unknown_job(client):
    job = _upload(client)["id"]
    assert client.post(f"/api/v2/jobs/{job}/shops/batch", json={"shops": []}).status_code == 400
    assert client.post(f"/api/v2/jobs/{job}/shops/batch", json={}).status_code == 400
    assert client.post(f"/api/v2/jobs/{job}/shops/batch", json={"shops": [_row("x")] * 501}).status_code == 413
    assert client.post("/api/v2/jobs/nope/shops/batch", json={"shops": [_row("x")]}).status_code == 404


def test_imported_shop_converts_like_a_manual_one(client):
    import time
    job = _upload(client)["id"]
    shop = client.post(f"/api/v2/jobs/{job}/shops/batch", json={"shops": [_row("Imported", 30, 40, "in")]}).json()["added"][0]
    assert client.post(f"/api/v2/shops/{shop['id']}/convert").status_code == 200
    deadline = time.time() + 10
    while time.time() < deadline:
        st = client.get(f"/api/v2/shops/{shop['id']}/status").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert st["status"] == "done"
