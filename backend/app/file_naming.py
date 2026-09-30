"""The one file-name scheme for every exported shop file (CDR, PDF, PNG/JPG, a shop's ZIP and the members of the
multi-shop ZIPs), matching the designers' own files in signage_dataset/:

    76 - 125 X 48 Inch - Nonlit - SRI KANNIYAMMAN NATTU MARUNTHU KADAI.cdr

`<S.NO> - <WIDTH> X <HEIGHT> <Unit> - <Type> - <SHOP NAME (EN)>`. Mixed units spell both: `10 Feet X 48 Inch`.
"""
from __future__ import annotations

import re

from .batch_import import clean_shop_name

DEFAULT_BOARD_TYPE = "Nonlit"
UNIT_WORDS = {"in": "Inch", "ft": "Feet", "cm": "CM", "mm": "MM"}
# CorelDRAW writes through the Win32 API, so keep the full path well under MAX_PATH (260): the shop output folder is
# ~115 characters on this machine.
MAX_BASE_LEN = 120
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _num(v) -> str:
    """125 -> "125", 10.5 -> "10.5", 10.25 -> "10.25" (no trailing zeros, at most 2 decimals)."""
    try:
        f = round(float(v), 2)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:g}" if abs(f) < 1e15 else str(int(f))


def size_label(width, width_unit, height, height_unit) -> str:
    """`125 X 48 Inch`, or `10 Feet X 48 Inch` when the two units differ."""
    wu = UNIT_WORDS.get(width_unit or "in", width_unit or "")
    hu = UNIT_WORDS.get(height_unit or width_unit or "in", height_unit or "")
    if wu == hu:
        return f"{_num(width)} X {_num(height)} {wu}"
    return f"{_num(width)} {wu} X {_num(height)} {hu}"


def safe_filename(text: str) -> str:
    """Characters Windows refuses in a file name become spaces; runs of spaces collapse; no leading/trailing dots or
    spaces. Letters of any script are kept."""
    t = re.sub(r"\s+", " ", _BAD.sub(" ", text or "")).strip(" .")
    return t


def signage_basename(no, width, width_unit, height, height_unit, board_type, name) -> str:
    """The name without extension. `name` may be an imported designer file name - it is reduced to the shop name."""
    shop = safe_filename(clean_shop_name(name or "")) or "Shop"
    btype = safe_filename(board_type or "") or DEFAULT_BOARD_TYPE
    head = f"{int(no) if str(no).strip().isdigit() else no} - {size_label(width, width_unit, height, height_unit)} - {btype} - "
    base = safe_filename(head + shop)
    if len(base) > MAX_BASE_LEN:
        base = base[:MAX_BASE_LEN].rstrip(" .-")
    return base


def shop_basename(row: dict, no=None) -> str:
    """`signage_basename` for a `shops` table row; `no` is its S.no in the queue (default: the row's seq_no)."""
    return signage_basename(no if no is not None else row.get("seq_no") or 1, row.get("width"), row.get("width_unit"),
                            row.get("height"), row.get("height_unit"), row.get("board_type"), row.get("name"))
