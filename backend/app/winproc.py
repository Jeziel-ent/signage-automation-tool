"""Starting helper programs on Windows without a console window flashing up.

The Signage Agent is a windowed program (PyInstaller --noconsole). When it starts a CONSOLE program (tasklist, taskkill, powershell) without the
CREATE_NO_WINDOW flag, Windows opens a new console window for it for a moment - the black box that appeared and vanished again during every
conversion. Every subprocess call in the app passes `**no_window()`."""
from __future__ import annotations

import sys

CREATE_NO_WINDOW = 0x08000000


def no_window(platform: str | None = None) -> dict:
    """Extra keyword arguments for subprocess.run / Popen: no console window on Windows, nothing elsewhere."""
    return {"creationflags": CREATE_NO_WINDOW} if (platform or sys.platform) == "win32" else {}


def worker_window_kwargs(platform: str | None = None, frozen: bool | None = None) -> dict:
    """For the CorelDRAW worker process: hidden only in the packaged agent. Started from a developer's terminal the worker shares that console,
    so its log lines still show there."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    return no_window(platform) if is_frozen else {}
