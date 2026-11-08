"""The agent's HTTPS calls to the website (standard library only: the exe stays small). Outbound only."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TIMEOUT_S = 20


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


def _open(url: str, *, data: bytes | None = None, token: str = "", timeout: float = TIMEOUT_S):
    headers = {"User-Agent": "AdinnSignageAgent"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        return urllib.request.urlopen(req, timeout=timeout)   # noqa: S310 - the server URL comes from the downloaded config.json
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8", "ignore")).get("detail", "")
        except Exception:  # noqa: BLE001
            pass
        raise ApiError(e.code, str(detail) or e.reason) from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ApiError(0, f"cannot reach the server ({getattr(e, 'reason', e)})") from None


def call(server: str, path: str, body: dict | None = None, token: str = "") -> dict:
    data = json.dumps(body).encode() if body is not None else None
    with _open(server + path, data=data, token=token) as r:
        return json.loads(r.read().decode("utf-8"))


def register(server: str, code: str, hostname: str, version: str, corel: list) -> dict:
    return call(server, "/api/agent/register", {"code": code, "hostname": hostname, "version": version, "corel": corel})


def heartbeat(server: str, token: str, version: str, corel: list, state: str, fonts_hash: str = "") -> dict:
    return call(server, "/api/agent/heartbeat", {"version": version, "corel": corel, "state": state, "fonts_hash": fonts_hash}, token)


def send_fonts(server: str, token: str, fonts_hash: str, fonts: dict) -> dict:
    return call(server, "/api/agent/fonts", {"hash": fonts_hash, "fonts": fonts}, token)


def latest_version(server: str) -> dict:
    return call(server, "/api/agent/version")


def download_exe(server: str, token: str, dest: Path, expected_sha256: str, expected_size: int | None = None) -> None:
    """Download the bare exe to `dest`; the file is deleted and ApiError raised unless its SHA-256 (and size) match."""
    sha = hashlib.sha256()
    size = 0
    try:
        with _open(server + "/api/agent/download?raw=1", token=token, timeout=120) as r, open(dest, "wb") as f:
            while chunk := r.read(1024 * 256):
                sha.update(chunk)
                size += len(chunk)
                f.write(chunk)
        if sha.hexdigest().lower() != expected_sha256.lower() or (expected_size is not None and size != expected_size):
            raise ApiError(0, "the downloaded update does not match its checksum - discarded")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------ the job queue

KINDS = ["convert", "scene_build", "export"]       # what this agent can do (an older agent only asked for "convert")


def claim_jobs(server: str, token: str, limit: int = 5, kinds: list[str] | None = None) -> list[dict]:
    return call(server, "/api/agent/jobs/claim", {"max": limit, "kinds": kinds or KINDS}, token)["jobs"]


def report_progress(server: str, token: str, job_id: str, step: str = "") -> None:
    call(server, f"/api/agent/jobs/{job_id}/progress", {"step": step}, token)


def download_master(server: str, token: str, job_id: str, dest: Path, expected_size: int | None = None) -> None:
    """Download a job's master to `dest` (via a .part file); a size mismatch discards it."""
    part = dest.with_name(dest.name + ".part")
    size = 0
    try:
        with _open(f"{server}/api/agent/jobs/{job_id}/master", token=token, timeout=300) as r, open(part, "wb") as f:
            while chunk := r.read(1024 * 256):
                size += len(chunk)
                f.write(chunk)
        if expected_size is not None and size != expected_size:
            raise ApiError(0, f"the master arrived incomplete ({size} of {expected_size} bytes)")
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def download_input(server: str, token: str, job_id: str, name: str, dest: Path, expected_size: int | None = None) -> None:
    """A file a scene-build / export job needs (its .cdr, scene.json, a product image); incomplete downloads are discarded."""
    part = dest.with_name(dest.name + ".part")
    size = 0
    try:
        with _open(f"{server}/api/agent/jobs/{job_id}/input/{urllib.parse.quote(name, safe='')}", token=token, timeout=300) as r, open(part, "wb") as f:
            while chunk := r.read(1024 * 256):
                size += len(chunk)
                f.write(chunk)
        if expected_size is not None and size != expected_size:
            raise ApiError(0, f"{name} arrived incomplete ({size} of {expected_size} bytes)")
        part.replace(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def upload_file(server: str, token: str, job_id: str, name: str, path: Path) -> None:
    """Stream one result file to the site (PUT, Content-Length known, never read into memory)."""
    quoted = urllib.parse.quote(name, safe="")
    size = path.stat().st_size
    with open(path, "rb") as f:
        req = urllib.request.Request(f"{server}/api/agent/jobs/{job_id}/files/{quoted}", data=f, method="PUT",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream",
                                              "Content-Length": str(size), "User-Agent": "AdinnSignageAgent"})
        try:
            with urllib.request.urlopen(req, timeout=600):   # noqa: S310 - the server URL comes from the downloaded config.json
                return
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8", "ignore")).get("detail", "")
            except Exception:  # noqa: BLE001
                pass
            raise ApiError(e.code, str(detail) or e.reason) from None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise ApiError(0, f"cannot reach the server ({getattr(e, 'reason', e)})") from None


def post_result(server: str, token: str, job_id: str, status: str, files: dict | None = None, report: dict | None = None,
                error: str = "") -> None:
    call(server, f"/api/agent/jobs/{job_id}/result", {"status": status, "files": files or {}, "report": report or {}, "error": error}, token)
