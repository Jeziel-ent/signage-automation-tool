"""Request rate limits: one noisy browser tab, script or stuck agent cannot starve everybody else.

A sliding one-minute window per caller. The caller is identified WITHOUT looking anything up: a hash of the session cookie, of the agent's
bearer token, or - for anonymous requests - the client address (behind Caddy that is the real address: uvicorn runs with --proxy-headers).
Buckets: `api` (everything under /api), `upload` (bodies that carry files), `agent` (calls with an agent token: heartbeat, job claim, progress),
`pairing` (pairing codes and agent registration). The login form has its own lock-out (app/auth.py). Limits come from the environment so a
busy office can raise them; 0 switches a bucket off. State is in memory: one process, which is how the site runs.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import deque

WINDOW_S = 60.0
DEFAULTS = {"api": 1500, "upload": 90, "agent": 3000, "pairing": 30}
ENV = {"api": "SIGNAGE_RATE_LIMIT_PER_MIN", "upload": "SIGNAGE_RATE_LIMIT_UPLOADS_PER_MIN",
       "agent": "SIGNAGE_RATE_LIMIT_AGENT_PER_MIN", "pairing": "SIGNAGE_RATE_LIMIT_PAIRING_PER_MIN"}
EXEMPT_PATHS = {"/healthz", "/api/health"}

_hits: dict[str, deque] = {}
_lock = threading.Lock()
_calls = 0


def limit_for(bucket: str) -> int:
    try:
        return int(os.environ.get(ENV[bucket], DEFAULTS[bucket]))
    except ValueError:
        return DEFAULTS[bucket]


def caller_key(cookie: str | None, authorization: str | None, client_host: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return "agent:" + hashlib.sha1(authorization[7:].strip().encode()).hexdigest()[:16]
    if cookie:
        return "user:" + hashlib.sha1(cookie.encode()).hexdigest()[:16]
    return "ip:" + (client_host or "?")


def buckets_for(method: str, path: str, caller: str, content_type: str = "") -> list[str]:
    """Which buckets this request counts against (all must have room)."""
    if method == "OPTIONS" or path in EXEMPT_PATHS or not path.startswith("/api/"):
        return []
    out = ["agent" if caller.startswith("agent:") else "api"]
    if method in ("POST", "PUT") and ("multipart/" in content_type or "octet-stream" in content_type):
        out.append("upload")
    if path in ("/api/agent/pairing-code", "/api/agent/register"):
        out.append("pairing")
    return out


def check(key: str, bucket: str, now: float | None = None) -> tuple[bool, int]:
    """Count one request. Returns (allowed, seconds until the oldest request leaves the window when refused)."""
    global _calls
    limit = limit_for(bucket)
    if limit <= 0:
        return True, 0
    now = now if now is not None else time.monotonic()
    slot = f"{bucket}|{key}"
    with _lock:
        _calls += 1
        if _calls % 5000 == 0:                                   # forget callers that have been quiet for a window
            for k in [k for k, q in _hits.items() if not q or now - q[-1] > WINDOW_S]:
                _hits.pop(k, None)
        q = _hits.setdefault(slot, deque())
        while q and now - q[0] > WINDOW_S:
            q.popleft()
        if len(q) >= limit:
            return False, max(1, int(WINDOW_S - (now - q[0])) + 1)
        q.append(now)
    return True, 0


def reset() -> None:
    with _lock:
        _hits.clear()
