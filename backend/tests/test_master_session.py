"""Persistent master session (engines.py): the fingerprint comparison that decides whether an undone master can be reused
for the next shop, and when the pool is about to quit the instance a kept document lives on."""
from __future__ import annotations

from app import corel_util, engines
from tests.test_shop_name_binding import _Shape


def test_fingerprint_tolerates_text_remeasure_noise_but_not_changes():
    a = (("page", 3175.0, 1219.2), (0, 6, 100.29, 84.37, 1370.07, 49.37, "SRI KANNIYAMMAN", "AvantGarde-Demi", 181.99))
    noise = (("page", 3175.0, 1219.2), (0, 6, 100.29, 84.37, 1370.06, 49.37, "SRI KANNIYAMMAN", "AvantGarde-Demi", 181.99))
    assert engines.same_fingerprint(a, noise)                      # seen live after Undo(1)
    moved = (a[0], (0, 6, 100.29, 84.37, 1370.07, 52.0, "SRI KANNIYAMMAN", "AvantGarde-Demi", 181.99))
    renamed = (a[0], (0, 6, 100.29, 84.37, 1370.07, 49.37, "ANISH STORES", "AvantGarde-Demi", 181.99))
    page = (("page", 3048.0, 1219.2), a[1])
    assert not engines.same_fingerprint(a, moved)
    assert not engines.same_fingerprint(a, renamed)               # a shop's name must never carry over
    assert not engines.same_fingerprint(a, page)
    assert not engines.same_fingerprint(a, a + ((0, 1, 0.0, 0.0, 1.0, 1.0, None),))   # a leftover tile duplicate


def test_fingerprint_walks_powerclip_contents():
    class _Page:
        def __init__(self, shapes):
            self.SizeWidth, self.SizeHeight = 1000.0, 500.0
            self.Shapes = engines._Coll(shapes)

    class _Doc:
        Unit = 0

        def __init__(self, shapes):
            self.ActivePage = _Page(shapes)

    inner = _Shape(10, 10, 100, 50, text="SRI KANNIYAMMAN")
    clip = _Shape(0, 0, 1000, 500, kids=[inner])
    clip.Fill = None
    fp = engines.doc_fingerprint(_Doc([clip]))
    assert len(fp) == 3
    assert fp[2][0] == 1
    assert "SRI KANNIYAMMAN" in fp[2]  # the nested text is part of it
    inner.Text.Story.Text = "ANISH STORES"
    assert not engines.same_fingerprint(fp, engines.doc_fingerprint(_Doc([clip])))


def test_will_quit_on_release_mirrors_release_instance(monkeypatch):
    monkeypatch.delenv("SIGNAGE_REUSE_COREL", raising=False)
    monkeypatch.setenv("SIGNAGE_COREL_RECYCLE_N", "5")
    monkeypatch.setattr(corel_util._Pool, "pid", 42)
    monkeypatch.setattr(corel_util._Pool, "jobs_run", 2)
    assert not corel_util.will_quit_on_release(42, True)
    assert corel_util.will_quit_on_release(42, False)              # a failed job's instance is quit
    monkeypatch.setattr(corel_util._Pool, "jobs_run", 5)
    assert corel_util.will_quit_on_release(42, True)               # used up
    assert not corel_util.will_quit_on_release(7, False)           # not the pooled instance


def test_keep_master_open_switch(monkeypatch):
    monkeypatch.delenv("SIGNAGE_KEEP_MASTER_OPEN", raising=False)
    assert engines.keep_master_open()
    monkeypatch.setenv("SIGNAGE_KEEP_MASTER_OPEN", "0")
    assert not engines.keep_master_open()


class _Doc:
    def __init__(self):
        self.closed = False
        self.Unit = None

    def Close(self):
        self.closed = True


def test_close_master_session_on_only_closes_that_instance(monkeypatch):
    kept = _Doc()
    monkeypatch.setattr(engines._MasterSession, "doc", kept)
    monkeypatch.setattr(engines._MasterSession, "pid", 111)
    engines.close_master_session_on(222)                       # another instance: left alone
    assert kept.closed is False
    engines.close_master_session_on(111)
    assert kept.closed is True
    assert engines._MasterSession.doc is None


def test_dump_uses_the_shared_pool_and_closes_the_kept_master(monkeypatch):
    """A batch worker holds a pooled CorelDRAW with the engine's master kept open: the dump must use that pool (never quit or
    CoUninitialize it) and close the kept master first - the 2026-10 'batch fails after board 1' bug."""
    from pathlib import Path

    from tools import dump_objects

    events = []
    kept = _Doc()
    monkeypatch.setattr(engines._MasterSession, "doc", kept)
    monkeypatch.setattr(engines._MasterSession, "pid", 777)

    class _Page:
        SizeWidth, SizeHeight = 100.0, 50.0

        class Layers:
            Count = 0

    class _App:
        def OpenDocument(self, path):
            events.append(("open", kept.closed))
            doc = _Doc()
            doc.ActivePage = _Page()
            return doc

    monkeypatch.setattr(corel_util, "ensure_com", lambda: events.append("com"))
    monkeypatch.setattr(corel_util, "acquire_instance", lambda: (_App(), True, 777))
    monkeypatch.setattr(corel_util, "release_instance", lambda pid, ok: events.append(("release", pid, ok)))
    monkeypatch.setattr(corel_util, "run_with_timeout", lambda fn, pid, name: fn())
    out = dump_objects.dump(Path("x.cdr"))
    assert out["shape_count"] == 0
    assert ("open", True) in events                            # the kept master was closed BEFORE the file was opened
    assert events[-1] == ("release", 777, True)                # released to the pool, not quit
