"""v2 conversions batched into one corel_worker session (main._v2_convert_worker). CorelDRAW is replaced by a fake
corel_supervisor.run_batch, so this runs anywhere; the real speed-up was measured live (see CLAUDE.md "Conversion speed")."""
from __future__ import annotations

import json
import sys

import pytest

from tests.test_main_v2 import _fake_cdr_bytes, client  # noqa: F401  (fixture re-export)


class FakeCorelEngine:
    name = "corel"


def _main():
    return sys.modules["app.main"]


def _setup(client, n):
    r = client.post("/api/v2/upload", data={"brand": "dalmia"}, files={"master": ("m.cdr", _fake_cdr_bytes(), "application/octet-stream")})
    job = r.json()["id"]
    ids = [client.post(f"/api/v2/jobs/{job}/shops", json={"name": f"S{i}", "width": 120, "height": 48, "unit": "in"}).json()["id"]
           for i in range(n)]
    m = _main()
    for sid in ids:  # queued exactly as the endpoint does, without letting the pool run them yet
        m.db.set_shop_status(sid, "queued")
        m._convert_queue.append(sid)
    return job, ids


def _ok(job):
    return {"status": "done", "result": {"files": {"cdr": "x.cdr"}, "report": {"timings_s": {}}}, "shop": job["shop"]}


@pytest.fixture()
def fake_corel(client, monkeypatch):  # noqa: F811
    m = _main()
    monkeypatch.setattr(m, "get_engine", lambda kind: FakeCorelEngine())
    m._convert_queue.clear()
    calls = []
    plan = {}  # call number -> list of statuses (or an exception)

    def run_batch(jobs, results_path, on_progress=None, **kw):
        calls.append([j["shop"]["name"] for j in jobs])
        spec = plan.get(len(calls) - 1)
        if isinstance(spec, Exception):
            raise spec
        results = []
        for i, j in enumerate(jobs):
            status = spec[i] if spec else "done"
            e = _ok(j) if status == "done" else {"status": "error", "error": "Object is not connected to server", "shop": j["shop"]}
            results.append(e)
            if on_progress:
                on_progress(i, e)
        return results

    monkeypatch.setattr(m.corel_supervisor, "run_batch", run_batch)
    return calls, plan


def _status(client, sid):
    return client.get(f"/api/v2/shops/{sid}/status").json()["status"]


def test_queued_shops_are_converted_in_one_batch_and_the_later_pool_tasks_are_no_ops(client, fake_corel):
    calls, _ = fake_corel
    job, ids = _setup(client, 3)
    m = _main()
    for sid in ids:                          # the pool runs one task per shop, in order
        m._v2_convert_worker(sid, job)
    assert calls == [["S0", "S1", "S2"]]     # ONE worker session for all three
    assert [_status(client, s) for s in ids] == ["done"] * 3
    assert not m._convert_queue and not m._batch_slots


def test_batches_are_capped(client, fake_corel, monkeypatch):
    calls, _ = fake_corel
    monkeypatch.setattr(_main(), "CONVERT_BATCH_MAX", 2)
    job, ids = _setup(client, 5)
    for sid in ids:
        _main()._v2_convert_worker(sid, job)
    assert calls == [["S0", "S1"], ["S2", "S3"], ["S4"]]
    assert all(_status(client, s) == "done" for s in ids)


def test_a_later_shop_that_failed_in_the_batch_is_retried_alone_and_the_first_is_not(client, fake_corel):
    calls, plan = fake_corel
    plan[0] = ["error", "done", "error"]     # first shop fails on a fresh instance (real failure); third fails on the reused one
    job, ids = _setup(client, 3)
    for sid in ids:
        _main()._v2_convert_worker(sid, job)
    assert calls == [["S0", "S1", "S2"], ["S2"]]  # only S2 retried, on its own
    assert [_status(client, s) for s in ids] == ["failed", "done", "done"]


def test_refused_to_start_fails_every_shop_in_the_batch_with_the_reason(client, fake_corel):
    _, plan = fake_corel
    m = _main()
    plan[0] = m.corel_supervisor.RefusedToStart("only 1.20 GB free RAM")
    job, ids = _setup(client, 2)
    m._v2_convert_worker(ids[0], job)
    # the retry of S1 runs as call 1 (not planned -> done): a RAM refusal for the batch is not the reused instance's fault,
    # but retrying alone is harmless and gives it its own chance once memory frees up
    st = client.get(f"/api/v2/shops/{ids[0]}/status").json()
    assert st["status"] == "failed" and "free RAM" in st["error"]


def test_status_reads_only_this_shops_beat_from_the_shared_batch_heartbeat(client, fake_corel, tmp_path):
    m = _main()
    job, ids = _setup(client, 2)
    hb = tmp_path / "batch.heartbeat"
    m._batch_slots[ids[0]] = (hb, 0)
    m._batch_slots[ids[1]] = (hb, 1)
    for sid in ids:
        m.db.set_shop_status(sid, "converting", step="starting")
    hb.write_text(json.dumps({"job_index": 1, "step": "pdf"}), encoding="utf-8")
    try:
        a, b = (client.get(f"/api/v2/shops/{s}/status").json() for s in ids)
        assert b["step"] == "pdf" and b["progress_pct"] == m._STEP_PERCENT["pdf"]
        assert a["step"] == "starting"          # shop 0's own beat is not the one in the file
    finally:
        m._batch_slots.clear()


def test_mock_engine_still_converts_one_shop_per_task(client):  # noqa: F811
    m = _main()
    m._convert_queue.clear()
    job, ids = _setup(client, 2)
    m._v2_convert_worker(ids[0], job)
    assert _status(client, ids[0]) == "done" and _status(client, ids[1]) == "queued"
    m._v2_convert_worker(ids[1], job)
    assert _status(client, ids[1]) == "done"


def test_shop_statuses_returns_many_shops_in_one_call_and_skips_unknown_ids(client):  # noqa: F811
    job, ids = _setup(client, 2)
    _main()._convert_queue.clear()
    r = client.get("/api/v2/shop-statuses", params={"ids": ",".join(ids + ["nope"])})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == set(ids) and all(body[s]["status"] == "queued" for s in ids)
    assert client.get("/api/v2/shop-statuses").json() == {}
