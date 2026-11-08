"""The website must run where there is no Windows: no pywin32, no winreg, no PowerShell (the Docker image / the droplet). A fresh interpreter is
started with every Windows-only module made unimportable, and the app is imported and exercised. Also the database backup tool."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

SCRIPT = textwrap.dedent('''
    import sys
    for name in ("win32api", "win32con", "win32gui", "win32process", "win32com", "win32com.client", "pythoncom", "pywintypes", "winreg"):
        sys.modules[name] = None                     # `import name` now raises ImportError, as on Linux
    import app.main as main
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    r = c.get("/healthz")
    assert r.status_code == 200, r.text
    assert r.json()["engine"] == "remote" and r.json()["mock_engine"] is False, r.json()
    assert c.get("/api/auth/me").json()["needs_setup"] is True
    fonts = c.post("/api/auth/setup", json={"email": "a@b.co", "password": "long-enough-1"})
    assert fonts.status_code == 200
    assert c.get("/api/v2/fonts").json()["available"] is False       # no agent has reported fonts: no PowerShell, no crash
    assert c.get("/api/agent/config").json()["agent_required"] is True
    print("LINUX-OK")
''')


def test_the_server_imports_and_answers_without_any_windows_library(tmp_path):
    env = {**os.environ, "SIGNAGE_DATA": str(tmp_path / "data"), "SIGNAGE_ENGINE": "remote", "SIGNAGE_AUTH": "on",
           "SIGNAGE_AGENT_DIST": str(tmp_path / "nodist"), "PYTHONPATH": str(BACKEND)}
    for k in ("SIGNAGE_PUBLIC_BASE_URL", "SIGNAGE_ADMIN_EMAIL", "SIGNAGE_ADMIN_PASSWORD"):
        env.pop(k, None)
    done = subprocess.run([sys.executable, "-c", SCRIPT], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and "LINUX-OK" in done.stdout, done.stdout + done.stderr


def test_backup_copies_a_live_database_and_keeps_only_the_newest(tmp_path):
    sys.path.insert(0, str(BACKEND))
    from tools import backup_db

    db = tmp_path / "signage.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE t (n INTEGER)")
    conn.execute("INSERT INTO t VALUES (1), (2), (3)")
    conn.commit()                                                   # still open: the backup must work while the site holds the file
    out = tmp_path / "backups"
    first = backup_db.backup(db, out, keep=2, now=1_700_000_000)
    assert sqlite3.connect(first).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    conn.execute("INSERT INTO t VALUES (4)")
    conn.commit()
    backup_db.backup(db, out, keep=2, now=1_700_000_100)
    newest = backup_db.backup(db, out, keep=2, now=1_700_000_200)
    names = sorted(p.name for p in out.glob("signage-*.db"))
    assert len(names) == 2 and first.name not in names             # the oldest is gone
    assert sqlite3.connect(newest).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 4
    assert not list(out.glob("*.part"))
    conn.close()


def test_backup_of_a_missing_database_is_an_error(tmp_path):
    sys.path.insert(0, str(BACKEND))
    from tools import backup_db

    try:
        backup_db.backup(tmp_path / "nope.db", tmp_path / "out")
    except FileNotFoundError as e:
        assert "no database" in str(e)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_a_backup_is_checked_and_can_be_restored(tmp_path):
    sys.path.insert(0, str(BACKEND))
    from tools import backup_db, restore_db

    data = tmp_path / "data"
    data.mkdir()
    db = data / "signage.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE users (n TEXT)")
    conn.executemany("INSERT INTO users VALUES (?)", [("a",), ("b",)])
    conn.commit()
    good = backup_db.backup(db, data / "backups", keep=3)
    assert backup_db.verify(good) == {"tables": {"users": 2}}
    conn.execute("INSERT INTO users VALUES ('c')")                 # the live database moves on ...
    conn.commit()
    conn.close()
    (data / "signage.db-wal").write_bytes(b"stale wal")            # ... and leaves a write-ahead file that must not be replayed
    result = restore_db.restore(good, data, now=1_800_000_000)
    import time
    assert result["tables"] == {"users": 2} and result["kept_old"].endswith("pre-restore-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(1_800_000_000)))
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    assert not (data / "signage.db-wal").exists() and list(data.glob("signage.db-wal.pre-restore-*"))
    assert sqlite3.connect(result["kept_old"]).execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3     # nothing was thrown away


def test_a_damaged_backup_is_refused_and_touches_nothing(tmp_path):
    sys.path.insert(0, str(BACKEND))
    from tools import backup_db, restore_db

    bad = tmp_path / "signage-bad.db"
    bad.write_bytes(b"this is not a database" * 100)
    data = tmp_path / "data"
    data.mkdir()
    (data / "signage.db").write_bytes(b"live")
    try:
        restore_db.restore(bad, data)
    except ValueError:
        pass
    else:
        raise AssertionError("a damaged backup must not be restored")
    assert (data / "signage.db").read_bytes() == b"live" and not list(data.glob("*.pre-restore-*"))
    try:
        backup_db.verify(bad)
    except ValueError as e:
        assert "not a usable database" in str(e) or "integrity" in str(e)
