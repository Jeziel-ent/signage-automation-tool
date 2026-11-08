"""Which CorelDRAW versions this PC has. Read from the registry only - CorelDRAW is never started to find out."""
from __future__ import annotations

import os
import re
import sys

_PROGID = re.compile(r"^CorelDRAW\.Application\.(\d+)$", re.IGNORECASE)


def _file_version(path: str) -> str | None:
    """'27.0.0.121' from the executable's version resource (ctypes, no pywin32), or None."""
    if sys.platform != "win32" or not path:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, buf):
            return None
        info, length = ctypes.c_void_p(), wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(info), ctypes.byref(length)):
            return None
        vals = ctypes.cast(info, ctypes.POINTER(ctypes.c_uint32 * 6)).contents   # FIXEDFILEINFO: signature, struct ver, MS, LS ...
        ms, ls = vals[2], vals[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:  # noqa: BLE001 - a missing version is not an error
        return None


def find_installs() -> list[dict]:
    """[{progid, major, version, exe}] for every registered CorelDRAW.Application.NN whose program file exists, newest first.
    [] on a computer without CorelDRAW (or not Windows)."""
    if sys.platform != "win32":
        return []
    import winreg

    out: list[dict] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "")
    except OSError:
        return []
    with root:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            m = _PROGID.match(name)
            if not m:
                continue
            exe = _server_path(winreg, name)
            if exe and os.path.isfile(exe):
                out.append({"progid": name, "major": int(m.group(1)), "version": _file_version(exe), "exe": exe})
    out.sort(key=lambda e: e["major"], reverse=True)
    return out


def _server_path(winreg, progid: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + "\\CLSID") as k:
            clsid = winreg.QueryValue(k, "")
        for sub in ("LocalServer32", "InprocServer32"):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, f"CLSID\\{clsid}\\{sub}") as k:
                    raw = winreg.QueryValue(k, "")
                m = re.match(r'^\s*"?([^"]+?\.exe)', raw, re.IGNORECASE)
                return m.group(1) if m else raw.strip('" ')
            except OSError:
                continue
    except OSError:
        return None
    return None
