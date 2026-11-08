"""The agent's tiny LOCAL status server, bound to 127.0.0.1 only. It is how the website finds out "is the agent installed and running on THIS
computer?" and how the page hands the agent its pairing code.

* GET  /status   {agent, version, configured, paired, connected, corel: [...], ...}
* POST /pair     {code}      -> registers with the configured server (the code comes from the signed-in website)
* POST /unpair
Safety: only browser pages loaded from `allowed_origins` (config.json: the one server this agent was downloaded from) get CORS access,
the Host header must be 127.0.0.1/localhost (DNS-rebinding), and /pair and /unpair additionally REQUIRE an allowed Origin header (a page
cannot forge it). The Private Network Access preflight header is answered so Chrome lets the https site call 127.0.0.1.
"""
from __future__ import annotations

import json
import logging
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import client
from .agent import Agent

log = logging.getLogger("agent.local")
PORTS = range(47890, 47900)
MAX_BODY = 4096


def make_handler(agent: Agent):
    cfg = agent.config

    class Handler(BaseHTTPRequestHandler):
        server_version = "AdinnSignageAgent"

        def log_message(self, fmt, *args):  # noqa: A003 - keep the console/log quiet
            log.debug(fmt, *args)

        # ---- helpers
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            return host in ("127.0.0.1", "localhost", "[::1]")

        def _cors(self) -> None:
            origin = self.headers.get("Origin")
            if cfg.origin_allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Private-Network", "true")

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(min(n, MAX_BODY)).decode() or "{}") if n else {}
            except (ValueError, UnicodeDecodeError):
                return {}

        # ---- routes
        def do_OPTIONS(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, {"detail": "bad host"})
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, {"detail": "bad host"})
            if self.path.split("?")[0] == "/status":
                return self._send(200, agent.status())
            self._send(404, {"detail": "not found"})

        def do_POST(self):  # noqa: N802
            body = self._read_json()          # always read the body first: replying to an unread request makes Windows reset the connection
            if not self._host_ok():
                return self._send(403, {"detail": "bad host"})
            path = self.path.split("?")[0]
            if path not in ("/pair", "/unpair"):
                return self._send(404, {"detail": "not found"})
            if not cfg.origin_allowed(self.headers.get("Origin")):
                return self._send(403, {"detail": "this page may not pair the agent"})
            if path == "/unpair":
                agent.unpair()
                return self._send(200, {"ok": True})
            code = str(body.get("code", "")).strip()
            if not code:
                return self._send(400, {"detail": "no pairing code"})
            try:
                self._send(200, agent.pair(code))
            except client.ApiError as e:
                self._send(502 if e.status == 0 else e.status, {"detail": e.detail})

    return Handler


class _Server(ThreadingHTTPServer):
    """Takes its port EXCLUSIVELY: on Windows the default SO_REUSEADDR would let a second agent bind the same port too, and the
    browser could then talk to either one."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def start(agent: Agent, ports=PORTS) -> tuple[ThreadingHTTPServer, int] | None:
    """Bind the first free port and serve in a background thread. None when every port is taken."""
    handler = make_handler(agent)
    for port in ports:
        try:
            srv = _Server(("127.0.0.1", port), handler)
        except OSError:
            continue
        threading.Thread(target=srv.serve_forever, name="local-status", daemon=True).start()
        log.info("local status server on 127.0.0.1:%d", port)
        return srv, port
    log.error("no free local port in %s", list(ports))
    return None
