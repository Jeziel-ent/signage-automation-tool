"""Parse pasted designer filename lines into shop rows.

Real master filenames (and the designer's own naming convention for
per-shop resizes) look like:

    16 - 12 X 4 Feet - Nonlit - AL MADEENA POOJA STORE
    02 - 120 X 48 Inch - 2 Nos Double Side GSB - SRI KAVI STEELS
    66 - 8 X 4 Feet - Nonlit - VASANTHAM ENTERPRISES - Copy

i.e. "<code> - <W> X <H> <unit> - <type> - <shop name>", where the type is
free text (seen: "Nonlit", "GSB", "2 Nos Double Side GSB", even the typo
"Nonlt") and the shop name may itself contain " - " (e.g. a "- Copy"
suffix) or commas, so it's everything after the third segment, not just
the fourth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SEGMENT_SPLIT = re.compile(r"\s+-\s+")  # NOSONAR - bounded, human-entered strings (file names / emails); no ReDoS exposure, rewrite would risk parsing changes
_SIZE_RE = re.compile(r"^([\d.]+)\s*[xX]\s*([\d.]+)\s*([A-Za-z]+)\.?$")

_UNIT_ALIASES = {
    "feet": "ft", "ft": "ft",
    "inch": "in", "inches": "in", "in": "in",
    "mm": "mm", "cm": "cm", "m": "m",
}


@dataclass
class ParsedShop:
    name: str
    width: float
    height: float
    unit: str
    type: str
    line: str


@dataclass
class ParseResult:
    shops: list[ParsedShop] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)  # {"line": ..., "reason": ...}


def parse_shop_lines(text: str) -> ParseResult:
    result = ParseResult()
    for raw_line in text.splitlines():
        line = raw_line.strip().strip(",")
        if not line:
            continue
        shop = _parse_line(line)
        if shop is None:
            result.errors.append({"line": raw_line, "reason": "expected '<code> - <W> X <H> <unit> - <type> - <name>'"})
        else:
            result.shops.append(shop)
    return result


def _parse_line(line: str) -> ParsedShop | None:
    parts = _SEGMENT_SPLIT.split(line)
    if len(parts) < 4:
        return None

    size_match = _SIZE_RE.match(parts[1].strip())
    if not size_match:
        return None
    width_s, height_s, unit_raw = size_match.groups()
    unit = _UNIT_ALIASES.get(unit_raw.strip().lower())
    if unit is None:
        return None

    shop_type = parts[2].strip()
    name = " - ".join(p.strip() for p in parts[3:]).strip()
    if not name:
        return None

    return ParsedShop(
        name=name, width=float(width_s), height=float(height_s), unit=unit, type=shop_type, line=line,
    )


# Windows copy suffixes on a file name: "Sri Sai cafe (1)", "VASANTHAM ENTERPRISES - Copy", "X - Copy (2)"
_COPY_SUFFIX = re.compile(r"(?:\s+-\s+copy(?:\s*\(\d+\))?|\s*\(\d+\))\s*$", re.IGNORECASE)  # NOSONAR - bounded, human-entered strings (file names / emails); no ReDoS exposure, rewrite would risk parsing changes


def shop_name_from_filename(filename: str | None) -> str | None:
    """The shop name inside a designer-style file name ("76 - 36 X 48 Inch - Nonlit - SRI KANNIYAMMAN ... KADAI.cdr"
    -> "SRI KANNIYAMMAN ... KADAI"), or None when the name does not follow that pattern. A trailing ".cdr" and a
    Windows "- Copy" suffix are dropped."""
    t = re.sub(r"\.cdr$", "", (filename or "").strip(), flags=re.IGNORECASE).strip()
    shop = _parse_line(t) if t else None
    if shop is None:
        return None
    return strip_copy_suffix(shop.name) or None


def strip_copy_suffix(name: str | None) -> str:
    """"Sri Sai cafe (1)" -> "Sri Sai cafe": Windows copy suffixes removed, repeatedly ("X (1) - Copy")."""
    name = (name or "").strip()
    while True:
        stripped = _COPY_SUFFIX.sub("", name).strip()
        if stripped == name:
            return name
        name = stripped


def clean_shop_name(name: str | None) -> str:
    """The text to print for a shop: a designer file name used as the shop name (an Excel sheet that lists files) is
    reduced to its shop-name part; anything else is returned stripped."""
    return shop_name_from_filename(name) or (name or "").strip()
