"""Dev server that picks up code changes by itself - without `uvicorn --reload`.

    cd backend
    ..\\.venv\\Scripts\\python.exe dev_server.py            # port 8000
    ..\\.venv\\Scripts\\python.exe dev_server.py --port 8001

Why not `uvicorn --reload`: on Windows its reloader leaves ORPHANED server processes that keep answering on the port with the OLD code (see
CLAUDE.md). This script runs uvicorn as a plain child process and, when a Python / JSON file under `app/` or `brand_data/` changes, kills the
WHOLE process tree (taskkill /T /F) and starts a fresh one.

It never restarts in the middle of work: while a shop is queued / converting or an export is queued / running (read from the database) the
restart waits, because a restart fails whatever is in flight ("Interrupted: the server was restarted"). It restarts as soon as the server is idle.
The frontend (`npm run dev`) already hot-reloads by itself.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WATCH = [(HERE / "app", (".py", ".json")), (HERE / "brand_data", (".json",)), (HERE / ".env", None)]
POLL_S = 1.0
DEBOUNCE_S = 1.0           # wait for the editor to finish writing (several files are often saved together)


def snapshot() -> dict[str, float]:
    """{path: mtime} of every watched file."""
    seen: dict[str, float] = {}
    for root, suffixes in WATCH:
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = [p for p in root.rglob("*") if p.is_file() and p.suffix in suffixes and "__pycache__" not in p.parts]
        else:
            files = []
        for p in files:
            try:
                seen[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return seen


def changed(a: dict[str, float], b: dict[str, float]) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def db_path() -> Path:
    return Path(os.environ.get("SIGNAGE_DATA", str(HERE / "data"))) / "signage.db"


def busy() -> str | None:
    """A short description of the work in flight, or None when the server is idle."""
    path = db_path()
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            shops = conn.execute("SELECT COUNT(*) FROM shops WHERE status IN ('queued','converting')").fetchone()[0]
            exports = conn.execute("SELECT COUNT(*) FROM exports WHERE status IN ('queued','running')").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None                                       # cannot tell: do not block the restart forever
    parts = []
    if shops:
        parts.append(f"{shops} shop(s) converting")
    if exports:
        parts.append(f"{exports} export(s) running")
    return ", ".join(parts) or None


def start(port: int, host: str) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--host", host]
    return subprocess.Popen(cmd, cwd=str(HERE))


def stop(proc: subprocess.Popen) -> None:
    """Kill the server and everything it started (workers included) - never leaves an orphan on the port."""
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    known = snapshot()
    proc = start(args.port, args.host)
    print(f"[dev] server started on {args.host}:{args.port} (pid {proc.pid}); watching {len(known)} files - Ctrl+C to stop", flush=True)
    pending: list[str] = []
    waiting_since = 0.0
    try:
        while True:
            time.sleep(POLL_S)
            now = snapshot()
            diff = changed(known, now)
            if diff:
                pending = sorted(set(pending) | set(diff))
                known = now
                waiting_since = time.time()
                continue
            if proc.poll() is not None:                   # the server died (a crash while editing): start it again with the new code
                print(f"[dev] server exited with {proc.returncode}; restarting", flush=True)
                proc = start(args.port, args.host)
                continue
            if pending and time.time() - waiting_since >= DEBOUNCE_S:
                work = busy()
                if work:
                    print(f"[dev] {len(pending)} file(s) changed - waiting for the server to finish: {work}", flush=True)
                    waiting_since = time.time() + 4       # print the hint at most every few seconds
                    continue
                names = ", ".join(Path(p).name for p in pending[:4]) + ("..." if len(pending) > 4 else "")
                print(f"[dev] changed: {names} - restarting", flush=True)
                stop(proc)
                proc = start(args.port, args.host)
                pending = []
                print(f"[dev] server restarted (pid {proc.pid})", flush=True)
    except KeyboardInterrupt:
        print("\n[dev] stopping", flush=True)
    finally:
        stop(proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
