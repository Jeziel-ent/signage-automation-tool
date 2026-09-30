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


# ------------------------------------------------------------- font files
#
# The editor draws edited text itself (frontend editor/LiveText.jsx), so a browser on ANOTHER machine needs the board's fonts too.
# `GET /api/fonts/file?family=...` serves the installed file for a family, found through the registry's font list (value name ->
# file). Only .ttf/.otf/.ttc files from the Windows fonts folders the registry points at are ever served, never an arbitrary path.

FONT_EXTS = (".ttf", ".otf", ".ttc")
_STYLE_WORDS = {"regular", "normal", "book", "roman"}


def resolve_font_file(family: str, entries: list[tuple[str, str]], fonts_dir: str) -> str | None:
    """Pick the file for `family` from registry `entries` [(value name, data)], data being a file name (in `fonts_dir`) or a full path.

    Exact family match first ("Arial" -> "Arial (TrueType)"), then the family plus a plain style word ("Arial Regular"); a bold or
    italic face is never returned for a plain family. Case-insensitive. None if nothing matches or the file type is not a font."""
    want = " ".join((family or "").split()).casefold()
    if not want:
        return None
    exact, plain = None, None
    for value_name, data in entries:
        for name in parse_registry_name(value_name):
            n = " ".join(name.split()).casefold()
            if n == want and exact is None:
                exact = data
            elif n.startswith(want + " ") and n[len(want) + 1:] in _STYLE_WORDS and plain is None:
                plain = data
    data = exact or plain
    if not data or not str(data).lower().endswith(FONT_EXTS):
        return None
    import os

    return data if os.path.isabs(data) else os.path.join(fonts_dir, data)


def _registry_entries() -> tuple[list[tuple[str, str]], list[str]]:
    """(entries, allowed folders): every registered font value, and the folders a served file must live in."""
    import os
    import winreg

    windir = os.environ.get("WINDIR", r"C:\Windows")
    folders = [os.path.join(windir, "Fonts")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        folders.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))  # per-user installs (full paths in HKCU)
    entries: list[tuple[str, str]] = []
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    ):
        try:
            with winreg.OpenKey(hive, path) as key:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    name, data, _ = winreg.EnumValue(key, i)
                    entries.append((name, str(data)))
        except OSError:
            continue
    return entries, folders


def font_file(family: str):
    """Path of the installed font file for `family`, or None (not installed, non-Windows, or not inside a Windows fonts folder)."""
    if sys.platform != "win32":
        return None
    from pathlib import Path

    entries, folders = _registry_entries()
    found = resolve_font_file(family, entries, folders[0])
    if not found:
        return None
    p = Path(found).resolve()
    if not p.is_file() or not any(p.is_relative_to(Path(f).resolve()) for f in folders):
        return None
    return p


# ------------------------------------------------------------- script support (the Shops Queue's font pickers)
#
# A font can print the Tamil shop name only if it maps Unicode Tamil: read from the font file's own character map with
# fontTools (cmap only, lazily). Legacy Tamil fonts (Bamini, TAU-*, TSCu-*) put Tamil glyphs at LATIN code points, so
# they would print the Unicode name as Latin garbage - they are listed separately, not offered as Tamil fonts.

TAMIL_PROBE = (0x0B95, 0x0BBE, 0x0BCD)          # KA, the AA sign and the virama: enough to write any shop name
LEGACY_TAMIL_HINTS = ("bamini", "tau-", "tau_", "tscu", "tam-", "tab-", "sentamil", "kavitha")
UNICODE_TAMIL_HINTS = ("latha", "vijaya", "nirmala", "tamil", "arial unicode")
_script_cache: dict | None = None
_cmap_cache: dict[tuple[str, float], bool | None] = {}


def font_has_tamil(path: str) -> bool | None:
    """True/False from the file's cmap; None when it cannot be read (or fontTools is missing)."""
    import os

    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key in _cmap_cache:
        return _cmap_cache[key]
    result = None
    try:
        from fontTools.ttLib import TTCollection, TTFont

        faces = TTCollection(path, lazy=True).fonts if path.lower().endswith(".ttc") else [TTFont(path, lazy=True)]
        result = any(all(cp in (f.getBestCmap() or {}) for cp in TAMIL_PROBE) for f in faces)
    except Exception:
        result = None
    _cmap_cache[key] = result
    return result


def classify_tamil(families: list[str], file_of) -> tuple[list[str], list[str], str]:
    """(tamil, legacy_tamil, detection) for installed `families`; `file_of(family)` -> font file path or None.
    detection = "cmap" when every decision came from a font file, else "cmap+names"."""
    tamil, legacy, guessed = [], [], False
    for fam in families:
        low = fam.casefold()
        path = file_of(fam)
        has = font_has_tamil(path) if path else None
        if has is None:                          # no readable file (or no fontTools): judge by the family name
            guessed = True
            has = any(h in low for h in UNICODE_TAMIL_HINTS)
        if has:
            tamil.append(fam)
        elif any(h in low for h in LEGACY_TAMIL_HINTS):
            legacy.append(fam)
    return tamil, legacy, ("cmap+names" if guessed else "cmap")


def script_fonts(refresh: bool = False) -> dict:
    """{"available", "all_fonts", "english_fonts", "tamil_fonts", "legacy_tamil_fonts", "tamil_detection", "source"}."""
    global _script_cache
    if _script_cache is not None and not refresh:
        return _script_cache
    base = installed_fonts(refresh)
    families = base["fonts"]
    if not base["available"]:
        _script_cache = {"available": False, "all_fonts": [], "english_fonts": [], "tamil_fonts": [],
                         "legacy_tamil_fonts": [], "tamil_detection": "none", "source": base["source"]}
        return _script_cache
    try:
        entries, folders = _registry_entries()
    except Exception:
        entries, folders = [], [""]

    def file_of(fam):
        return resolve_font_file(fam, entries, folders[0]) if entries else None

    tamil, legacy, detection = classify_tamil(families, file_of)
    _script_cache = {"available": True, "all_fonts": families, "english_fonts": families, "tamil_fonts": tamil,
                     "legacy_tamil_fonts": legacy, "tamil_detection": detection, "source": base["source"]}
    return _script_cache
