"""quit_corel / the instance pool: CorelDRAW only exits after Quit() once the LAST COM reference to it is released (verified live:
with a reference held it never exited and the old code waited the full 15 s timeout and force-killed it on every conversion;
released, it exited 1.3 s after Quit()). The fake below models exactly that - its "process" disappears when the object is
garbage-collected - so these tests run without CorelDRAW."""
from __future__ import annotations

import gc
import time

import pytest

from app import corel_util as cu

RUNNING: set[int] = set()
KILLED: list[int] = []


class FakeCorel:
    def __init__(self, pid):
        self.pid = pid
        self.quit_called = False
        RUNNING.add(pid)

    def Quit(self):
        self.quit_called = True

    def __del__(self):
        if self.quit_called:          # like CorelDRAW: exits after Quit(), once nobody references it any more
            RUNNING.discard(self.pid)


@pytest.fixture(autouse=True)
def fake_processes(monkeypatch, tmp_path):
    RUNNING.clear()
    KILLED.clear()
    monkeypatch.setattr(cu, "PID_FILE", tmp_path / "pids.json")
    monkeypatch.setattr(cu, "corel_pids", lambda: set(RUNNING))
    monkeypatch.setattr(cu, "force_kill", lambda pid: (KILLED.append(pid), RUNNING.discard(pid)))
    monkeypatch.setattr(cu, "QUIT_GRACE_S", 0.5)
    monkeypatch.setattr(cu, "QUIT_CALLER_GRACE_S", 0.1)
    monkeypatch.setattr(cu._Pool, "app", None)
    monkeypatch.setattr(cu._Pool, "pid", None)
    monkeypatch.setattr(cu._Pool, "jobs_run", 0)
    cu._reapers.clear()
    yield
    cu.wait_for_pending_quits(2)


def test_quit_corel_never_blocks_for_the_timeout_even_for_a_temporary_argument():
    # CPython keeps a call's arguments alive on the CALLER's stack for the whole call, so quit_corel can never drop the last
    # reference itself - it waits only the short caller grace, then the reaper sees the exit once the call has returned.
    cu._track_launched(101)
    t = time.time()
    cu.quit_corel(FakeCorel(101), True, 101)
    assert time.time() - t < 0.5            # not the 15 s timeout
    cu.wait_for_pending_quits(2)
    assert 101 not in RUNNING and not KILLED
    assert 101 not in cu._load_tracked_pids()


def test_caller_holding_a_reference_is_not_blocked_and_the_reaper_finishes_once_it_lets_go():
    cu._track_launched(102)
    app = FakeCorel(102)
    t = time.time()
    cu.quit_corel(app, True, 102)
    assert time.time() - t < 0.5            # the short caller grace only, not the full timeout
    assert 102 in RUNNING                   # still referenced by `app` here
    del app
    gc.collect()
    cu.wait_for_pending_quits(2)
    assert 102 not in RUNNING and not KILLED
    assert 102 not in cu._load_tracked_pids()


def test_a_process_that_never_exits_is_force_killed_by_the_reaper(monkeypatch):
    cu._track_launched(103)
    keep = FakeCorel(103)                   # never released -> never exits by itself
    cu.quit_corel(keep, True, 103, timeout=0.3)
    cu.wait_for_pending_quits(2)
    assert KILLED == [103]
    assert 103 not in cu._load_tracked_pids()


def test_quit_pooled_drops_the_pools_reference_first_so_corel_exits_at_once():
    cu._Pool.app, cu._Pool.pid, cu._Pool.jobs_run = FakeCorel(104), 104, 1
    cu._track_launched(104)
    t = time.time()
    cu._quit_pooled()
    assert time.time() - t < 0.3            # exited as soon as the pool let go - no grace wait, no reaper
    assert cu._Pool.app is None and cu._Pool.pid is None
    assert 104 not in RUNNING and not KILLED and not cu._reapers


def test_recycling_the_pool_quits_the_old_instance_without_waiting(monkeypatch):
    cu._Pool.app, cu._Pool.pid, cu._Pool.jobs_run = FakeCorel(105), 105, 1
    monkeypatch.setattr(cu, "recycle_n", lambda: 1)
    monkeypatch.setattr(cu, "dispatch_corel", lambda: (FakeCorel(106), True, 106))
    t = time.time()
    app, launched, pid = cu.acquire_instance()
    assert time.time() - t < 0.3
    assert pid == 106 and 105 not in RUNNING and not KILLED
    del app


def test_not_launched_by_us_is_never_quit():
    app = FakeCorel(107)
    cu.quit_corel(app, False, 107)
    assert not app.quit_called and 107 in RUNNING


def test_orphan_cleanup_spares_the_pooled_instance_in_use_and_drops_dead_pids():
    live_orphan, pooled, dead = FakeCorel(201), FakeCorel(202), 203
    cu._Pool.app, cu._Pool.pid = pooled, 202
    for pid in (201, 202, dead):
        cu._track_launched(pid)
    other = FakeCorel(299)                  # a designer's own CorelDRAW: never tracked
    cu.cleanup_orphaned_instances()
    assert KILLED == [201]                  # only the tracked, running, NOT-in-use one
    assert 202 in RUNNING and 299 in RUNNING
    assert cu._load_tracked_pids() == {202}  # the dead pid 203 is dropped (pid numbers get reused), the orphan too
    del live_orphan, other


def test_orphan_cleanup_with_nothing_running_still_prunes_stale_pids():
    cu._track_launched(301)
    cu.cleanup_orphaned_instances()
    assert cu._load_tracked_pids() == set() and not KILLED


def test_release_quits_an_instance_that_has_done_its_last_job_and_keeps_one_that_has_not(monkeypatch):
    monkeypatch.setattr(cu, "dispatch_corel", lambda: (FakeCorel(401), True, 401))
    monkeypatch.setattr(cu, "recycle_n", lambda: 2)
    app, _, pid = cu.acquire_instance()
    del app
    cu.release_instance(pid, True)
    assert cu._Pool.pid == 401 and 401 in RUNNING          # 1 of 2 jobs: kept for reuse
    app, _, pid = cu.acquire_instance()
    assert pid == 401                                      # reused
    del app
    cu.release_instance(pid, True)
    assert cu._Pool.app is None and 401 not in RUNNING      # 2 of 2: quit at once, not left idle
