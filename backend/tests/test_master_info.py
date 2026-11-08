"""A master's SHA-256 and the CorelDRAW version that saved it are recorded at upload / replace, and a conversion that no CorelDRAW on the
user's PCs can open is refused up front with a plain message instead of failing inside CorelDRAW."""
from __future__ import annotations

import hashlib
import io
import zipfile

from tests.test_agent_queue import _convert, _pair, world  # noqa: F401  (world is a fixture)


def _cdr(core="2510", extra=b"") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/x-cdr")
        if core:
            z.writestr("META-INF/metadata.xml", f"<meta><CoreVersion>{core}</CoreVersion></meta>")
        if extra:
            z.writestr("extra.bin", extra)
    return buf.getvalue()


def _upload(client, data, filename="m.cdr", orientation="landscape"):
    return client.post("/api/masters/upload", files={"master": (filename, data, "application/octet-stream")},
                       data={"brand": "Hangyo", "orientation": orientation}).json()


def test_upload_records_the_hash_and_the_corel_version(world):
    data = _cdr("2510")
    m = _upload(world.dee, data)
    assert m["master_hash"] == hashlib.sha256(data).hexdigest()
    assert m["core_version"] == 25.1 and m["core_major"] == 25
    assert world.dee.get("/api/masters").json()["masters"][0]["core_major"] == 25


def test_the_same_file_under_another_name_has_the_same_hash(world):
    data = _cdr()
    a, b = _upload(world.dee, data, "one.cdr"), _upload(world.dee, data, "two.cdr")
    assert a["master_hash"] == b["master_hash"] and a["id"] != b["id"]


def test_a_file_with_no_version_stamp_has_no_major(world):
    m = _upload(world.dee, _cdr(core=None))
    assert m["core_version"] is None and m["core_major"] is None and m["master_hash"]


def test_replacing_the_file_updates_both(world):
    m = _upload(world.boss, _cdr("2100"))
    assert m["core_major"] == 21
    new = _cdr("2700", b"more")
    r = world.boss.put(f"/api/masters/{m['id']}/file", files={"master": ("m.cdr", new, "application/octet-stream")})
    assert r.status_code == 200 and r.json()["core_major"] == 27 and r.json()["master_hash"] == hashlib.sha256(new).hexdigest()


def _shop_on(world, master_id, name="NR Traders"):
    job = master_id
    return world.dee.post(f"/api/v2/jobs/{job}/shops", json={"name": name, "width": 12, "width_unit": "ft", "height": 4, "height_unit": "ft"}).json()["id"]


def _agent_with(world, majors):
    code = world.dee.post("/api/agent/pairing-code").json()["code"]
    from fastapi.testclient import TestClient
    r = TestClient(world.main.app).post("/api/agent/register", json={"code": code, "hostname": "PC", "version": "0.2.0",
                                                                      "corel": [{"progid": f"CorelDRAW.Application.{m}", "major": m} for m in majors]})
    assert r.status_code == 200


def test_a_master_no_installed_corel_can_open_is_refused_before_it_is_queued(world):
    _agent_with(world, [21])                                            # CorelDRAW 2019 on the designer's PC
    m = _upload(world.dee, _cdr("2510"))                                # saved by CorelDRAW 2024
    sid = _shop_on(world, m["id"])
    _convert(world.dee, sid)
    st = world.dee.get(f"/api/v2/shops/{sid}/status").json()
    assert st["status"] == "failed" and "saved by CorelDRAW 25" in st["error"] and "newest CorelDRAW on your PC is 21" in st["error"]
    assert world.db.active_agent_job_for_shop(sid) is None              # nothing was queued


def test_a_new_enough_corel_is_queued_as_usual(world):
    _agent_with(world, [21, 27])
    m = _upload(world.dee, _cdr("2510"))
    sid = _shop_on(world, m["id"])
    _convert(world.dee, sid)
    assert world.dee.get(f"/api/v2/shops/{sid}/status").json()["status"] == "queued"


def test_no_opinion_without_agent_information(world):
    m = _upload(world.dee, _cdr("2510"))
    sid = _shop_on(world, m["id"])
    _convert(world.dee, sid)                                            # no agent at all: the job waits for one
    assert world.dee.get(f"/api/v2/shops/{sid}/status").json()["status"] == "queued"
    _agent_with(world, [])                                              # an agent that found no CorelDRAW says nothing about versions
    sid2 = _shop_on(world, m["id"], "Second")
    _convert(world.dee, sid2)
    assert world.dee.get(f"/api/v2/shops/{sid2}/status").json()["status"] == "queued"
