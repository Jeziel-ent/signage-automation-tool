"""Regression: a portrait master uploaded AFTER a shop was added used to be ignored (the shop row kept
portrait_master_id = NULL, so the landscape master won by fallback). The convert call now carries the current ids."""
from __future__ import annotations

import logging

from tests.test_dual_master import MM, _report, _shop, _upload, _wait_done  # noqa: F401
from tests.test_main_v2 import client  # noqa: F401


def test_portrait_master_uploaded_after_the_shop_was_added_is_used_when_convert_sends_the_ids(client, caplog):
    land = _upload(client, "landscape")
    shop = _shop(client, land["id"], 30, 40, landscape_master_id=land["id"])        # portrait not uploaded yet
    assert shop["portrait_master_id"] is None
    port = _upload(client, "portrait")

    with caplog.at_level(logging.INFO, logger="signage.convert"):
        r = client.post(f"/api/v2/shops/{shop['id']}/convert",
                        json={"landscape_master_id": land["id"], "portrait_master_id": port["id"]})
        assert r.status_code == 200
        assert _wait_done(client, shop["id"])["status"] == "done"

    used = _report(shop["id"])["master_used"]
    assert used["job_id"] == port["id"] and used["orientation"] == "portrait" and used["fallback"] is False
    line = next(m for m in caplog.messages if "Selected master file path" in m)
    assert shop["id"] in line and "portrait" in line and port["id"] in line and land["id"] not in line.split("->")[1]


def test_convert_without_a_body_keeps_the_stored_master_ids(client):
    land, port = _upload(client, "landscape"), _upload(client, "portrait")
    shop = _shop(client, land["id"], 30, 40, landscape_master_id=land["id"], portrait_master_id=port["id"])
    assert client.post(f"/api/v2/shops/{shop['id']}/convert").status_code == 200
    assert _wait_done(client, shop["id"])["status"] == "done"
    assert _report(shop["id"])["master_used"]["job_id"] == port["id"]


def test_convert_rejects_a_wrong_orientation_master_id(client):
    land = _upload(client, "landscape")
    shop = _shop(client, land["id"], 30, 40)
    r = client.post(f"/api/v2/shops/{shop['id']}/convert", json={"portrait_master_id": land["id"]})
    assert r.status_code == 400
