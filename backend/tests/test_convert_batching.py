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


@pytest.fixture
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
        # the worker is past shop 0 (its result is being stored): nearly done - it used to fall back to "starting", 5 %
        assert a["step"] == "saving" and a["progress_pct"] == 99
    finally:
        m._batch_slots.clear()
        m._progress_peak.clear()


def test_a_converting_shops_progress_never_goes_backwards(client, fake_corel, tmp_path):
    """A batch member that failed is retried alone: its steps start again, but the % shown does not drop."""
    m = _main()
    job, ids = _setup(client, 1)
    sid = ids[0]
    batch_hb, retry_hb = tmp_path / "batch.heartbeat", tmp_path / "retry.heartbeat"
    m.db.set_shop_status(sid, "converting", step="starting")
    try:
        m._batch_slots[sid] = (batch_hb, 0)
        batch_hb.write_text(json.dumps({"job_index": 0, "step": "pdf"}), encoding="utf-8")
        assert client.get(f"/api/v2/shops/{sid}/status").json()["progress_pct"] == 88
        m._batch_slots[sid] = (retry_hb, 0)                                   # retried on its own: back at "launch"
        retry_hb.write_text(json.dumps({"job_index": 0, "step": "launch"}), encoding="utf-8")
        assert client.get(f"/api/v2/shops/{sid}/status").json()["progress_pct"] == 88
        retry_hb.write_text(json.dumps({"job_index": 0, "step": "png"}), encoding="utf-8")
        assert client.get(f"/api/v2/shops/{sid}/status").json()["progress_pct"] == 97
        m.db.set_shop_status(sid, "queued")                                   # a new conversion starts from 0 again
        assert client.get(f"/api/v2/shops/{sid}/status").json()["progress_pct"] == 0
        assert sid not in m._progress_peak
    finally:
        m._batch_slots.clear()
        m._progress_peak.clear()


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


def test_conversions_run_with_the_per_shop_time_limit(client, fake_corel, monkeypatch):
    m = _main()
    seen = []
    real = m.corel_supervisor.run_batch
    monkeypatch.setattr(m.corel_supervisor, "run_batch", lambda jobs, path, **kw: (seen.append(kw), real(jobs, path, **kw))[1])
    monkeypatch.delenv("SIGNAGE_SHOP_TIMEOUT_S", raising=False)
    job, ids = _setup(client, 1)
    m._v2_convert_worker(ids[0], job)
    assert seen[0]["job_timeout_s"] == 45 and seen[0]["overall_timeout_s"] == 120
    monkeypatch.setenv("SIGNAGE_SHOP_TIMEOUT_S", "0")              # 0 turns the limit off: the supervisor's defaults apply
    assert m._convert_limits() == {}


def test_a_timed_out_later_shop_and_the_ones_it_blocked_are_retried_in_fresh_workers(client, fake_corel):
    """What the supervisor returns after killing a hung shop: that shop "timed out", the rest "not started"."""
    calls, plan = fake_corel
    plan[0] = ["done", "error", "error"]
    job, ids = _setup(client, 3)
    for sid in ids:
        _main()._v2_convert_worker(sid, job)
    assert calls == [["S0", "S1", "S2"], ["S1"], ["S2"]]
    assert [_status(client, s) for s in ids] == ["done"] * 3


def test_startup_fails_work_left_over_by_the_previous_server(client, fake_corel):
    """Queued/converting rows of a server that was restarted can never finish - the page polled them forever."""
    m = _main()
    job, ids = _setup(client, 3)                                    # all queued
    m.db.set_shop_status(ids[0], "converting", step="png")
    m.db.set_shop_status(ids[2], "done")
    (m.CONVERT_RUNS / "stale.heartbeat").write_text("{}", encoding="utf-8")
    m._recover_interrupted_work()
    rows = [client.get(f"/api/v2/shops/{s}/status").json() for s in ids]
    assert [r["status"] for r in rows] == ["failed", "failed", "done"]
    assert "restarted" in rows[0]["error"]
    assert not (m.CONVERT_RUNS / "stale.heartbeat").exists()
    m._convert_queue.clear()


def test_status_payloads_stay_light(client, fake_corel):
    """Profiled at 1000 shops: /shop-statuses sent every done shop's full report (755 KB per 50-shop poll), /status sent
    it twice (raw + parsed), /jobs/{id} sent every report (9 MB). The page reads none of them."""
    m = _main()
    job, ids = _setup(client, 1)
    m._v2_convert_worker(ids[0], job)
    one = client.get(f"/api/v2/shops/{ids[0]}/status").json()
    assert one["report"] is not None and "report_json" not in one
    many = client.get(f"/api/v2/shop-statuses?ids={ids[0]}").json()[ids[0]]
    assert many["status"] == "done" and "report" not in many and "report_json" not in many
    assert all("report_json" not in s for s in client.get(f"/api/v2/jobs/{job}").json()["shops"])


def test_step_estimates_average_timings_and_skip_malformed_reports(client, fake_corel):
    m = _main()
    job, ids = _setup(client, 3)
    with m.db._conn() as conn:
        conn.execute("UPDATE shops SET report_json = ? WHERE id = ?", (json.dumps({"timings_s": {"pdf": 1.0}}), ids[0]))
        conn.execute("UPDATE shops SET report_json = ? WHERE id = ?", (json.dumps({"timings_s": {"pdf": 3.0, "png": 2}}), ids[1]))
        conn.execute("UPDATE shops SET report_json = ? WHERE id = ?", ("{not json", ids[2]))
    assert client.get("/api/v2/step-estimates").json() == {"pdf": 2.0, "png": 2.0}
    m._convert_queue.clear()
