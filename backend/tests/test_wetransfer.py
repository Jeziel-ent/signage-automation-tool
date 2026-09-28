"""The WeTransfer link: POST/GET /api/export-wetransfer and wetransfer_uploader's pure helpers.

wetransfer.com itself is never contacted here - the uploader is replaced by a fake. Whether the real website flow works
can only be checked by running it by hand (see wetransfer_uploader.py's docstring)."""
from __future__ import annotations

import sys
import threading
import time
import types

import pytest

from app import main
from app import wetransfer_uploader as wt
from tests.test_dual_master import _upload
from tests.test_main_v2 import client  # noqa: F401
from tests.test_print_sheet import _done_shop

EMAIL = "designer@example.com"


def test_extract_link_and_limits(monkeypatch):
    assert wt.extract_link("Your link: https://we.tl/t-AbC123xyz is ready") == "https://we.tl/t-AbC123xyz"
    assert wt.extract_link(None, "", "copy https://we.tl/t-9Z") == "https://we.tl/t-9Z"
    assert wt.extract_link("https://wetransfer.com/downloads/abc") is None
    monkeypatch.delenv("SIGNAGE_WETRANSFER_MAX_GB", raising=False)
    assert wt.max_bytes() == 3_000_000_000
    monkeypatch.setenv("SIGNAGE_WETRANSFER_MAX_GB", "0.5")
    assert wt.max_bytes() == 500_000_000
    assert wt.upload_timeout_s(50_000_000) == 280.0
    # an e-mailed-code screen is no longer a dead end: the person types the code (CODE_RE), only account/plan walls block
    assert wt.CODE_RE.search("Please verify your email to continue") and not wt.BLOCKER_RE.search("Please verify your email")
    assert wt.CODE_RE.search("Enter the 6-digit code we sent to you")
    assert wt.BLOCKER_RE.search("Upgrade to send more") and wt.EMAIL_NEEDED_RE.search("We ask for your email to keep the community safe.")
    assert wt.valid_email("a.b@company.co.in") and not wt.valid_email("nope") and not wt.valid_email(None)


@pytest.fixture()
def fake_uploader(monkeypatch):
    """Replace the browser upload; `calls` records each upload, `gate` holds it until released."""
    calls, gate = [], threading.Event()
    state = {"result": "https://we.tl/t-FAKE123", "fail": None}

    def upload(path, sender_email=None, on_step=None, debug_dir=None, ask_code=None, **_):
        calls.append(path)
        state["email"] = sender_email
        on_step("uploading", 40)
        if state.get("code"):                      # WeTransfer asks for the e-mailed code
            error = None
            for _ in range(3):
                got = ask_code(error)
                if got is None:
                    raise wt.WeTransferError("no verification code was entered in time")
                if got == state["code"]:
                    break
                error = "WeTransfer did not accept that code"
            else:
                raise wt.WeTransferError("WeTransfer did not accept the e-mailed code after 3 tries")
        assert gate.wait(10)
        if state["fail"]:
            raise wt.WeTransferError(state["fail"])
        return state["result"]

    monkeypatch.setattr(wt, "check_available", lambda: None)
    monkeypatch.setattr(wt, "upload_zip_to_wetransfer", upload)
    return types.SimpleNamespace(calls=calls, gate=gate, state=state)


def _poll(client, job_id, until=("success", "failed")):
    deadline = time.time() + 10
    while time.time() < deadline:
        s = client.get(f"/api/export-wetransfer/{job_id}").json()
        if s["status"] in until:
            return s
        time.sleep(0.02)
    raise AssertionError("upload job did not finish")


def test_upload_of_the_modal_zip_returns_the_link(client, fake_uploader):
    job = _upload(client)["id"]
    shop = _done_shop(client, job)
    built = client.post("/api/export-zip", json={"shop_ids": [shop["id"]]}).json()
    r = client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": EMAIL})
    assert r.status_code == 200 and r.json()["token"] == built["token"]
    job_id = r.json()["job_id"]
    running = _poll(client, job_id, until=("running",))
    assert running["wetransfer_url"] is None
    fake_uploader.gate.set()
    done = _poll(client, job_id)
    assert done["status"] == "success" and done["wetransfer_url"] == "https://we.tl/t-FAKE123"
    assert fake_uploader.calls[0].endswith("Signage_Assets_Export.zip")
    assert main._asset_zips[built["token"]].busy == 0


def test_closing_the_modal_during_an_upload_deletes_the_zip_afterwards(client, fake_uploader):
    job = _upload(client)["id"]
    shop = _done_shop(client, job)
    built = client.post("/api/export-zip", json={"shop_ids": [shop["id"]]}).json()
    job_id = client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": EMAIL}).json()["job_id"]
    _poll(client, job_id, until=("running",))
    d = main._asset_zips[built["token"]].dir
    assert client.delete(f"/api/export-zip/{built['token']}").json() == {"deleted": False, "pending_upload": True}
    assert d.exists()                                    # still being read by the upload
    fake_uploader.gate.set()
    assert _poll(client, job_id)["status"] == "success"
    assert not d.exists() and built["token"] not in main._asset_zips


def test_shop_ids_alone_build_the_zip_first(client, fake_uploader):
    """The spec's payload: {shop_ids} without a generated ZIP."""
    job = _upload(client)["id"]
    shop = _done_shop(client, job)
    fake_uploader.gate.set()
    r = client.post("/api/export-wetransfer", json={"shop_ids": [shop["id"]], "sender_email": EMAIL})
    assert r.status_code == 200 and r.json()["token"] in main._asset_zips
    assert _poll(client, r.json()["job_id"])["wetransfer_url"] == "https://we.tl/t-FAKE123"


def _built(client):
    job = _upload(client)["id"]
    shop = _done_shop(client, job)
    return client.post("/api/export-zip", json={"shop_ids": [shop["id"]]}).json()


def test_a_failed_upload_falls_back_to_a_server_link(client, fake_uploader):
    """The reported live failure (link_mode) must still give the page a working link, with the reason kept."""
    built = _built(client)
    fake_uploader.state["fail"] = "step 'link_mode': could not switch the transfer to 'get a link' mode"
    fake_uploader.gate.set()
    s = _poll(client, client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": EMAIL}).json()["job_id"])
    assert s["status"] == "success" and s["fallback_used"] is True and "link_mode" in s["wetransfer_error"]
    assert "/api/shared/" in s["wetransfer_url"] and s["wetransfer_url"].endswith("/Signage_Assets_Export.zip")
    assert "WeTransfer automated link creation failed" in s["message"] and s["expires_at"] > time.time()
    path = s["wetransfer_url"].split("/api/shared/", 1)[1]
    r = client.get(f"/api/shared/{path}")
    assert r.status_code == 200 and r.content == client.get(built["download"]).content
    # the shared copy outlives the modal's archive
    client.delete(f"/api/export-zip/{built['token']}")
    assert client.get(f"/api/shared/{path}").status_code == 200


def test_a_shared_link_expires_and_bad_ids_are_refused(client, fake_uploader, monkeypatch):
    built = _built(client)
    fake_uploader.state["fail"] = "boom"
    fake_uploader.gate.set()
    s = _poll(client, client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": EMAIL}).json()["job_id"])
    share_id = s["wetransfer_url"].split("/api/shared/", 1)[1].split("/")[0]
    assert client.get(f"/api/shared/{share_id}/other.zip").status_code == 404
    assert client.get("/api/shared/..%2F..%2Fsecret/Signage_Assets_Export.zip").status_code == 404
    assert client.get("/api/shared/short/Signage_Assets_Export.zip").status_code == 404
    meta = main.SHARED_ZIPS / share_id / "meta.json"
    meta.write_text('{"expires_at": 1}', encoding="utf-8")
    assert client.get(f"/api/shared/{share_id}/Signage_Assets_Export.zip").status_code == 410
    assert not (main.SHARED_ZIPS / share_id).exists()


def test_too_big_or_no_playwright_skip_straight_to_the_server_link(client, fake_uploader, monkeypatch):
    built = _built(client)
    assert client.post("/api/export-wetransfer", json={}).status_code == 422
    assert client.post("/api/export-wetransfer", json={"token": "nope"}).status_code == 404
    assert client.get("/api/export-wetransfer/nope").status_code == 404
    monkeypatch.setenv("SIGNAGE_WETRANSFER_MAX_GB", "0.0000000001")          # 0.1 byte
    s = _poll(client, client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": EMAIL}).json()["job_id"])
    assert s["fallback_used"] and "free-transfer limit" in s["wetransfer_error"] and fake_uploader.calls == []
    monkeypatch.delenv("SIGNAGE_WETRANSFER_MAX_GB")

    def missing():
        raise ImportError("No module named 'playwright'")

    monkeypatch.setattr(wt, "check_available", missing)
    s = _poll(client, client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": EMAIL}).json()["job_id"])
    assert s["fallback_used"] and "pip install playwright" in s["wetransfer_error"] and sys.executable in s["wetransfer_error"]
    assert fake_uploader.calls == []


def test_share_base_says_whether_other_computers_can_open_the_link(monkeypatch):
    monkeypatch.delenv("SIGNAGE_PUBLIC_BASE_URL", raising=False)
    assert main._share_base(("127.0.0.1", 8000)) == {"base": "http://127.0.0.1:8000", "local_only": True}
    monkeypatch.setattr(main, "_lan_ip", lambda: "192.168.1.20")
    assert main._share_base(("0.0.0.0", 8000)) == {"base": "http://192.168.1.20:8000", "local_only": False}
    monkeypatch.setattr(main, "_lan_ip", lambda: None)
    assert main._share_base(("0.0.0.0", 8000))["local_only"] is True
    assert main._share_base(("10.0.0.5", 9000)) == {"base": "http://10.0.0.5:9000", "local_only": False}
    monkeypatch.setenv("SIGNAGE_PUBLIC_BASE_URL", "https://signage.example.local/")
    assert main._share_base(("127.0.0.1", 8000)) == {"base": "https://signage.example.local", "local_only": False}


def test_the_local_only_fallback_explains_how_to_share_it(client, fake_uploader, monkeypatch):
    monkeypatch.delenv("SIGNAGE_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(main, "_share_base", lambda server: {"base": "http://127.0.0.1:8000", "local_only": True})
    built = _built(client)
    fake_uploader.state["fail"] = "timeout"
    fake_uploader.gate.set()
    s = _poll(client, client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": EMAIL}).json()["job_id"])
    assert s["local_only"] is True and s["wetransfer_url"].startswith("http://127.0.0.1:8000/api/shared/")
    assert "--host 0.0.0.0" in s["message"]


# ---- the uploader's real browser mechanics, against a LOCAL stand-in page (tests/fixtures/wetransfer_standin.html) ----

def _edge_available() -> bool:
    try:
        wt.check_available()
    except ImportError:
        return False
    import os
    return any(os.path.exists(p) for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                                           r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"))


needs_edge = pytest.mark.skipif(not _edge_available(), reason="needs Playwright and Microsoft Edge")


@needs_edge
def test_uploader_mechanics_against_a_local_standin(tmp_path, monkeypatch):
    """Launch Edge, pass the consent screens, attach the file, pick link mode, submit, follow the %, read the link."""
    from pathlib import Path
    page = Path(__file__).parent / "fixtures" / "wetransfer_standin.html"
    monkeypatch.setattr(wt, "WETRANSFER_URL", page.resolve().as_uri())
    f = tmp_path / "Signage_Assets_Export.zip"
    f.write_bytes(b"x" * 1234)
    steps = []
    link = wt.upload_zip_to_wetransfer(str(f), EMAIL, on_step=lambda n, p=None: steps.append((n, p)), debug_dir=tmp_path / "dbg")
    assert link == "https://we.tl/t-LOCALstandin1234"                       # the file really reached the page (its size)
    names = [n for n, _ in steps]
    assert names[:7] == ["launch", "open", "consent", "add_file", "link_mode", "email", "submit"] and names[-1] == "done"
    assert any(n == "uploading" and p and 0 < p < 100 for n, p in steps)   # progress read off the page


@needs_edge
def test_uploader_stops_at_a_screen_it_cannot_pass_with_a_screenshot(tmp_path, monkeypatch):
    from pathlib import Path
    page = Path(__file__).parent / "fixtures" / "wetransfer_standin.html"
    monkeypatch.setattr(wt, "WETRANSFER_URL", page.resolve().as_uri() + "?blocked=1")
    f = tmp_path / "a.zip"
    f.write_bytes(b"x")
    with pytest.raises(wt.WeTransferError) as e:
        wt.upload_zip_to_wetransfer(str(f), EMAIL, debug_dir=tmp_path / "dbg")
    assert "uploading" in str(e.value) and "limit reached" in str(e.value).lower()
    assert "screenshot:" in str(e.value) and list((tmp_path / "dbg").glob("wetransfer_*_uploading.png"))


@needs_edge
def test_uploader_finds_the_icon_only_options_button_like_the_live_page(tmp_path, monkeypatch):
    """The live "..." button has no accessible name (screenshot 2026-09-28): found by position, above Transfer, and its
    menu's 'Get transfer link' (menuitemradio) chosen."""
    from pathlib import Path
    page = Path(__file__).parent / "fixtures" / "wetransfer_standin.html"
    monkeypatch.setattr(wt, "WETRANSFER_URL", page.resolve().as_uri() + "?iconmenu=1")
    f = tmp_path / "Signage_Assets_Export.zip"
    f.write_bytes(b"x" * 77)
    assert wt.upload_zip_to_wetransfer(str(f), EMAIL, debug_dir=tmp_path / "dbg") == "https://we.tl/t-LOCALstandin77"


@needs_edge
def test_link_mode_failure_saves_the_pages_controls_for_diagnosis(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    page = Path(__file__).parent / "fixtures" / "wetransfer_standin.html"
    monkeypatch.setattr(wt, "WETRANSFER_URL", page.resolve().as_uri() + "?iconmenu=1&nolink=1")
    f = tmp_path / "a.zip"
    f.write_bytes(b"x")
    with pytest.raises(wt.WeTransferError) as e:
        wt.upload_zip_to_wetransfer(str(f), EMAIL, debug_dir=tmp_path / "dbg")
    assert "link_mode" in str(e.value) and "no 'link' choice" in str(e.value)
    dumps = list((tmp_path / "dbg").glob("wetransfer_*_link_mode_controls.json"))
    assert dumps and list((tmp_path / "dbg").glob("wetransfer_*_link_mode.png"))
    texts = [c["text"] for c in json.loads(dumps[0].read_text(encoding="utf-8"))["controls"]]
    assert "Transfer" in texts and "Password protect" in texts and "3 days" in texts


# ---- sender e-mail and the e-mailed code (OTP) ----

def test_the_email_is_required_validated_and_passed_on(client, fake_uploader):
    built = _built(client)
    r = client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": "not-an-email"})
    assert r.status_code == 422
    fake_uploader.gate.set()
    s = _poll(client, client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": EMAIL}).json()["job_id"])
    assert s["status"] == "success" and not s["fallback_used"] and fake_uploader.state["email"] == EMAIL
    # no address at all: WeTransfer cannot be used, the server link is made straight away
    s = _poll(client, client.post("/api/export-wetransfer", json={"token": built["token"]}).json()["job_id"])
    assert s["fallback_used"] and "sender e-mail" in s["wetransfer_error"]


def test_otp_flow_wrong_code_then_right_code(client, fake_uploader):
    built = _built(client)
    fake_uploader.state["code"] = "953GYV"
    fake_uploader.gate.set()
    job_id = client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": EMAIL}).json()["job_id"]
    s = _poll(client, job_id, until=("requires_otp",))
    assert s["session_id"] == job_id and s["otp_error"] is None and s["otp_deadline"] > time.time()
    assert client.post("/api/export-wetransfer/verify-otp", json={"session_id": job_id, "otp_code": "12"}).status_code == 422
    assert client.post("/api/export-wetransfer/verify-otp", json={"session_id": job_id, "otp_code": "95#GY"}).status_code == 422
    assert client.post("/api/export-wetransfer/verify-otp", json={"session_id": "nope", "otp_code": "123456"}).status_code == 404
    r = client.post("/api/export-wetransfer/verify-otp", json={"session_id": job_id, "otp_code": "999 999"})
    assert r.status_code == 200 and r.json()["status"] == "verifying"
    s = _poll(client, job_id, until=("requires_otp",))
    assert "did not accept" in s["otp_error"]                               # asked again, with the reason
    r = client.post("/api/export-wetransfer/verify-otp", json={"session_id": job_id, "otp_code": " 953-gyv "})
    assert r.status_code == 200                                             # lower case, spaces, a dash: all fine
    s = _poll(client, job_id)
    assert s["status"] == "success" and s["wetransfer_url"] == "https://we.tl/t-FAKE123" and not s["fallback_used"]
    r = client.post("/api/export-wetransfer/verify-otp", json={"session_id": job_id, "otp_code": "123456"})
    assert r.status_code == 409                                             # no longer waiting


def test_otp_rejected_three_times_or_never_entered_falls_back(client, fake_uploader, monkeypatch):
    built = _built(client)
    fake_uploader.state["code"] = "123456"
    fake_uploader.gate.set()
    job_id = client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": EMAIL}).json()["job_id"]
    for _ in range(3):
        _poll(client, job_id, until=("requires_otp",))
        client.post("/api/export-wetransfer/verify-otp", json={"session_id": job_id, "otp_code": "000000"})
    s = _poll(client, job_id)
    assert s["status"] == "success" and s["fallback_used"] and "3 tries" in s["wetransfer_error"]
    monkeypatch.setenv("SIGNAGE_WETRANSFER_OTP_WAIT_S", "0.3")
    job_id = client.post("/api/export-wetransfer", json={"token": built["token"], "sender_email": EMAIL}).json()["job_id"]
    s = _poll(client, job_id)
    assert s["fallback_used"] and "in time" in s["wetransfer_error"] and "/api/shared/" in s["wetransfer_url"]


@needs_edge
def test_uploader_relays_the_code_the_person_types(tmp_path, monkeypatch):
    """The stand-in's 6-box code screen: a wrong code is reported back through ask_code, the right one completes."""
    from pathlib import Path
    page = Path(__file__).parent / "fixtures" / "wetransfer_standin.html"
    monkeypatch.setattr(wt, "WETRANSFER_URL", page.resolve().as_uri() + "?iconmenu=1&code=1")
    f = tmp_path / "Signage_Assets_Export.zip"
    f.write_bytes(b"x" * 5)
    asked, answers = [], iter(["654321", "953gyv"])      # typed in lower case: the uploader upper-cases it

    def ask_code(error):
        asked.append(error)
        return next(answers)

    assert wt.upload_zip_to_wetransfer(str(f), EMAIL, debug_dir=tmp_path / "dbg", ask_code=ask_code) == "https://we.tl/t-LOCALstandin5"
    assert asked[0] is None and asked[1] and "did not accept" in asked[1]


@needs_edge
def test_uploader_without_a_code_callback_or_email_fails_clearly(tmp_path, monkeypatch):
    from pathlib import Path
    page = Path(__file__).parent / "fixtures" / "wetransfer_standin.html"
    monkeypatch.setattr(wt, "WETRANSFER_URL", page.resolve().as_uri() + "?code=1")
    f = tmp_path / "a.zip"
    f.write_bytes(b"x")
    with pytest.raises(wt.WeTransferError, match="sender e-mail address is required"):
        wt.upload_zip_to_wetransfer(str(f), None)
    with pytest.raises(wt.WeTransferError, match="nobody can type it"):
        wt.upload_zip_to_wetransfer(str(f), EMAIL, debug_dir=tmp_path / "dbg")
