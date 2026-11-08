"""Login, sessions and roles.

* Accounts are email + password (scrypt hashes, stdlib only). Roles: `admin` and `designer`.
* A login sets an HttpOnly, SameSite=Lax cookie holding a random token; the database keeps only its SHA-256.
* `require_user` is attached to the whole FastAPI app, so every route needs a login except `PUBLIC_PATHS` / `PUBLIC_PREFIXES`.
  `require_admin` is added per route on the destructive master / correction / user routes.
* SIGNAGE_AUTH=off switches the check off (local development and the test-suite): everybody is then an admin.
* The first admin comes from SIGNAGE_ADMIN_EMAIL + SIGNAGE_ADMIN_PASSWORD (created at start-up while there are no users), or - only
  from the same computer (loopback) - from `POST /api/auth/setup` while there are no users.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import audit, db

COOKIE = "signage_session"
SESSION_TTL_S = 7 * 24 * 3600
ROLES = ("admin", "designer")
MIN_PASSWORD = 8
PUBLIC_PATHS = {"/api/health", "/healthz", "/api/agent/config", "/api/agent/register", "/api/agent/heartbeat", "/api/agent/fonts", "/api/agent/version", "/api/agent/download", "/api/corel/health", "/api/auth/login", "/api/auth/me", "/api/auth/setup", "/api/auth/logout"}
# /api/corel/health: the launch screen's "Connect CorelDRAW Engine" check runs BEFORE the sign-in (engine version and free memory only)
PUBLIC_PREFIXES = ("/api/shared/", "/api/agent/jobs/")   # the agent job calls carry the agent's own bearer token (app/agents.py)   # unguessable share links meant for people without an account
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SCRYPT = (2 ** 14, 8, 1)

MSG_LOGIN_REQUIRED = "login required"
MSG_ADMIN_REQUIRED = "this needs an administrator"


def enabled() -> bool:
    return os.environ.get("SIGNAGE_AUTH", "on").strip().lower() not in ("off", "0", "false", "no")


# --------------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    n, r, p = _SCRYPT
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password("not-a-real-password")   # compared against when the email is unknown (same timing)


def _check_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HTTPException(422, f"the password must have at least {MIN_PASSWORD} characters")


def _clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "enter a valid email address")
    return email


# --------------------------------------------------------------------- login throttle

_FAIL_LIMIT = 5
_LOCK_S = 300
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _throttle_key(request: Request, email: str) -> str:
    return f"{request.client.host if request.client else '?'}|{email}"


def _locked(key: str) -> bool:
    now = time.time()
    with _fails_lock:
        recent = [t for t in _fails.get(key, []) if now - t < _LOCK_S]
        _fails[key] = recent
        return len(recent) >= _FAIL_LIMIT


def _note_failure(key: str) -> None:
    with _fails_lock:
        _fails.setdefault(key, []).append(time.time())


def _clear_failures(key: str) -> None:
    with _fails_lock:
        _fails.pop(key, None)


# --------------------------------------------------------------------- sessions + dependencies

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _secure_cookie(request: Request) -> bool:
    if os.environ.get("SIGNAGE_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes"):
        return True
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


def _start_session(request: Request, response: Response, user: dict) -> None:
    token = secrets.token_urlsafe(32)
    db.create_session(_token_hash(token), user["id"], SESSION_TTL_S)
    response.set_cookie(COOKIE, token, max_age=SESSION_TTL_S, httponly=True, samesite="lax",
                        secure=_secure_cookie(request), path="/")


_LOCAL_ADMIN = {"id": 0, "email": "local@auth-off", "name": "Local (login off)", "role": "admin", "active": 1}


def current_user(request: Request) -> dict | None:
    if not enabled():
        return _LOCAL_ADMIN
    token = request.cookies.get(COOKIE)
    return db.get_session_user(_token_hash(token)) if token else None


def require_user(request: Request) -> dict | None:
    """App-wide dependency: 401 unless the request carries a valid session (public paths excepted)."""
    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES) or not path.startswith("/api/"):
        return None
    user = current_user(request)
    if user is None:
        raise HTTPException(401, MSG_LOGIN_REQUIRED)
    request.state.user = user
    return user


def require_admin(request: Request) -> dict:
    user = getattr(request.state, "user", None) or current_user(request)
    if user is None:
        raise HTTPException(401, MSG_LOGIN_REQUIRED)
    if user["role"] != "admin":
        raise HTTPException(403, MSG_ADMIN_REQUIRED)
    return user


def bootstrap_admin() -> None:
    """Create the first admin from SIGNAGE_ADMIN_EMAIL / SIGNAGE_ADMIN_PASSWORD when the database has no user yet."""
    email = os.environ.get("SIGNAGE_ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("SIGNAGE_ADMIN_PASSWORD", "")
    if not email or not password or db.count_users():
        return
    if not _EMAIL_RE.match(email) or len(password) < MIN_PASSWORD:
        raise RuntimeError("SIGNAGE_ADMIN_EMAIL must be an email address and SIGNAGE_ADMIN_PASSWORD at least "
                           f"{MIN_PASSWORD} characters")
    db.create_user(email, "Administrator", hash_password(password), "admin")


# --------------------------------------------------------------------- routes

router = APIRouter()


class LoginBody(BaseModel):
    email: str
    password: str


class SetupBody(LoginBody):
    name: str = ""


class UserCreate(LoginBody):
    name: str = ""
    role: str = "designer"


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = None


def _public(user: dict) -> dict:
    return {"id": user["id"], "email": user["email"], "name": user.get("name") or "", "role": user["role"],
            "active": bool(user["active"])}


@router.get("/api/auth/me")
def me(request: Request):
    """{user, auth_enabled, needs_setup}: 200 even when nobody is logged in, so the page can decide what to show."""
    user = current_user(request)
    return {"auth_enabled": enabled(), "user": _public(user) if user else None,
            "needs_setup": enabled() and db.count_users() == 0}


@router.post("/api/auth/login", responses={401: {"description": "Wrong email or password"}, 429: {"description": "Too many attempts"}})
def login(body: LoginBody, request: Request, response: Response):
    email = (body.email or "").strip().lower()
    key = _throttle_key(request, email)
    if _locked(key):
        audit.record("login_locked", email)
        raise HTTPException(429, "too many failed attempts - wait a few minutes and try again")
    row = db.get_user_with_hash(email)
    ok = verify_password(body.password or "", row["password_hash"] if row else _DUMMY_HASH)
    if not row or not ok or not row["active"]:
        _note_failure(key)
        audit.record("login_failed", email)
        raise HTTPException(401, "wrong email or password")
    _clear_failures(key)
    _start_session(request, response, row)
    audit.record("login", email, user=row)
    return {"user": _public(row)}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        who = db.get_session_user(_token_hash(token))
        db.delete_session(_token_hash(token))
        if who:
            audit.record("logout", who["email"], user=who)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/api/auth/setup", responses={403: {"description": "Setup is closed"}, 422: {"description": "Invalid input"}})
def setup(body: SetupBody, request: Request, response: Response):
    """Create the very first administrator. Only while there is no user, and only from this computer."""
    host = request.client.host if request.client else ""
    if db.count_users() or host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403, "setup is closed - an administrator already exists, or you are not on the server itself")
    email = _clean_email(body.email)
    _check_password_strength(body.password)
    user = db.create_user(email, body.name.strip(), hash_password(body.password), "admin")
    _start_session(request, response, user)
    audit.record("setup_admin", email, user=user)
    return {"user": _public(user)}


@router.get("/api/users", dependencies=[Depends(require_admin)])
def list_users():
    return {"users": [_public(u) for u in db.list_users()]}


@router.post("/api/users", dependencies=[Depends(require_admin)],
             responses={409: {"description": "Email already used"}, 422: {"description": "Invalid input"}})
def create_user(body: UserCreate):
    if body.role not in ROLES:
        raise HTTPException(422, "role must be admin or designer")
    email = _clean_email(body.email)
    _check_password_strength(body.password)
    try:
        user = db.create_user(email, body.name.strip(), hash_password(body.password), body.role)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "that email already has an account") from None
    audit.record("user_created", email, role=body.role)
    return {"user": _public(user)}


@router.patch("/api/users/{user_id}", responses={404: {"description": "Not found"}, 409: {"description": "Last administrator"},
                                                 422: {"description": "Invalid input"}})
def update_user(user_id: int, body: UserUpdate, admin: dict = Depends(require_admin)):
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "user not found")
    fields: dict = {}
    if body.name is not None:
        fields["name"] = body.name.strip()
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(422, "role must be admin or designer")
        fields["role"] = body.role
    if body.active is not None:
        fields["active"] = 1 if body.active else 0
    if body.password is not None:
        _check_password_strength(body.password)
        fields["password_hash"] = hash_password(body.password)
    losing_admin = target["role"] == "admin" and target["active"] and (
        fields.get("role", "admin") != "admin" or fields.get("active", 1) == 0)
    if losing_admin and db.count_active_admins() <= 1:
        raise HTTPException(409, "this is the last active administrator - make someone else an administrator first")
    db.update_user(user_id, fields)
    audit.record("user_updated", target["email"], changed=sorted(k if k != "password_hash" else "password" for k in fields),
                 **({"role": fields["role"]} if "role" in fields else {}), **({"active": bool(fields["active"])} if "active" in fields else {}))
    if "password_hash" in fields or fields.get("active") == 0 or "role" in fields:
        db.delete_user_sessions(user_id)   # the new password / role / disabling applies at once
    return {"user": _public(db.get_user(user_id))}
