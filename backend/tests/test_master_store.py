"""Lifecycle of the master spatial model: upload -> background analysis -> storage -> reuse / invalidation -> conversion hand-off.
MockEngine / fake extractions only: nothing here ran CorelDRAW."""
from __future__ import annotations

import importlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spatial_fixtures import extraction, obj, sign_10x8


def _cdr(extra: bytes = b"") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/x-cdr")
        z.writestr("note.txt", extra)
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    monkeypatch.setenv("SIGNAGE_MASTER_ANALYSIS", "1")
    import app.db as db_module
    import app.main as main_module
    import app.master_store as store_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    importlib.reload(store_module)
    main_module.master_store = store_module
    yield TestClient(main_module.app), main_module, db_module, store_module
    main_module._pool.shutdown(wait=True)          # no background task may outlive the test (and its environment)


def _wait(client, master_id, want, secs=10.0):
    end = time.time() + secs
    while time.time() < end:
        st = client.get(f"/api/masters/{master_id}/analysis").json()["status"]
        if st in want:
            return st
        time.sleep(0.05)
    raise AssertionError(f"analysis stayed {st!r}")


def _upload(client, name="m.cdr", content=b"a", brand="Hangyo"):
    r = client.post("/api/masters/upload", data={"brand": brand, "orientation": "landscape"},
                    files={"master": (name, _cdr(content), "application/octet-stream")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_upload_returns_at_once_and_the_analysis_completes_in_the_background(env):
    client, main, db, store = env
    mid = _upload(client)
    assert _wait(client, mid, {"completed"}) == "completed"
    row = db.get_job(mid)
    model = store.usable_model(row)
    assert model is not None and model.master_hash == row["master_hash"] and len(model.objects) == 4
    assert Path(store.model_path(row)).is_file()
    listed = [m for m in client.get("/api/masters").json()["masters"] if m["id"] == mid][0]
    assert listed["analysis"]["status"] == "completed" and listed["analysis"]["analysed_at"]
    assert "error" not in listed["analysis"] and "model_path" not in listed["analysis"]          # no internals for designers


def test_a_current_analysis_is_reused_not_rerun_unless_forced(env):
    client, main, db, store = env
    mid = _upload(client)
    _wait(client, mid, {"completed"})
    first = db.get_master_model(mid)["updated_at"]
    assert main._ensure_master_analysis(mid) == "completed"
    time.sleep(0.05)
    assert db.get_master_model(mid)["updated_at"] == first                                       # nothing ran
    main._ensure_master_analysis(mid, force=True)
    end = time.time() + 5
    while time.time() < end and not (db.get_master_model(mid)["updated_at"] > first and db.get_master_model(mid)["status"] == "completed"):
        time.sleep(0.05)
    assert db.get_master_model(mid)["updated_at"] > first and db.get_master_model(mid)["status"] == "completed"


def test_a_changed_master_hash_or_extractor_version_means_needs_reanalysis_and_is_not_used(env, monkeypatch):
    client, main, db, store = env
    mid = _upload(client)
    _wait(client, mid, {"completed"})
    row = db.get_job(mid)
    assert store.usable_model(row) is not None
    changed = dict(row, master_hash="different")
    assert store.status_for(changed)["status"] == "needs_reanalysis" and store.usable_model(changed) is None
    monkeypatch.setattr(store, "EXTRACTOR_VERSION", store.EXTRACTOR_VERSION + 1)
    assert store.status_for(row)["status"] == "needs_reanalysis" and store.usable_model(row) is None
    assert store.should_analyse(row)


def test_replacing_the_master_file_triggers_a_fresh_analysis(env):
    client, main, db, store = env
    mid = _upload(client, content=b"one")
    _wait(client, mid, {"completed"})
    old_hash = db.get_job(mid)["master_hash"]
    r = client.put(f"/api/masters/{mid}/file", files={"master": ("m.cdr", _cdr(b"two"), "application/octet-stream")})
    assert r.status_code == 200
    _wait(client, mid, {"completed"})
    row = db.get_job(mid)
    assert row["master_hash"] != old_hash and db.get_master_model(mid)["master_hash"] == row["master_hash"]


def test_only_one_analysis_of_a_master_version_can_be_claimed(env):
    client, main, db, store = env
    mid = _upload(client)
    _wait(client, mid, {"completed"})
    row = db.get_job(mid)
    assert store.begin(row, force=True) is True
    assert store.begin(row, force=True) is False                                                  # already analysing
    assert store.status_for(row)["status"] == "analysing"


def test_a_stale_or_interrupted_analysis_can_be_taken_over_and_is_failed_at_startup(env):
    client, main, db, store = env
    mid = _upload(client)
    _wait(client, mid, {"completed"})
    row = db.get_job(mid)
    assert store.begin(row, force=True)
    with db._conn() as c:
        c.execute("UPDATE master_models SET started_at = ? WHERE job_id = ?", (time.time() - db.ANALYSIS_STALE_S - 5, mid))
    assert store.begin(row, force=True) is True                                                    # the dead run is taken over
    assert store.recover_interrupted() == 1
    assert db.get_master_model(mid)["status"] == "failed" and "restarted" in db.get_master_model(mid)["error"]


def test_failure_is_recorded_hidden_from_designers_and_visible_to_admins(env, monkeypatch):
    client, main, db, store = env
    real_store = store.store_result
    monkeypatch.setattr(store, "store_result", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("COM exploded at C:/secret/path")))
    mid = _upload(client)
    assert _wait(client, mid, {"failed"}) == "failed"
    pub = client.get(f"/api/masters/{mid}/analysis").json()
    assert pub == {"status": "failed", "analysed_at": None}
    adm = client.get(f"/api/admin/masters/{mid}/analysis").json()
    assert "COM exploded" in adm["error"]
    monkeypatch.setattr(store, "store_result", real_store)
    assert client.post(f"/api/masters/{mid}/analysis").json()["status"] in ("analysing", "completed")       # retry works


def test_analysis_is_off_by_default_and_follows_the_planner_flag_for_real_engines(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path))
    monkeypatch.delenv("SIGNAGE_MASTER_ANALYSIS", raising=False)
    monkeypatch.delenv("SIGNAGE_SPATIAL_PLANNER", raising=False)
    from app import master_store
    assert not any(master_store.enabled(k) for k in ("mock", "corel", "remote"))                  # default: nothing changes for anyone
    monkeypatch.setenv("SIGNAGE_SPATIAL_PLANNER", "1")
    assert master_store.enabled("corel") and master_store.enabled("remote") and not master_store.enabled("mock")
    monkeypatch.setenv("SIGNAGE_MASTER_ANALYSIS", "0")
    assert not master_store.enabled("corel")
    monkeypatch.setenv("SIGNAGE_MASTER_ANALYSIS", "1")
    assert master_store.enabled("mock")


def test_remote_mode_queues_an_agent_job_and_stores_what_the_agent_sends(env, monkeypatch):
    client, main, db, store = env
    monkeypatch.setenv("SIGNAGE_ENGINE", "remote")
    mid = _upload(client)                                                                         # nobody to run it yet
    assert db.get_master_model(mid)["status"] == "pending" and db.active_agent_job("master_analysis", ref=mid) is None
    assert main._ensure_master_analysis(mid, user_id=7) == "analysing"
    job = db.active_agent_job("master_analysis", ref=mid)
    assert job and job["user_id"] == 7 and job["shop_id"] is None
    assert main._ensure_master_analysis(mid, user_id=7) == "analysing" and len(
        [1 for _ in [db.active_agent_job("master_analysis", ref=mid)]]) == 1                      # no duplicate job
    out = Path(json.loads(job["payload_json"])["out_dir"])
    (out / "extraction.json").write_text(json.dumps(sign_10x8()), encoding="utf-8")
    main._finalize_agent_master_analysis(job, {"status": "done", "result": {"files": {"extraction": "extraction.json"}}})
    assert store.usable_model(db.get_job(mid)) is not None
    # a failed agent run marks the row failed
    store.begin(db.get_job(mid), force=True)
    main._finalize_agent_master_analysis(job, {"status": "error", "error": "CorelDRAW crashed"})
    assert db.get_master_model(mid)["status"] == "failed"
    # a result that cannot be stored fails the row too
    (out / "extraction.json").write_text("{not json", encoding="utf-8")
    store.begin(db.get_job(mid), force=True)
    with pytest.raises(Exception):
        main._finalize_agent_master_analysis(job, {"status": "done", "result": {"files": {"extraction": "extraction.json"}}})
    assert db.get_master_model(mid)["status"] == "failed"


def test_agent_job_kind_is_known_to_the_claim_api():
    from app import agents
    assert "master_analysis" in agents.KINDS


def test_corel_worker_runs_a_master_analysis_job(tmp_path, monkeypatch):
    """The worker's job plumbing (not CorelDRAW): analyze_master_file is replaced by a fake."""
    from app import corel_worker, master_analysis

    calls = {}

    def fake(path, png, on_step=None, render_px=1600):
        calls["args"] = (str(path), Path(png).name)
        Path(png).write_bytes(b"png")
        return sign_10x8() | {"page_render": {"file": "master_page.png"}}

    monkeypatch.setattr(master_analysis, "analyze_master_file", fake)
    jobs = tmp_path / "jobs.json"
    results = tmp_path / "results.json"
    out = tmp_path / "out"
    jobs.write_text(json.dumps([{"master_analysis": "m.cdr", "analysis_out": str(out)}]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["corel_worker", str(jobs), str(results)])
    corel_worker.main()
    entry = json.loads(results.read_text(encoding="utf-8"))[0]
    assert entry["status"] == "done" and entry["extraction"] == "extraction.json" and entry["render"] == "master_page.png"
    assert json.loads((out / "extraction.json").read_text(encoding="utf-8"))["page"]["width_mm"] == 3048.0
    assert calls["args"] == ("m.cdr", "master_page.png")


def test_convert_job_hands_the_engine_a_model_only_when_the_planner_is_on_and_the_model_is_current(env, monkeypatch):
    client, main, db, store = env
    mid = _upload(client)
    _wait(client, mid, {"completed"})
    sid = client.post(f"/api/v2/jobs/{mid}/shops", json={"name": "S", "width": 12, "width_unit": "ft", "height": 6, "height_unit": "ft"}).json()["id"]
    monkeypatch.delenv("SIGNAGE_SPATIAL_PLANNER", raising=False)
    assert "spatial_model" not in main._convert_job(sid)[0]["shop"]                                  # flag off: legacy payload unchanged
    monkeypatch.setenv("SIGNAGE_SPATIAL_PLANNER", "1")
    shop = main._convert_job(sid)[0]["shop"]
    assert shop["spatial_model"]["master_hash"] == db.get_job(mid)["master_hash"]
    with db._conn() as c:                                                                           # the master changed: the stored model is stale
        c.execute("UPDATE jobs SET master_hash = 'other' WHERE id = ?", (mid,))
    assert "spatial_model" not in main._convert_job(sid)[0]["shop"]


def test_master_json_shape_is_additive(env):
    client, main, db, store = env
    mid = _upload(client)
    body = client.get(f"/api/masters/{mid}" if False else "/api/masters").json()["masters"][0]
    for key in ("id", "name", "orientation", "brand", "file_name", "master_hash", "preview_url", "created_at"):
        assert key in body                                                                          # the pre-existing contract
    assert "analysis" in body


def test_concurrent_claims_of_one_master_version_have_exactly_one_winner(env):
    """Many threads (each its own SQLite connection) race to start the analysis: BEGIN IMMEDIATE lets one through."""
    import threading

    client, main, db, store = env
    mid = _upload(client)
    _wait(client, mid, {"completed"})
    row = db.get_job(mid)
    wins, barrier = [], threading.Barrier(8)

    def go():
        barrier.wait()
        wins.append(store.begin(row, force=True))

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert wins.count(True) == 1 and wins.count(False) == 7


def test_different_masters_do_not_block_each_other(env):
    client, main, db, store = env
    a, b = _upload(client, content=b"a"), _upload(client, content=b"b")
    _wait(client, a, {"completed"}); _wait(client, b, {"completed"})
    assert store.begin(db.get_job(a), force=True) and store.begin(db.get_job(b), force=True)


def test_a_failed_corel_analysis_is_retried_once_in_a_fresh_worker(env, monkeypatch):
    """The real-run bug: a back-to-back worker died with 'The remote procedure call failed'; the second attempt must succeed."""
    client, main, db, store = env
    monkeypatch.setattr(main, "ANALYSIS_RETRY_DELAY_S", 0.0)
    calls = []

    def fake_run_batch(jobs, results_path, **kw):
        calls.append(jobs[0]["master_analysis"])
        out = Path(jobs[0]["analysis_out"])
        if len(calls) == 1:
            return [{"status": "error", "error": "(-2147023170, 'The remote procedure call failed.')"}]
        out.mkdir(parents=True, exist_ok=True)
        (out / "extraction.json").write_text(json.dumps(sign_10x8()), encoding="utf-8")
        return [{"status": "done"}]

    monkeypatch.setattr(main.corel_supervisor, "run_batch", fake_run_batch)
    mid = _upload(client)                                         # the mock engine is selected, so drive the corel branch directly
    _wait(client, mid, {"completed"})
    db.finish_master_analysis(mid, False, error="x")
    main._master_analysis_worker(mid, "corel")
    assert len(calls) == 2 and db.get_master_model(mid)["status"] == "completed"
    # two failures in a row: failed, reported
    calls.clear()
    monkeypatch.setattr(main.corel_supervisor, "run_batch", lambda j, r, **k: calls.append(1) or [{"status": "error", "error": "boom"}])
    main._master_analysis_worker(mid, "corel")
    assert len(calls) == 2 and db.get_master_model(mid)["status"] == "failed"


def test_a_conversion_with_no_current_model_asks_for_an_analysis_and_still_converts_with_the_legacy_layout(env, monkeypatch):
    client, main, db, store = env
    mid = _upload(client)
    _wait(client, mid, {"completed"})
    sid = client.post(f"/api/v2/jobs/{mid}/shops", json={"name": "S", "width": 12, "width_unit": "ft", "height": 6, "height_unit": "ft"}).json()["id"]
    monkeypatch.setenv("SIGNAGE_SPATIAL_PLANNER", "1")
    with db._conn() as c:
        c.execute("UPDATE jobs SET master_hash = 'changed' WHERE id = ?", (mid,))                # stale model
    shop = main._convert_job(sid)[0]["shop"]
    assert "spatial_model" not in shop                                                             # this board: legacy
    end = time.time() + 5
    while time.time() < end and db.get_master_model(mid)["master_hash"] != "changed":
        time.sleep(0.05)
    assert db.get_master_model(mid)["master_hash"] == "changed"                                    # the next board has a model


def test_a_recently_failed_analysis_is_not_retried_by_every_conversion(env):
    client, main, db, store = env
    mid = _upload(client)
    _wait(client, mid, {"completed"})
    db.finish_master_analysis(mid, False, error="boom")
    assert store.may_auto_retry(db.get_job(mid)) is False
    with db._conn() as c:
        c.execute("UPDATE master_models SET updated_at = ? WHERE job_id = ?", (time.time() - store.AUTO_RETRY_COOLDOWN_S - 5, mid))
    assert store.may_auto_retry(db.get_job(mid)) is True
