"""Login, sessions, roles, CORS and the upload limit (app/auth.py + main.py). Mock engine, throwaway data directory."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

ADMIN = {"email": "boss@example.com", "password": "correct-horse-1"}
DESIGNER = {"email": "dee@example.com", "password": "designer-pass-1"}


@pytest.fixture
def app_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    monkeypatch.setenv("SIGNAGE_AUTH", "on")
    import app.auth as auth_module
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    auth_module._fails.clear()
    return main_module


@pytest.fixture
def anon(app_modules):
    return TestClient(app_modules.app)


def _login(client, creds):
    return client.post("/api/auth/login", json=creds)


@pytest.fixture
def admin(anon):
    assert anon.post("/api/auth/setup", json={**ADMIN, "name": "Boss"}).status_code == 200
    return anon          # the setup call also logged this client in


@pytest.fixture
def designer(admin, app_modules):
    r = admin.post("/api/users", json={**DESIGNER, "name": "Dee", "role": "designer"})
    assert r.status_code == 200
    other = TestClient(app_modules.app)
    assert _login(other, DESIGNER).status_code == 200
    return other


def test_api_needs_a_login(anon):
    assert anon.get("/api/masters").status_code == 401
    assert anon.get("/api/v2/recent").status_code == 401
    assert anon.get("/api/health").status_code == 200   # public
    assert anon.get("/api/corel/health").status_code == 200   # the launch screen checks the engine before sign-in


def test_me_is_public_and_reports_setup(anon):
    body = anon.get("/api/auth/me").json()
    assert body == {"auth_enabled": True, "user": None, "needs_setup": True}


def test_setup_creates_the_first_admin_once(anon):
    r = anon.post("/api/auth/setup", json={**ADMIN, "name": "Boss"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    assert anon.get("/api/masters").status_code == 200   # logged in by the setup
    assert TestClient(anon.app).post("/api/auth/setup", json=DESIGNER).status_code == 403
    assert anon.get("/api/auth/me").json()["needs_setup"] is False


def test_setup_validates_email_and_password(anon):
    assert anon.post("/api/auth/setup", json={"email": "nope", "password": "longenough1"}).status_code == 422
    assert anon.post("/api/auth/setup", json={"email": "a@b.co", "password": "short"}).status_code == 422


def test_login_logout_cycle(admin, app_modules):
    c = TestClient(app_modules.app)
    r = _login(c, ADMIN)
    assert r.status_code == 200
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    assert c.get("/api/masters").status_code == 200
    assert c.post("/api/auth/logout").status_code == 200
    c.cookies.clear()
    assert c.get("/api/masters").status_code == 401


def test_logout_invalidates_the_token_on_the_server(admin, app_modules):
    c = TestClient(app_modules.app)
    _login(c, ADMIN)
    token = c.cookies.get("signage_session")
    c.post("/api/auth/logout")
    stolen = TestClient(app_modules.app, cookies={"signage_session": token})
    assert stolen.get("/api/masters").status_code == 401


def test_wrong_password_and_unknown_email_look_the_same(admin, app_modules):
    c = TestClient(app_modules.app)
    a = _login(c, {"email": ADMIN["email"], "password": "wrong-password"})
    b = _login(c, {"email": "ghost@example.com", "password": "whatever-1"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_repeated_failures_lock_the_login(admin, app_modules):
    c = TestClient(app_modules.app)
    for _ in range(5):
        assert _login(c, {"email": ADMIN["email"], "password": "bad-password"}).status_code == 401
    assert _login(c, ADMIN).status_code == 429          # even the right password waits
    assert _login(TestClient(app_modules.app), {"email": DESIGNER["email"], "password": "x"}).status_code == 401  # other account unaffected


def test_password_is_stored_hashed(admin, app_modules):
    from app import db
    row = db.get_user_with_hash(ADMIN["email"])
    assert row["password_hash"].startswith("scrypt$") and ADMIN["password"] not in row["password_hash"]


def test_designer_cannot_manage_users_or_masters(designer):
    assert designer.get("/api/masters").status_code == 200
    assert designer.get("/api/users").status_code == 403
    assert designer.post("/api/users", json={**ADMIN, "role": "admin"}).status_code == 403
    assert designer.delete("/api/masters/nope").status_code == 403
    assert designer.patch("/api/masters/nope", json={"name": "x"}).status_code == 403
    assert designer.put("/api/masters/nope/file", files={"master": ("a.cdr", b"x")}).status_code == 403
    assert designer.delete("/api/v2/corrections/nope").status_code == 403


def test_admin_reaches_admin_routes(admin):
    assert admin.delete("/api/masters/nope").status_code == 404      # allowed through, then "not found"
    assert admin.delete("/api/v2/corrections/nope").status_code == 404
    assert [u["email"] for u in admin.get("/api/users").json()["users"]] == [ADMIN["email"]]


def test_duplicate_email_and_bad_role(admin):
    assert admin.post("/api/users", json={**ADMIN, "role": "designer"}).status_code == 409
    assert admin.post("/api/users", json={**DESIGNER, "role": "king"}).status_code == 422


def test_disabling_a_user_ends_their_session(admin, designer):
    uid = next(u["id"] for u in admin.get("/api/users").json()["users"] if u["email"] == DESIGNER["email"])
    assert admin.patch(f"/api/users/{uid}", json={"active": False}).status_code == 200
    assert designer.get("/api/masters").status_code == 401
    assert _login(designer, DESIGNER).status_code == 401


def test_password_reset_by_admin(admin, designer, app_modules):
    uid = next(u["id"] for u in admin.get("/api/users").json()["users"] if u["email"] == DESIGNER["email"])
    assert admin.patch(f"/api/users/{uid}", json={"password": "brand-new-pass"}).status_code == 200
    assert designer.get("/api/masters").status_code == 401           # old session gone
    fresh = TestClient(app_modules.app)
    assert _login(fresh, DESIGNER).status_code == 401
    assert _login(fresh, {"email": DESIGNER["email"], "password": "brand-new-pass"}).status_code == 200


def test_the_last_admin_cannot_be_demoted_or_disabled(admin):
    me_id = admin.get("/api/auth/me").json()["user"]["id"]
    assert admin.patch(f"/api/users/{me_id}", json={"role": "designer"}).status_code == 409
    assert admin.patch(f"/api/users/{me_id}", json={"active": False}).status_code == 409


def test_share_links_stay_public(anon):
    assert anon.get("/api/shared/abc/file.zip").status_code == 404   # reaches the handler (no 401)


def test_bootstrap_admin_from_environment(app_modules, monkeypatch):
    from app import auth, db
    monkeypatch.setenv("SIGNAGE_ADMIN_EMAIL", "Root@Example.com")
    monkeypatch.setenv("SIGNAGE_ADMIN_PASSWORD", "bootstrap-pass-1")
    auth.bootstrap_admin()
    assert db.count_users() == 1 and db.get_user_with_hash("root@example.com")["role"] == "admin"
    auth.bootstrap_admin()                                           # does nothing once a user exists
    assert db.count_users() == 1


def test_bootstrap_rejects_a_weak_environment_password(app_modules, monkeypatch):
    from app import auth
    monkeypatch.setenv("SIGNAGE_ADMIN_EMAIL", "root@example.com")
    monkeypatch.setenv("SIGNAGE_ADMIN_PASSWORD", "short")
    with pytest.raises(RuntimeError):
        auth.bootstrap_admin()


def test_auth_off_makes_everyone_admin(app_modules, monkeypatch):
    monkeypatch.setenv("SIGNAGE_AUTH", "off")
    c = TestClient(app_modules.app)
    assert c.get("/api/masters").status_code == 200
    assert c.delete("/api/masters/nope").status_code == 404
    assert c.get("/api/auth/me").json()["auth_enabled"] is False


# ----------------------------------------------------------------------- CORS + upload limit

def test_cors_is_closed_by_default(app_modules):
    assert app_modules._cors_origins() == []


def test_cors_origins_come_from_the_environment(app_modules, monkeypatch):
    monkeypatch.setenv("SIGNAGE_CORS_ORIGINS", "https://a.example.com/, https://b.example.com ,*")
    assert app_modules._cors_origins() == ["https://a.example.com", "https://b.example.com"]


def test_declared_oversize_upload_is_refused(admin, monkeypatch):
    monkeypatch.setenv("SIGNAGE_MAX_UPLOAD_MB", "1")
    big = b"x" * (3 * 1024 * 1024)
    r = admin.post("/api/masters/upload", files={"master": ("m.cdr", big)}, data={"brand": "b", "orientation": "landscape"})
    assert r.status_code == 413


def test_upload_under_the_limit_still_works(admin, monkeypatch):
    monkeypatch.setenv("SIGNAGE_MAX_UPLOAD_MB", "5")
    r = admin.post("/api/masters/upload", files={"master": ("m.cdr", b"x" * 1000)}, data={"brand": "b", "orientation": "landscape"})
    assert r.status_code == 200


def test_save_stream_stops_at_the_limit_and_removes_the_partial_file(app_modules, tmp_path, monkeypatch):
    import io

    from fastapi import HTTPException
    monkeypatch.setenv("SIGNAGE_MAX_UPLOAD_MB", "1")
    dest = tmp_path / "out.bin"
    with pytest.raises(HTTPException) as e:
        app_modules._save_stream(io.BytesIO(b"x" * (2 * 1024 * 1024)), dest)
    assert e.value.status_code == 413 and not dest.exists()
    ok = tmp_path / "ok.bin"
    app_modules._save_stream(io.BytesIO(b"y" * 1000), ok)
    assert ok.read_bytes() == b"y" * 1000
