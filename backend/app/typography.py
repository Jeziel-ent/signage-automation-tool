"""Brand typography: which fonts a brand's designers use, whether this machine has them, and what the engine actually put on a board.

Brand-neutral: the data lives in `brand_data/<brand>/typography.json` (Hangyo: Anek Tamil + Amaranth, from the Stage 1 audit); a brand without the file behaves exactly
as before (`layout.TAMIL_FONT` for Tamil text whose font cannot draw it). Nothing here installs or distributes a font.

`SIGNAGE_TYPOGRAPHY_V2=0` switches the whole feature off (the resolver that recognises a family registered through one of its named instances, the brand's
preferred Tamil font, the font report) - used to reproduce the pre-Stage-2 behaviour for evaluation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "brand_data"
_cache: dict[str, dict | None] = {}


def enabled() -> bool:
    return os.environ.get("SIGNAGE_TYPOGRAPHY_V2", "1").strip().lower() not in ("0", "false", "no", "off")


def load(brand: str | None) -> dict | None:
    key = (brand or "").strip().lower()
    if not key:
        return None
    if key not in _cache:
        p = DATA / key / "typography.json"
        try:
            _cache[key] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
        except (OSError, ValueError):
            _cache[key] = None
    return _cache[key]


def installed_families() -> set[str]:
    from . import fonts

    return {f.casefold() for f in fonts.installed_fonts().get("fonts", [])}


def is_installed(family: str | None) -> bool:
    from . import fonts

    if not family:
        return False
    key = family.strip().casefold()
    return key in installed_families() or bool(fonts.family_files(family))


def tamil_font(brand: str | None) -> str | None:
    """The brand's Tamil font - only when it is installed here AND its file really maps Unicode Tamil; else None (the legacy default applies)."""
    if not enabled():
        return None
    spec = (load(brand) or {}).get("tamil") or {}
    fam = spec.get("family")
    if not fam or not spec.get("enforce_when_master_font_cannot_draw_tamil", True):
        return None
    from . import corel_util

    return fam if is_installed(fam) and corel_util.font_renders_tamil(fam) else None


def font_status(brand: str | None, used: dict[str, int]) -> dict:
    """Report for a finished board: every font family on its text shapes with installed / Tamil-capable flags, the brand's intended fonts and whether this
    board can be called typographically designer-equivalent. `used` = {family: number of text shapes}."""
    from . import corel_util

    spec = load(brand) or {}
    rows = {}
    for fam, n in sorted(used.items()):
        real = fam not in ("(mixed)", "", None)
        rows[fam] = {"text_shapes": n, "installed": bool(is_installed(fam)) if real else None,
                     "draws_tamil": corel_util.font_renders_tamil(fam) if real else None}
    intended = {k: (spec.get(k) or {}).get("family") for k in ("tamil", "english") if (spec.get(k) or {}).get("family")}
    missing = [f for f, r in rows.items() if r["installed"] is False]
    absent = [f"{k}: the brand's font {fam!r} is on no text shape" for k, fam in intended.items() if k == "tamil" and fam not in used and any(r["draws_tamil"] for r in rows.values())]
    return {"fonts_used": rows, "brand_intended": intended, "missing_on_this_machine": missing, "intended_font_not_used": absent,
            "designer_equivalent": bool(intended and not missing and not absent),
            "note": ("a missing font is substituted by CorelDRAW; COM reports the REQUESTED name, not the face actually drawn - treat the render as NOT designer-equivalent"
                     if missing else ("" if intended else "no brand typography configured"))}
