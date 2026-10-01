"""The S.No an imported sheet gives a shop ("76", "SL-01") numbers its output files; without one the queue position does."""
from __future__ import annotations

from app import file_naming
from app.asset_zip import member_base
from tests.test_main_v2 import _fake_cdr_bytes, client  # noqa: F401  (fixture re-export)


def _job(client):
    r = client.post("/api/v2/upload", data={"brand": "Adinn"},
                    files={"master": ("m.cdr", _fake_cdr_bytes(), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _add(client, job, **extra):
    r = client.post(f"/api/v2/jobs/{job}/shops", json={"name": "Kalkee", "width": 6, "height": 6, "unit": "ft", **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_numeric_sno_becomes_seq_no_and_names_the_files(client):
    shop = _add(client, _job(client), sno="76")
    assert shop["seq_no"] == 76 and shop["sno_label"] is None
    assert file_naming.shop_basename(shop) == "76 - 6 X 6 Feet - Nonlit - Kalkee"


def test_text_sno_is_kept_as_written(client):
    job = _job(client)
    shop = _add(client, job, sno="SL-01")
    assert shop["sno_label"] == "SL-01" and shop["seq_no"] == 1           # seq_no still orders the job's shops
    assert file_naming.shop_basename(shop) == "SL-01 - 6 X 6 Feet - Nonlit - Kalkee"
    # converting with a numeric S.No later replaces the label
    r = client.post(f"/api/v2/shops/{shop['id']}/convert", json={"sno": 7})
    assert r.status_code == 200, r.text
    import app.db as db
    row = db.get_shop(shop["id"])
    assert row["seq_no"] == 7 and row["sno_label"] is None


def test_no_sno_keeps_the_position(client):
    job = _job(client)
    _add(client, job)
    second = _add(client, job)
    assert second["seq_no"] == 2 and file_naming.shop_basename(second).startswith("2 - ")


def test_overlong_text_sno_is_refused(client):
    r = client.post(f"/api/v2/jobs/{_job(client)}/shops",
                    json={"name": "K", "width": 6, "height": 6, "unit": "ft", "sno": "X" * 21})
    assert r.status_code == 400


def test_zip_and_caption_numbers_accept_text():
    assert member_base("SL-01", "Kalkee Pooja") == "SL-01_Kalkee_Pooja"
    assert member_base(5, "A") == "05_A" and member_base("76", "A") == "76_A"
    assert file_naming.sno_text("076") == "76" and file_naming.sno_text("7", 2) == "07"
