"""The Windows agent: pairing, heartbeat, version check and the download the website offers.

The agent (agent/ in the repository, built into SignageAgent.exe) runs on a designer PC next to CorelDRAW. The website
  * detects it: the browser asks the agent's local status endpoint (127.0.0.1) and this module's `GET /api/agents` says which agents
    have called in recently (a heartbeat every ~15 s => "online" for 60 s);
  * recommends and serves the download (`GET /api/agent/download`: a zip with SignageAgent.exe and a config.json that already names
    this server, so the agent only ever talks to - and only ever takes pairing from - the site it was downloaded from);
  * pairs it with the signed-in user (`POST /api/agent/pairing-code` -> the agent exchanges that one-time code at
    `POST /api/agent/register` for its own revocable token).
Agent -> server calls carry `Authorization: Bearer <token>`; only a hash of the token is stored. The agent only makes OUTBOUND calls.
Running conversions on the agent (the job queue) is the next milestone; today the agent reports itself, its CorelDRAW installs and
its version, and keeps itself up to date (`GET /api/agent/version`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import auth, db

ONLINE_S = 60                 # an agent that called in within this many seconds is "online"
CODE_TTL_S = 600
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I
EXE_NAME = "SignageAgent.exe"
MSG_NO_AGENT_BUILD = "the agent has not been built on this server yet (agent/build.py)"

router = APIRouter()


def dist_dir() -> Path:
    return Path(os.environ.get("SIGNAGE_AGENT_DIST", Path(__file__).resolve().parents[2] / "agent" / "dist"))


def release() -> dict | None:
    """{version, sha256, size, min_supported} of the built agent (agent/dist/latest.json + SignageAgent.exe), else None."""
    d = dist_dir()
    meta, exe = d / "latest.json", d / EXE_NAME
    if not meta.is_file() or not exe.is_file():
        return None
    try:
        info = json.loads(meta.read_text(encoding="utf-8"))
        return {"version": str(info["version"]), "sha256": str(info["sha256"]).lower(), "size": exe.stat().st_size,
                "min_supported": str(info.get("min_supported") or info["version"])}
    except (OSError, ValueError, KeyError):
        return None


def version_key(v: str | None) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")) or (0,)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _clean_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def agent_from_request(request: Request) -> dict:
    token = _bearer(request)
    agent = db.get_agent_by_token(_hash(token)) if token else None
    if not agent:
        raise HTTPException(401, "unknown or revoked agent")
    return agent


def _public_base(request: Request) -> str:
    base = os.environ.get("SIGNAGE_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base:
        return base
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{request.headers.get('host') or request.url.netloc}"


def _agent_json(a: dict) -> dict:
    seen = a.get("last_seen")
    try:
        corel = json.loads(a["corel_json"]) if a.get("corel_json") else []
    except ValueError:
        corel = []
    latest = release()
    return {"id": a["id"], "name": a["name"], "owner": a.get("owner_email"), "version": a.get("version"), "corel": corel,
            "state": a.get("state"), "last_seen": seen, "online": bool(seen and time.time() - seen < ONLINE_S),
            "created_at": a["created_at"],
            "update_available": bool(latest and version_key(latest["version"]) > version_key(a.get("version")))}


# --------------------------------------------------------------------- for the website (signed-in user)

@router.get("/api/agent/config")
def agent_config(request: Request):
    """PUBLIC (the launch screen asks before sign-in; nothing secret in it). What the page needs to decide whether to recommend the agent: is a build downloadable, which version, and is an agent
    REQUIRED here (SIGNAGE_AGENT_REQUIRED=1, or the server itself has no real CorelDRAW engine)."""
    from .engines import get_engine
    latest = release()
    engine = get_engine(os.environ.get("SIGNAGE_ENGINE", "auto")).name
    required = os.environ.get("SIGNAGE_AGENT_REQUIRED", "").strip().lower() in ("1", "true", "yes") or engine != "corel"
    return {"agent_required": required, "download_available": latest is not None, "latest_version": latest["version"] if latest else None,
            "size": latest["size"] if latest else None, "engine": engine}


@router.post("/api/agent/pairing-code")
def pairing_code(request: Request):
    """A one-time code (10 minutes) that links an agent to the signed-in user. The page hands it to the agent on this computer."""
    user = getattr(request.state, "user", None) or auth.current_user(request)
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    db.create_pairing_code(_hash(raw), user["id"], CODE_TTL_S)
    return {"code": f"{raw[:4]}-{raw[4:]}", "expires_in": CODE_TTL_S}


@router.get("/api/agents")
def list_agents(request: Request, all: bool = False):
    """The agents of the signed-in user (an administrator may ask for all with ?all=1)."""
    user = getattr(request.state, "user", None) or auth.current_user(request)
    only = None if (all and user["role"] == "admin") else user["id"]
    return {"agents": [_agent_json(a) for a in db.list_agents(only)]}


@router.delete("/api/agents/{agent_id}", responses={403: {"description": "Not yours"}, 404: {"description": "Not found"}})
def revoke(agent_id: str, request: Request):
    """Disconnect an agent: its token stops working at once (the PC can pair again with a new code)."""
    user = getattr(request.state, "user", None) or auth.current_user(request)
    a = db.get_agent(agent_id)
    if not a or a.get("revoked_at"):
        raise HTTPException(404, "agent not found")
    if a["user_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403, "this agent belongs to another user")
    db.revoke_agent(agent_id)
    return {"revoked": agent_id}


# --------------------------------------------------------------------- download + version (session OR agent token)

def _signed_in_or_agent(request: Request) -> None:
    if auth.current_user(request) is None:
        agent_from_request(request)      # raises 401 when there is neither a session nor a valid agent token


@router.get("/api/agent/version", responses={404: {"description": "No build"}})
def agent_version():
    """{version, sha256, size, min_supported}: what the agent compares itself with. Public (no secrets in it)."""
    latest = release()
    if latest is None:
        raise HTTPException(404, MSG_NO_AGENT_BUILD)
    return latest


@router.get("/api/agent/download", responses={401: {"description": "Login required"}, 404: {"description": "No build"}})
def agent_download(request: Request, raw: bool = False):
    """The installer the page offers: a zip with SignageAgent.exe + config.json (this server's address) + a short README. PUBLIC: the
    launch screen offers it before anybody has signed in, and the exe does nothing without a pairing code from a signed-in user.
    `?raw=1` is the bare exe - what a running agent downloads to update itself (needs a session or the agent's token)."""
    if raw:
        _signed_in_or_agent(request)
    latest = release()
    if latest is None:
        raise HTTPException(404, MSG_NO_AGENT_BUILD)
    exe = dist_dir() / EXE_NAME
    if raw:
        return FileResponse(exe, media_type="application/octet-stream", filename=EXE_NAME)
    base = _public_base(request)
    tmp = tempfile.NamedTemporaryFile(prefix="agent_", suffix=".zip", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, EXE_NAME)
        z.writestr("config.json", json.dumps({"server_url": base, "allowed_origins": [base]}, indent=2))
        z.writestr("README.txt",
                   "Adinn Signage Agent\r\n\r\n1. Extract this whole folder (do not run the .exe from inside the zip).\r\n"
                   f"2. Double-click {EXE_NAME}. It installs itself for your Windows user and starts with Windows.\r\n"
                   f"3. Go back to {base} - the page detects the agent and asks you to connect this PC.\r\n")
    return FileResponse(tmp.name, media_type="application/zip", filename=f"SignageAgent-{latest['version']}.zip",
                        background=BackgroundTask(lambda: Path(tmp.name).unlink(missing_ok=True)))


# --------------------------------------------------------------------- the agent's own calls

class RegisterBody(BaseModel):
    code: str
    hostname: str = ""
    version: str = ""
    corel: list = []


class HeartbeatBody(BaseModel):
    version: str = ""
    corel: list | None = None
    state: str = "idle"


@router.post("/api/agent/register", responses={401: {"description": "Bad or expired code"}, 429: {"description": "Too many attempts"}})
def register(body: RegisterBody, request: Request):
    """Exchange a pairing code for the agent's own token (shown once; only its hash is stored)."""
    key = f"agent-register|{request.client.host if request.client else '?'}"
    if auth._locked(key):
        raise HTTPException(429, "too many failed attempts - wait a few minutes and try again")
    user_id = db.consume_pairing_code(_hash(_clean_code(body.code)))
    if user_id is None:
        auth._note_failure(key)
        raise HTTPException(401, "that code is wrong, already used or expired - ask the website for a new one")
    auth._clear_failures(key)
    agent_id, token = uuid.uuid4().hex[:12], secrets.token_urlsafe(32)
    name = (body.hostname or "Windows PC").strip()[:60]
    db.create_agent(agent_id, user_id, name, _hash(token), body.version or None, json.dumps(body.corel))
    return {"agent_id": agent_id, "token": token, "name": name}


@router.post("/api/agent/heartbeat")
def heartbeat(body: HeartbeatBody, request: Request):
    """Called every ~15 s by the agent: proves it is alive and refreshes its version / CorelDRAW installs. Answers what the
    agent should know about updates."""
    agent = agent_from_request(request)
    db.touch_agent(agent["id"], body.version or None, json.dumps(body.corel) if body.corel is not None else None, body.state or None)
    latest = release()
    out = {"ok": True, "agent_id": agent["id"]}
    if latest:
        out.update(latest=latest["version"], min_supported=latest["min_supported"],
                   update_required=version_key(body.version) < version_key(latest["min_supported"]))
    return out


# --------------------------------------------------------------------- the job queue (the agent's work)

LEASE_S = 120                 # a claimed job returns to the queue unless its agent reports in within this time
CLAIM_MAX = 5                 # same batch size the local worker uses (main.CONVERT_BATCH_MAX)
_SAFE_NAME = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,200}$")

# kind -> fn(job: dict, entry: dict); main.py registers what happens with a finished job (kept here so this module never imports main)
FINALIZERS: dict[str, Callable[[dict, dict], None]] = {}


def register_finalizer(kind: str, fn: Callable[[dict, dict], None]) -> None:
    FINALIZERS[kind] = fn


def _max_bytes() -> int:
    try:
        return int(float(os.environ.get("SIGNAGE_MAX_UPLOAD_MB", "1024")) * 1024 * 1024)
    except ValueError:
        return 1024 * 1024 * 1024


def expire_and_fail() -> int:
    """Put jobs whose agent went silent back in the queue (or fail them after MAX_ATTEMPTS) and keep the shop rows in step. Called
    when an agent asks for work and by the server's background loop; returns how many jobs changed."""
    changed = db.expire_agent_jobs()
    for c in changed:
        if not c["shop_id"] or not db.get_shop(c["shop_id"]):
            continue
        if c["state"] == "failed":
            db.set_shop_status(c["shop_id"], "failed", error="the agent stopped responding and the job ran out of retries")
        else:
            db.set_shop_status(c["shop_id"], "queued")
    return len(changed)


def _held_job(job_id: str, agent: dict) -> dict:
    """The job, which this agent must currently hold (claimed or running) - else 404 / 409."""
    job = db.get_agent_job(job_id)
    if not job:
        raise HTTPException(404, "no such job")
    if job["agent_id"] != agent["id"] or job["state"] not in ("claimed", "running"):
        raise HTTPException(409, "this agent no longer holds that job (its lease ran out and the job went back to the queue)")
    return job


def _master_key(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}-{int(st.st_mtime)}"


class ClaimBody(BaseModel):
    max: int = CLAIM_MAX


class ProgressBody(BaseModel):
    step: str = ""


class ResultBody(BaseModel):
    status: str = "done"                 # done | error
    files: dict = {}                     # kind -> file name (uploaded with PUT .../files/{name} before this call)
    report: dict = {}
    error: str = ""


@router.post("/api/agent/jobs/claim")
def claim_jobs(body: ClaimBody, request: Request):
    """The agent asks for work: up to `max` queued jobs of ITS OWNER, oldest first. Each comes with what the agent needs to run it
    (the shop settings, the master's cache key and size). Doubles as a keep-alive for the agent."""
    agent = agent_from_request(request)
    db.touch_agent(agent["id"], None, None, None)
    expire_and_fail()
    out = []
    for job in db.claim_agent_jobs(agent["id"], agent["user_id"], max(1, min(body.max, CLAIM_MAX)), LEASE_S):
        payload = json.loads(job["payload_json"])
        master = Path(payload["master"]["path"])
        if not master.is_file():
            db.finish_agent_job(job["id"], agent["id"], False, None, "the master file is missing on the server")
            if job["shop_id"] and db.get_shop(job["shop_id"]):
                db.set_shop_status(job["shop_id"], "failed", error="the master file is missing on the server")
            continue
        if job["shop_id"] and db.get_shop(job["shop_id"]):
            db.set_shop_status(job["shop_id"], "converting", step="starting")
        out.append({"id": job["id"], "kind": job["kind"], "shop": payload["shop"], "lease_s": LEASE_S,
                    "master": {"filename": payload["master"]["filename"], "key": _master_key(master), "size": master.stat().st_size}})
    return {"jobs": out}


@router.get("/api/agent/jobs/{job_id}/master", responses={404: {"description": "No such job"}, 409: {"description": "Not held"}})
def job_master(job_id: str, request: Request):
    """The master .cdr of a job this agent holds (the agent caches it by `master.key`)."""
    agent = agent_from_request(request)
    job = _held_job(job_id, agent)
    master = Path(json.loads(job["payload_json"])["master"]["path"])
    if not master.is_file():
        raise HTTPException(404, "the master file is missing on the server")
    db.touch_agent_job(job_id, agent["id"], LEASE_S, running=False)
    return FileResponse(master, media_type="application/octet-stream", filename=master.name)


@router.post("/api/agent/jobs/{job_id}/progress", responses={409: {"description": "Not held"}})
def job_progress(job_id: str, body: ProgressBody, request: Request):
    """The agent reports the step it is on (launch, open, tile_resize, saveas, pdf, png, uploading...): extends the lease and drives
    the progress bar of the shop."""
    agent = agent_from_request(request)
    job = _held_job(job_id, agent)
    db.touch_agent_job(job_id, agent["id"], LEASE_S, step=body.step or None)
    if job["shop_id"] and body.step and db.get_shop(job["shop_id"]):
        db.set_shop_status(job["shop_id"], "converting", step=body.step)
    return {"ok": True}


@router.put("/api/agent/jobs/{job_id}/files/{name}", responses={400: {"description": "Bad name"}, 409: {"description": "Not held"},
                                                              413: {"description": "Too large"}})
async def job_upload(job_id: str, name: str, request: Request):
    """One result file of a job this agent holds, streamed straight to the shop's output folder (the same folder a local conversion
    writes to), so the existing download / preview / ZIP code finds it unchanged."""
    agent = agent_from_request(request)
    job = _held_job(job_id, agent)
    if not _SAFE_NAME.match(name) or name in (".", "..") or name.startswith("."):
        raise HTTPException(400, "bad file name")
    out_dir = Path(json.loads(job["payload_json"])["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    dest, tmp = out_dir / name, out_dir / (name + ".part")
    limit, written = _max_bytes(), 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                written += len(chunk)
                if written > limit:
                    raise HTTPException(413, f"the file is larger than the {limit // (1024 * 1024)} MB limit")
                await run_in_threadpool(f.write, chunk)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    db.touch_agent_job(job_id, agent["id"], LEASE_S)
    return {"name": name, "size": written}


@router.post("/api/agent/jobs/{job_id}/result", responses={409: {"description": "Not held"}, 422: {"description": "Files missing"}})
def job_result(job_id: str, body: ResultBody, request: Request):
    """The agent is finished. `done` needs every named file to have been uploaded; the shop then becomes `done` (or `failed`)."""
    agent = agent_from_request(request)
    job = _held_job(job_id, agent)
    payload = json.loads(job["payload_json"])
    out_dir = Path(payload["out_dir"]).resolve()
    ok = body.status == "done"
    entry: dict
    if ok:
        files: dict = {}
        for kind, name in body.files.items():
            p = (out_dir / str(name)).resolve()
            if out_dir not in p.parents or not p.is_file():
                raise HTTPException(422, f"file {name!r} ({kind}) was not uploaded")
            files[kind] = p.name           # stored like a local conversion: names relative to the shop's output folder
        entry = {"status": "done", "result": {"files": files, "report": body.report}}
    else:
        entry = {"status": "error", "error": body.error or "the agent reported an error"}
    fin = FINALIZERS.get(job["kind"])
    if fin:
        fin(job, entry)
    db.finish_agent_job(job_id, agent["id"], ok, entry.get("result") if ok else None, None if ok else entry["error"])
    return {"ok": True}
