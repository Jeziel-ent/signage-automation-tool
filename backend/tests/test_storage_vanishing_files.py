"""SQLite creates and deletes signage.db-wal / -shm at any moment. A file that disappears between listing and measuring must
count 0, not crash the admin Storage page (a live alert: FileNotFoundError on /data/signage.db-shm in GET /api/admin/storage)."""
from __future__ import annotations

import pathlib

from app import retention


def _vanishing_shm(monkeypatch):
    real_stat = pathlib.Path.stat

    def stat(self, *a, **k):
        if self.name.endswith("-shm"):
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "stat", stat)


def test_storage_report_survives_a_vanishing_shm_file(tmp_path, monkeypatch):
    (tmp_path / "signage.db").write_bytes(b"x" * 100)
    (tmp_path / "signage.db-shm").write_bytes(b"y" * 50)
    _vanishing_shm(monkeypatch)
    out = retention.storage_report(tmp_path)
    assert out["database"] == 100


def test_size_of_a_vanished_file_is_zero(tmp_path, monkeypatch):
    f = tmp_path / "signage.db-shm"
    f.write_bytes(b"y" * 50)
    _vanishing_shm(monkeypatch)
    assert retention._size(f) == 0


def test_storage_browser_size_skips_vanished_files(tmp_path, monkeypatch):
    from app import storage_browser
    (tmp_path / "a.cdr").write_bytes(b"z" * 30)
    (tmp_path / "signage.db-shm").write_bytes(b"y" * 50)
    _vanishing_shm(monkeypatch)
    assert storage_browser._size(tmp_path) == 30
