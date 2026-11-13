"""Telling a person when something is wrong: errors, a full disk, a broken database, boards waiting for an agent that is not there.

SIGNAGE_ALERT_WEBHOOK = a URL that accepts a JSON POST (Slack / Teams / Discord incoming webhooks, or anything of your own). The body has
`text` (what chat tools show) plus `title` and `detail`. Each kind of alert is sent at most once per cooldown (default one hour) and a
"recovered" message follows when the problem is gone. Nothing is sent when the variable is empty - `problems()` still tells the Server page.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import urllib.request
from pathlib import Path

from . import db

log = logging.getLogger("signage.alerts")
_last_sent: dict[str, float] = {}
_active: dict[str, str] = {}              # alert key -> title, for the problems that are on right now (to send "recovered")
_lock = threading.Lock()
WAIT_FOR_AGENT_S = 15 * 60                # a board waiting this long with none of its owner's agents online is reported


def webhook() -> str:
    return os.environ.get("SIGNAGE_ALERT_WEBHOOK", "").strip()


def is_google_chat(url: str) -> bool:
    return "chat.googleapis.com" in url


def body_for(url: str, text: str, title: str = "", detail: str = "") -> dict:
    """Google Chat rejects fields it does not know, so it gets `text` alone; other webhooks also get `title` and `detail`."""
    if is_google_chat(url):
        return {"text": text}
    return {"text": text, "title": title, "detail": detail}


def chat_alert_text(title: str, detail: str, recovered: bool = False) -> str:
    """The alert in the style of the other messages in the space: a bold header, the server, the time, then the problem."""
    import time as _t
    when = _t.strftime("%d-%b-%Y %H:%M", _t.localtime()) + " IST"
    server = os.environ.get("SIGNAGE_SERVER_LABEL", "").strip() or "Signage server"
    head = "✅ *SIGNAGE SERVER RECOVERED*" if recovered else "🔴 *SIGNAGE SERVER ALERT* — CRITICAL"
    mark = "🟡" if recovered else "🔴"
    NL = chr(10)
    text = NL.join([head, f"Server: {server}", f"Detected: {when}", "", f"{mark} *{title}*"])
    if detail:
        text += NL + detail
    if not recovered:
        text += NL * 2 + "Action: manual investigation required." + NL + "No automatic changes were made."
    return text


def _post(url: str, body: dict) -> None:
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "User-Agent": "SignageAlerts"}, method="POST")
        urllib.request.urlopen(req, timeout=10).close()  # noqa: S310 - the URL is the administrator's own setting
    except Exception as e:  # noqa: BLE001 - a dead webhook must never take the site down
        log.warning("alert webhook failed: %s", e)


def notify(key: str, title: str, detail: str = "", cooldown_s: float = 3600.0, wait: bool = False) -> bool:
    """Send one alert unless this `key` was sent within `cooldown_s`. True when it was sent (or queued to send). `wait` = send in this thread
    (tests); normally a background thread so a slow webhook never slows a request."""
    url = webhook()
    if not url:
        return False
    now = time.time()
    with _lock:
        if now - _last_sent.get(key, 0) < cooldown_s:
            return False
        _last_sent[key] = now
    text = (chat_alert_text(title, detail, recovered=key.startswith("recovered:")) if is_google_chat(url)
            else f"[Signage] {title}" + (f"\n{detail}" if detail else ""))
    body = body_for(url, text, title, detail)
    if wait:
        _post(url, body)
    else:
        threading.Thread(target=_post, args=(url, body), name="alert", daemon=True).start()
    return True


def reset() -> None:
    with _lock:
        _last_sent.clear()
        _active.clear()


# ------------------------------------------------------------------ what is wrong right now

def problems(data_dir: Path, now: float | None = None) -> list[dict]:
    """[{key, title, detail}] for every problem on right now: database, disk, boards waiting for an agent."""
    now = now or time.time()
    out: list[dict] = []
    try:
        with db._conn() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as e:  # noqa: BLE001
        out.append({"key": "database", "title": "The database does not answer", "detail": str(e)})
    try:
        free_gb = shutil.disk_usage(data_dir).free / 1024 ** 3
        floor = float(os.environ.get("SIGNAGE_MIN_FREE_DISK_GB", "2"))
        if free_gb < floor:
            out.append({"key": "disk", "title": "The server disk is almost full",
                        "detail": f"{free_gb:.1f} GB free (alert below {floor:g} GB). Run the cleanup on the Server page or enlarge the disk."})
    except OSError as e:
        out.append({"key": "disk", "title": "The data folder cannot be read", "detail": str(e)})
    try:
        out.extend(_waiting_for_agents(now))
    except Exception:  # noqa: BLE001 - a broken check must not hide the others
        log.exception("agent check failed")
    return out


def _waiting_for_agents(now: float) -> list[dict]:
    from . import agents

    with db._conn() as conn:
        rows = conn.execute("SELECT user_id, COUNT(*) AS n, MIN(created_at) AS oldest FROM agent_jobs WHERE state = 'queued'"
                            " GROUP BY user_id").fetchall()
    found = []
    for r in rows:
        if now - r["oldest"] < WAIT_FOR_AGENT_S:
            continue
        online = any(agents._agent_json(a)["online"] for a in db.list_agents(r["user_id"]))
        if not online:
            who = db.get_user(r["user_id"]) if r["user_id"] else None
            found.append({"key": f"waiting:{r['user_id']}", "title": "Boards are waiting for a designer's agent",
                          "detail": f"{r['n']} job(s) queued for {(who or {}).get('email', 'a user')} for over {WAIT_FOR_AGENT_S // 60} minutes and none of their PCs is online."})
    return found


def check_and_notify(data_dir: Path, now: float | None = None, wait: bool = False) -> list[dict]:
    """One round of the watchdog: send an alert for each problem (once per cooldown) and a 'recovered' note for those that cleared."""
    found = problems(data_dir, now)
    keys = {p["key"] for p in found}
    for p in found:
        with _lock:
            _active[p["key"]] = p["title"]
        notify(p["key"], p["title"], p["detail"], wait=wait)
    with _lock:
        cleared = [k for k in _active if k not in keys]
        titles = {k: _active.pop(k) for k in cleared}
    for k, title in titles.items():
        notify(f"recovered:{k}", f"Recovered: {title}", cooldown_s=0, wait=wait)
    return found
