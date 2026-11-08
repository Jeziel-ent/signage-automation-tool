"""The agent's brain: pairing, the heartbeat loop and update checks. The local status server (local_server.py) reads its state."""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from pathlib import Path

from . import client, corel, engine_bridge, updater
from .jobs import JobRunner
from .config import Config
from .version import AGENT_VERSION

log = logging.getLogger("agent")
HEARTBEAT_S = float(os.environ.get("SIGNAGE_AGENT_HEARTBEAT_S", "15"))
UPDATE_EVERY_S = float(os.environ.get("SIGNAGE_AGENT_UPDATE_S", str(4 * 3600)))
COREL_CACHE_S = 60


class Agent:
    def __init__(self, config: Config):
        self.config = config
        self.state = "idle"                 # idle | busy (a CorelDRAW job is running - none yet)
        self.last_ok: float | None = None   # time of the last good heartbeat
        self.last_error = ""
        self.update_status = ""             # '' | current | available | busy | updated | failed: ...
        self.latest_version = ""
        self.started = time.time()
        self._corel: list[dict] = []
        self._corel_at = 0.0
        self._stop = threading.Event()
        self._last_update_check = 0.0
        self.runner = JobRunner(self, poll_s=float(os.environ.get("SIGNAGE_AGENT_JOB_POLL_S", "4")))

    # ---- what the local status endpoint and the heartbeat report
    def corel_installs(self) -> list[dict]:
        if time.time() - self._corel_at > COREL_CACHE_S:
            try:
                self._corel = corel.find_installs()
            except Exception as e:  # noqa: BLE001
                log.warning("could not read CorelDRAW installs: %s", e)
                self._corel = []
            self._corel_at = time.time()
        return [{k: v for k, v in c.items() if k != "exe"} for c in self._corel]

    def status(self) -> dict:
        online = bool(self.last_ok and time.time() - self.last_ok < HEARTBEAT_S * 3 + 5)
        return {"agent": "adinn-signage-agent", "version": AGENT_VERSION, "configured": self.config.configured,
                "server": self.config.server_url, "paired": self.config.paired, "name": self.config.name,
                "connected": online, "state": self.state, "corel": self.corel_installs(), "last_error": self.last_error,
                "update": self.update_status, "latest_version": self.latest_version}

    # ---- pairing
    def pair(self, code: str) -> dict:
        """Exchange the website's one-time code for this PC's own token. Raises client.ApiError (message safe to show)."""
        if not self.config.configured:
            raise client.ApiError(0, "this agent has no config.json (extract the whole download and run it again)")
        r = client.register(self.config.server_url, code, socket.gethostname(), AGENT_VERSION, self.corel_installs())
        self.config.save_pairing(r["agent_id"], r["token"], r["name"])
        self.last_error = ""
        self.beat()
        return {"ok": True, "name": r["name"]}

    def unpair(self) -> None:
        self.config.clear_pairing()
        self.last_ok = None

    # ---- loop
    def beat(self) -> None:
        """One heartbeat; remembers the outcome. A 401 means the PC was disconnected on the website: pairing is dropped."""
        if not self.config.paired:
            return
        try:
            r = client.heartbeat(self.config.server_url, self.config.token, AGENT_VERSION, self.corel_installs(), self.state)
        except client.ApiError as e:
            self.last_error = e.detail
            if e.status == 401:
                log.warning("the website no longer knows this agent (disconnected): pairing dropped")
                self.config.clear_pairing()
                self.last_ok = None
            return
        self.last_ok, self.last_error = time.time(), ""
        self.latest_version = r.get("latest", "")
        updater.confirm_after_update(self.config.home)
        updater.cleanup_leftovers(self.config.home)
        if r.get("update_required"):
            self.maybe_update(force=True)

    def maybe_update(self, force: bool = False) -> None:
        if not self.config.paired:
            return
        self._last_update_check = time.time()
        self.update_status = updater.check_and_apply(self.config.server_url, self.config.token, AGENT_VERSION, self.config.home,
                                                     idle=self.state == "idle", force=force)
        if self.update_status not in ("current", ""):
            log.info("update check: %s", self.update_status)

    def run(self) -> None:
        log.info("agent %s started (server %s, paired %s)", AGENT_VERSION, self.config.server_url or "-", self.config.paired)
        engine_bridge.prepare(self.config.home / "data")
        threading.Thread(target=self.runner.loop, name="job-runner", daemon=True).start()
        while not self._stop.is_set():
            self.beat()
            if updater.rollback_due(self.config.home) and not self.last_ok:
                updater.rollback(self.config.home)
            if self.config.paired and time.time() - self._last_update_check > UPDATE_EVERY_S:
                self.maybe_update()
            self._stop.wait(HEARTBEAT_S)

    def stop(self) -> None:
        self._stop.set()
        self.runner.stop()


def home_of(path: str | None) -> Path | None:
    return Path(path) if path else None
