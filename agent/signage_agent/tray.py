"""The system-tray icon: the only visible sign of the agent (it has no window). Shows whether it is connected and lets the designer open the
website, check for an update, quit, or remove the agent from the PC. Needs pystray + Pillow; without them the agent just runs without an icon."""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from .config import APP_DIR_NAME, install_dir
from .version import AGENT_VERSION

log = logging.getLogger("agent.tray")
DETACHED = 0x00000008 | 0x00000200


def status_text(status: dict) -> str:
    """One line for the menu, from `Agent.status()`."""
    if not status.get("configured"):
        return "Not set up (no config.json next to the agent)"
    if not status.get("paired"):
        return "Not connected - open the website and press Connect this PC"
    if status.get("state") == "busy":
        return "Working on a board..."
    if status.get("connected"):
        corel = status.get("corel") or []
        return "Connected - " + (f"CorelDRAW {corel[0]['major']}" if corel else "CorelDRAW not found on this PC")
    return "Reconnecting to the website..."


def light(status: dict) -> str:
    """Colour of the icon's dot: green connected, amber working / reconnecting, red not connected."""
    if status.get("paired") and status.get("connected"):
        return "amber" if status.get("state") == "busy" else "green"
    return "amber" if status.get("paired") else "red"


COLORS = {"green": (52, 211, 153), "amber": (251, 191, 36), "red": (239, 68, 68)}


def removal_command(folder: Path) -> list[str] | None:
    """The detached command that deletes the agent's folder a few seconds after the agent exits - only ever the agent's own install folder."""
    folder = Path(folder)
    if folder.name != APP_DIR_NAME:
        return None
    return ["cmd", "/c", f'ping -n 4 127.0.0.1 >nul & rmdir /s /q "{folder}"']


def _image(color: str):
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((4, 4, 60, 60), fill=(220, 38, 38, 255))
    d.polygon([(32, 14), (14, 50), (22, 50), (32, 30), (42, 50), (50, 50)], fill=(255, 255, 255, 255))
    d.ellipse((38, 38, 62, 62), fill=COLORS[color] + (255,), outline=(255, 255, 255, 255), width=3)
    return im


def _ask(text: str) -> bool:
    if sys.platform != "win32":
        return False
    return ctypes.windll.user32.MessageBoxW(None, text, "Adinn Signage Agent", 0x04 | 0x20) == 6        # MB_YESNO | MB_ICONQUESTION -> IDYES


def start(agent):
    """Show the icon (in its own thread). Returns the icon, or None when pystray / Pillow are not available."""
    try:
        import pystray
        _image("red")
    except Exception as e:  # noqa: BLE001
        log.info("no tray icon (%s)", e)
        return None

    def quit_agent(icon, _item=None):
        agent.stop()
        icon.stop()
        os._exit(0)

    def remove(icon, _item=None):
        if not _ask("Remove the Signage Agent from this PC?\n\nIt stops, no longer starts with Windows, and its folder is deleted. "
                    "Your boards on the website are not affected."):
            return
        from . import installer

        installer.uninstall()
        cmd = removal_command(install_dir())
        agent.stop()
        icon.stop()
        if cmd:
            subprocess.Popen(cmd, creationflags=DETACHED, close_fds=True)  # noqa: S603
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem(f"Adinn Signage Agent {AGENT_VERSION}", None, enabled=False),
        pystray.MenuItem(lambda item: status_text(agent.status()), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open the website", lambda icon, item: webbrowser.open(agent.config.server_url or "")),
        pystray.MenuItem("Check for updates now", lambda icon, item: threading.Thread(target=agent.maybe_update, daemon=True).start()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit (starts again when you sign in to Windows)", quit_agent),
        pystray.MenuItem("Remove the agent from this PC...", remove),
    )
    icon = pystray.Icon("AdinnSignageAgent", _image(light(agent.status())), "Adinn Signage Agent", menu)

    def keep_light_current():
        last = None
        while True:
            threading.Event().wait(5)
            color = light(agent.status())
            if color != last:
                try:
                    icon.icon = _image(color)
                    icon.title = f"Adinn Signage Agent - {status_text(agent.status())}"
                except Exception:  # noqa: BLE001
                    pass
                last = color

    icon.run_detached()
    threading.Thread(target=keep_light_current, name="tray-light", daemon=True).start()
    return icon
