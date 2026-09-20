"""Which font families are installed on this machine.

CorelDRAW does not raise when asked to use a font that is not installed - it
silently keeps the old one (verified with "Noto Sans Tamil", see CLAUDE.md
"Shop name replacement") - so the editor asks this module before letting a
text edit name a font. The list comes from GDI's InstalledFontCollection (the
same families CorelDRAW offers), read once via PowerShell and cached; if that
fails, the registry's font list is used instead. Non-Windows hosts return
available=False and the editor skips the check rather than blocking edits.
"""
from __future__ import annotations

import re
import subprocess
import sys

_cache: dict | None = None

_PS = (
    "Add-Type -AssemblyName System.Drawing;"
    "(New-Object System.Drawing.Text.InstalledFontCollection).Families | ForEach-Object { $_.Name }"
)


def _from_powershell() -> list[str]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
        capture_output=True, text=True, timeout=30, encoding="utf-8",
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "powershell failed")
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def parse_registry_name(value_name: str) -> list[str]:
    """'Yu Gothic Medium & Yu Gothic UI Semibold (TrueType)' -> both names, style suffix removed."""
    base = re.sub(r"\s*\((TrueType|OpenType|All res|VGA res)\)\s*$", "", value_name)
    return [p.strip() for p in base.split(" & ") if p.strip()]


def _from_registry() -> list[str]:
    import winreg

    names: set[str] = set()
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    ):
        try:
            with winreg.OpenKey(hive, path) as key:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    names.update(parse_registry_name(winreg.EnumValue(key, i)[0]))
        except OSError:
            continue
    return sorted(names)


def installed_fonts(refresh: bool = False) -> dict:
    """{"available": bool, "fonts": [family, ...], "source": str}"""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    if sys.platform != "win32":
        _cache = {"available": False, "fonts": [], "source": "unsupported platform"}
        return _cache
    try:
        fonts, source = _from_powershell(), "InstalledFontCollection"
    except Exception:
        try:
            fonts, source = _from_registry(), "registry"
        except Exception:
            _cache = {"available": False, "fonts": [], "source": "unreadable"}
            return _cache
    _cache = {"available": True, "fonts": sorted(set(fonts), key=str.casefold), "source": source}
    return _cache
