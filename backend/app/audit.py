"""The audit log: who did what, and when, for the actions that matter (sign-ins, accounts, masters, corrections, agents, fonts).

`record(action, target, **detail)` is called from the routes. It never raises (a log problem must not break the action) and never stores
secrets: pass what changed, not passwords or tokens. The signed-in user and the client address come from the request being served (a context
variable set by main's middleware); calls outside a request (start-up) are logged as "system". Kept for SIGNAGE_AUDIT_DAYS (default 365).
"""
from __future__ import annotations

import contextvars
import logging
import os

from . import db

log = logging.getLogger("signage.audit")
current_request: contextvars.ContextVar = contextvars.ContextVar("signage_request", default=None)


def _who(request, user) -> tuple[int | None, str]:
    if user is None and request is not None:
        user = getattr(request.state, "user", None)
    if user:
        return user.get("id"), user.get("email") or ""
    return None, "system" if request is None else "anonymous"


def record(action: str, target: str = "", user: dict | None = None, **detail) -> None:
    try:
        request = current_request.get()
        uid, email = _who(request, user)
        host = request.client.host if request is not None and request.client else ""
        db.add_audit(uid, email, action, target, detail, host)
    except Exception:  # noqa: BLE001 - never let the log break the action it describes
        log.exception("could not write the audit log entry %s", action)


def keep_days() -> int:
    try:
        return int(os.environ.get("SIGNAGE_AUDIT_DAYS", "365"))
    except ValueError:
        return 365
