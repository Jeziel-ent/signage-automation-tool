"""Google Chat reports: backup slots, the 7-day retention, the backup message and the Chat-safe webhook body."""
import os
import time
from datetime import datetime

import pytest

from app import alerts, chat_reports, db


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "signage.db")
    db.init_db()


def test_next_run_follows_the_four_slots():
    z = chat_reports.tz()
    assert chat_reports.next_run(datetime(2026, 11, 13, 5, 59, tzinfo=z)).hour == 6
    assert chat_reports.next_run(datetime(2026, 11, 13, 6, 0, tzinfo=z)).hour == 12
    assert chat_reports.next_run(datetime(2026, 11, 13, 18, 1, tzinfo=z)) == datetime(2026, 11, 14, 0, 0, tzinfo=z)


def test_prune_keeps_seven_days_and_the_newest(tmp_path):
    now = time.time()
    for name, age in (("signage-a.db", 9), ("signage-b.db", 8), ("signage-c.db", 2)):
        p = tmp_path / name
        p.write_bytes(b"x")
        os.utime(p, (now - age * 86400, now - age * 86400))
    assert chat_reports.prune(tmp_path, 7, now) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["signage-c.db"]
    old = tmp_path / "signage-c.db"
    os.utime(old, (now - 30 * 86400,) * 2)
    assert chat_reports.prune(tmp_path, 7, now) == 0                     # the only copy is never deleted


def test_google_chat_gets_text_only():
    url = "https://chat.googleapis.com/v1/spaces/X/messages?key=k&token=t"
    assert alerts.body_for(url, "hi", "t", "d") == {"text": "hi"}
    assert set(alerts.body_for("https://hooks.slack.com/x", "hi", "t", "d")) == {"text", "title", "detail"}
    assert "SIGNAGE SERVER ALERT" in alerts.chat_alert_text("Disk full", "1 GB left")


def test_backup_is_reported(world, monkeypatch, tmp_path):
    tmp_path = tmp_path / "data"
    sent = []
    monkeypatch.setenv("SIGNAGE_ALERT_WEBHOOK", "https://chat.googleapis.com/v1/spaces/X/messages?key=k")
    monkeypatch.setattr(alerts, "_post", lambda url, body: sent.append(body))
    r = chat_reports.run_backup(tmp_path)
    assert r["ok"] and list(sent[0]) == ["text"]
    assert "Database Backup - Signage #1" in sent[0]["text"] and "7-day retention" in sent[0]["text"]
    assert len(list((tmp_path / "backups").glob("signage-*.db"))) == 1


def test_alert_to_google_chat_is_text_only(monkeypatch):
    sent = []
    monkeypatch.setenv("SIGNAGE_ALERT_WEBHOOK", "https://chat.googleapis.com/v1/spaces/X/messages?key=k")
    monkeypatch.setattr(alerts, "_post", lambda url, body: sent.append(body))
    alerts.reset()
    assert alerts.notify("disk", "The server disk is almost full", "1 GB free", wait=True)
    assert list(sent[0]) == ["text"] and "SIGNAGE SERVER ALERT" in sent[0]["text"] and "1 GB free" in sent[0]["text"]
