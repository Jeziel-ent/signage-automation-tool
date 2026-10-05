"""Tests for corel_supervisor's timeout/kill logic, using a fake worker
(tests/fake_hanging_worker.py) instead of real CorelDRAW - these run
anywhere, including CI/non-Windows, since taskkill-based killing is the
only Windows-specific part and these scenarios don't need a real launched
CorelDRAW pid to exercise the supervisor's own polling/timeout behaviour.
"""
import platform

import pytest

from app import corel_supervisor

pytestmark = pytest.mark.skipif(platform.system() != "Windows", reason="corel_supervisor uses taskkill")


def test_hanging_worker_is_killed_after_timeout(tmp_path):
    jobs = [{"master_path": "m.cdr", "shop": {"name": "A", "_test_action": "hang"}, "out_dir": "out"}]
    results = corel_supervisor.run_batch(
        jobs, tmp_path / "results.json", overall_timeout_s=2, worker_module="tests.fake_hanging_worker",
        skip_memory_check=True,
    )
    assert len(results) == 1
    assert results[0]["status"] == "error"
    assert "no progress" in results[0]["error"] or "killed" in results[0]["error"]


def test_completed_jobs_before_a_hang_are_preserved(tmp_path):
    jobs = [
        {"master_path": "m.cdr", "shop": {"name": "A"}, "out_dir": "out1"},
        {"master_path": "m.cdr", "shop": {"name": "B", "_test_action": "hang"}, "out_dir": "out2"},
        {"master_path": "m.cdr", "shop": {"name": "C"}, "out_dir": "out3"},
    ]
    results = corel_supervisor.run_batch(
        jobs, tmp_path / "results.json", overall_timeout_s=2, worker_module="tests.fake_hanging_worker",
        skip_memory_check=True,
    )
    assert len(results) == 3
    assert results[0]["status"] == "done"  # A completed before B hung
    assert results[1]["status"] == "error"  # B is the one that hung
    assert results[2]["status"] == "error"
    assert "not started" in results[2]["error"]  # C never got a chance to run


def test_all_jobs_complete_normally():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        jobs = [
            {"master_path": "m.cdr", "shop": {"name": "A"}, "out_dir": "out1"},
            {"master_path": "m.cdr", "shop": {"name": "B"}, "out_dir": "out2"},
        ]
        results = corel_supervisor.run_batch(
            jobs, Path(d) / "results.json", overall_timeout_s=30, worker_module="tests.fake_hanging_worker",
            skip_memory_check=True,
        )
    assert len(results) == 2
    assert all(r["status"] == "done" for r in results)


def test_on_progress_fires_even_for_jobs_that_finish_instantly(tmp_path):
    # Regression test: on_progress was only checked *inside* the polling loop,
    # right after a `if proc.poll() is not None: break` - a worker that exits
    # between two polls (or before the loop's first full iteration, which a
    # fast, sleep-free fake job can easily do) skipped the final progress
    # check entirely, so callers relying on live per-job callbacks (not just
    # the final return value) silently missed results.
    seen = []
    jobs = [{"master_path": "m.cdr", "shop": {"name": "A"}, "out_dir": "out1"}]
    corel_supervisor.run_batch(
        jobs, tmp_path / "results.json", overall_timeout_s=30, worker_module="tests.fake_hanging_worker",
        skip_memory_check=True, on_progress=lambda idx, entry: seen.append((idx, entry["status"])),
    )
    assert seen == [(0, "done")]


def test_refuses_to_start_below_min_free_ram(tmp_path, monkeypatch):
    monkeypatch.setattr(corel_supervisor.corel_util, "check_memory", lambda: 0.5)
    with pytest.raises(corel_supervisor.RefusedToStart):
        corel_supervisor.run_batch([{"master_path": "m", "shop": {}, "out_dir": "o"}], tmp_path / "r.json")


def _job(name, **kw):
    return {"master_path": "m.cdr", "shop": {"name": name, **kw}, "out_dir": f"out_{name}"}


def test_per_job_limit_kills_a_job_that_keeps_beating_but_never_finishes(tmp_path):
    """A hidden dialog / locked file / hung COM call inside one shop must not hold the queue until the no-progress
    timeout: a job still running after job_timeout_s is killed even while its heartbeat keeps changing."""
    import time
    t0 = time.time()
    results = corel_supervisor.run_batch(
        [_job("A"), _job("B", _test_action="beat"), _job("C")], tmp_path / "results.json",
        overall_timeout_s=60, job_timeout_s=2, worker_module="tests.fake_hanging_worker", skip_memory_check=True)
    assert time.time() - t0 < 20                                   # not the 60 s no-progress limit
    assert results[0]["status"] == "done"
    assert results[1]["status"] == "error"
    assert "timed out after 2s" in results[1]["error"]
    assert "beat" in results[1]["error"]                           # names the step it was on
    assert results[2]["status"] == "error"
    assert "not started" in results[2]["error"]


def test_per_job_limit_does_not_count_launching_corel(tmp_path):
    """The clock starts after "launch" (CorelDRAW start-up has its own Dispatch timeout)."""
    results = corel_supervisor.run_batch(
        [_job("A", _test_launch_s=2.5, _test_seconds=1)], tmp_path / "results.json",
        overall_timeout_s=30, job_timeout_s=2, worker_module="tests.fake_hanging_worker", skip_memory_check=True)
    assert results[0]["status"] == "done"


def test_per_job_limit_restarts_for_each_job(tmp_path):
    """Three 1.2 s jobs under a 2 s limit: each job has its own clock, not one for the batch."""
    results = corel_supervisor.run_batch(
        [_job(n, _test_seconds=1.2) for n in "ABC"], tmp_path / "results.json",
        overall_timeout_s=30, job_timeout_s=2, worker_module="tests.fake_hanging_worker", skip_memory_check=True)
    assert [r["status"] for r in results] == ["done", "done", "done"]


def test_shop_timeout_setting(monkeypatch):
    monkeypatch.delenv("SIGNAGE_SHOP_TIMEOUT_S", raising=False)
    assert corel_supervisor.shop_timeout_s() == 45
    monkeypatch.setenv("SIGNAGE_SHOP_TIMEOUT_S", "90")
    assert corel_supervisor.shop_timeout_s() == 90
    monkeypatch.setenv("SIGNAGE_SHOP_TIMEOUT_S", "junk")
    assert corel_supervisor.shop_timeout_s() == 45
