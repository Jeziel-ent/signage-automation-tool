"""Where the agent keeps its settings, and what it is allowed to talk to.

`config.json` (next to the exe in the download, copied to the install folder on first run) names the ONE server this agent belongs to:
    {"server_url": "https://signage.example.com", "allowed_origins": ["https://signage.example.com"]}
The agent only ever calls that server and its local status server only answers browser pages loaded from those origins, so a
random website cannot pair it with another server. `state.json` (in the install folder) holds what pairing returned.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIR_NAME = "AdinnSignageAgent"


def install_dir() -> Path:
    override = os.environ.get("SIGNAGE_AGENT_HOME")          # tests and development
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_DIR_NAME


def normalize_origin(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


class Config:
    """server_url + allowed_origins (read-only, from config.json) and the pairing state (read/write, state.json)."""

    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else install_dir()
        self.server_url = ""
        self.allowed_origins: list[str] = []
        self.agent_id = ""
        self.token = ""
        self.name = ""
        self.load()

    @property
    def config_path(self) -> Path:
        return self.home / "config.json"

    @property
    def state_path(self) -> Path:
        return self.home / "state.json"

    def load(self) -> None:
        try:
            cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.server_url = normalize_origin(cfg.get("server_url", ""))
            self.allowed_origins = [normalize_origin(o) for o in cfg.get("allowed_origins") or [self.server_url] if o]
        except (OSError, ValueError):
            pass
        try:
            st = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.agent_id, self.token, self.name = st.get("agent_id", ""), st.get("token", ""), st.get("name", "")
        except (OSError, ValueError):
            pass

    @property
    def configured(self) -> bool:
        return bool(self.server_url)

    @property
    def paired(self) -> bool:
        return bool(self.agent_id and self.token)

    def save_pairing(self, agent_id: str, token: str, name: str) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"agent_id": agent_id, "token": token, "name": name}), encoding="utf-8")
        tmp.replace(self.state_path)
        self.agent_id, self.token, self.name = agent_id, token, name

    def clear_pairing(self) -> None:
        self.state_path.unlink(missing_ok=True)
        self.agent_id = self.token = self.name = ""

    def origin_allowed(self, origin: str | None) -> bool:
        return bool(origin) and normalize_origin(origin) in self.allowed_origins
