"""Inline editing of Shops-table rows: PATCH, DELETE, and convert carrying the current on-screen values."""
from __future__ import annotations

import json
import time

from tests.test_dual_master import _upload
from tests.test_main_v2 import client  # noqa: F401


def _import_one(client, job, w=10, h=4):
    r = client.post(f"/api/v2/jobs/{job}/shops/batch", json={"shops": [
        {"name": "Sri Kumar", "width": w, "width_unit": "in", "height": h, "height_unit": "in"}]})
    return r.json()["added"][0]


def _wait(client, shop_id):
    deadline = time.time() + 10
    while time.time() < deadline:
        st = client.get(f"/api/v2/shops/{shop_id}/status").json()
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError("did not finish")


def test_patch_updates_only_the_named_fields_and_clears_blank_contacts(client):
    job = _upload(client)["id"]
    shop = _import_one(client, job)
    client.patch(f"/api/v2/shops/{shop['id']}", json={"phone": "9876543210", "address": "Somewhere"})
    r = client.patch(f"/api/v2/shops/{shop['id']}", json={"width": 12, "width_unit": "ft", "phone": ""})
    assert r.status_code == 200
    row = r.json()
    assert (row["name"], row["width"], row["width_unit"], row["height"], row["height_unit"]) == ("Sri Kumar", 12, "ft", 4, "in")
    assert row["phone"] is None and row["address"] == "Somewhere"


def test_patch_validates_and_rejects_unknown_shop(client):
    job = _upload(client)["id"]
    shop = _import_one(client, job)
    assert client.patch(f"/api/v2/shops/{shop['id']}", json={"width": 0}).status_code == 400
    assert client.patch(f"/api/v2/shops/{shop['id']}", json={"height_unit": "yard"}).status_code == 400
    assert client.patch(f"/api/v2/shops/{shop['id']}", json={"name": "  "}).status_code == 400
    assert client.patch("/api/v2/shops/nope", json={"name": "x"}).status_code == 404
    assert client.get(f"/api/v2/shops/{shop['id']}/status").json()["width"] == 10          # nothing was changed


def test_delete_removes_the_row(client):
    job = _upload(client)["id"]
    a = _import_one(client, job)
    b = _import_one(client, job)
    assert client.delete(f"/api/v2/shops/{a['id']}").status_code == 200
    assert [s["id"] for s in client.get(f"/api/v2/jobs/{job}/shops").json()] == [b["id"]]
    assert client.delete(f"/api/v2/shops/{a['id']}").status_code == 404


def test_convert_uses_the_values_sent_with_it_even_if_no_patch_landed(client):
    """Import '10*4', edit the input to 12*4 - the convert body carries 12 and that is what gets converted."""
    job = _upload(client)["id"]
    shop = _import_one(client, job, 10, 4)
    r = client.post(f"/api/v2/shops/{shop['id']}/convert", json={
        "name": "Sri Kumar", "width": 12, "width_unit": "in", "height": 4, "height_unit": "in",
        "phone": "", "gst": "", "address": ""})
    assert r.status_code == 200
    st = _wait(client, shop["id"])
    assert st["status"] == "done" and st["width"] == 12
    rep = st["report"]
    assert abs(rep["page"]["width"] - 12 * 25.4) < 0.01 if "page" in rep else True      # mock report carries the page size
    assert st["phone"] is None                                                             # blank stays "leave the master's text"


def test_convert_with_an_invalid_edit_is_rejected_and_nothing_starts(client):
    job = _upload(client)["id"]
    shop = _import_one(client, job)
    r = client.post(f"/api/v2/shops/{shop['id']}/convert", json={"width": ""})
    assert r.status_code == 400
    assert client.get(f"/api/v2/shops/{shop['id']}/status").json()["status"] == "new"


def test_a_converting_shop_cannot_be_edited_or_deleted(client):
    import app.db as db
    job = _upload(client)["id"]
    shop = _import_one(client, job)
    db.set_shop_status(shop["id"], "converting", step="open")
    assert client.patch(f"/api/v2/shops/{shop['id']}", json={"name": "x"}).status_code == 409
    assert client.delete(f"/api/v2/shops/{shop['id']}").status_code == 409


def test_one_shared_unit_sets_both_dimensions_on_add_patch_and_convert(client):
    job = _upload(client)["id"]
    r = client.post(f"/api/v2/jobs/{job}/shops", json={"name": "S", "width": 10, "height": 4, "unit": "ft"})
    assert r.status_code == 200
    shop = r.json()
    assert (shop["width_unit"], shop["height_unit"]) == ("ft", "ft")
    row = client.patch(f"/api/v2/shops/{shop['id']}", json={"unit": "in"}).json()
    assert (row["width_unit"], row["height_unit"]) == ("in", "in")
    assert client.patch(f"/api/v2/shops/{shop['id']}", json={"unit": "yard"}).status_code == 400
    # a body of only name/width/height/unit (what the table sends) converts fine and keeps contact fields untouched
    r = client.post(f"/api/v2/shops/{shop['id']}/convert", json={"name": "S", "width": 12, "height": 4, "unit": "ft"})
    assert r.status_code == 200 and _wait(client, shop["id"])["status"] == "done"
    done = client.get(f"/api/v2/shops/{shop['id']}/status").json()
    assert (done["width"], done["width_unit"], done["height_unit"], done["phone"]) == (12, "ft", "ft", None)
