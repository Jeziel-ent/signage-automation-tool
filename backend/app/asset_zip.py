"""`Signage_Assets_Export.zip` for the Shops queue's "Download ZIP" button: every selected converted shop as

    01 - 125 X 48 Inch - Nonlit - SHOP NAME.jpg        (root: an image of the board)
    CDR&PDF/cdr/01 - 125 X 48 Inch - Nonlit - SHOP NAME.cdr
    CDR&PDF/pdf/01 - 125 X 48 Inch - Nonlit - SHOP NAME.pdf

(the standard name from file_naming.py when the caller gives `stem`; else `01_SHOP_NAME`)

Which file stands for a shop, per format (`pick_sources`): the newest finished editor export that has that format AND was
made from the edits the shop has now (its `ops` equal the saved op list) - so a board edited and exported in the editor
ships with its edits -, else the conversion's own file. When a shop has saved edits but no matching export, the conversion
file is used and a note says the edits are not in it (the conversion never contains editor edits).

The JPG: an editor export's JPEG as is, or its PNG converted to JPEG; else the conversion's preview PNG converted to JPEG.
Every pixel is CorelDRAW's render, but the conversion preview is a preview: CorelEngine aims at 1600 px on the long side
and clamps to 36-150 dpi (`PREVIEW_MAX_PX`), so a 120 in board comes out 4320 px wide at 36 dpi. A print-resolution JPG
needs an editor export (row Download -> JPG), which this then picks up. Conversion is JPEG quality 95, white under
transparency. An SVG preview (MockEngine) cannot become a JPG and is reported missing.
"""
from __future__ import annotations

import json
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

RASTER = (".png", ".jpg", ".jpeg")
ZIP_NAME = "Signage_Assets_Export.zip"
FILES_DIR = "CDR&PDF"                   # the parent folder of the cdr/ and pdf/ subfolders


@dataclass
class ShopAssets:
    no: int | str
    name: str
    cdr: Path | None = None
    pdf: Path | None = None
    image: Path | None = None            # a PNG or JPEG to become the root .jpg
    notes: list[str] = field(default_factory=list)
    stem: str | None = None              # "01 - 125 X 48 Inch - Nonlit - SHOP" (file_naming.shop_basename)

    @property
    def base(self) -> str:
        return self.stem or member_base(self.no, self.name)


def member_base(no: int | str, name: str) -> str:
    """`01_FAYAZ_HARDWREAS_COLACHAL`: the queue number, then the shop name with every run of other characters as one
    underscore. Letters of any script are kept WITH their combining marks - the regex class `\\w` drops Tamil vowel signs and the
    virama (U+0BCD), which mangled "அல்" into "அல" - and case is kept as typed."""
    from .file_naming import sno_text
    return f"{sno_text(no, 2) or '01'}_{safe_part(name) or 'Shop'}"


def safe_part(text: str) -> str:
    """Letters (with combining marks) and digits of any script; every other run becomes one underscore."""
    out, gap = [], False
    for ch in (text or "").strip():
        if unicodedata.category(ch)[0] in "LMN":
            if gap and out:
                out.append("_")
            out.append(ch)
            gap = False
        else:
            gap = True
    return "".join(out)


def pick_sources(out_dir: Path, conversion_files: dict | None, exports: list[dict], current_ops: list) -> dict:
    """{"cdr", "pdf", "image": Path | None, "notes": [...]} for one shop.

    `exports`: newest first, each {"id", "status", "files": {kind: filename}, "ops": [...]}; files live in
    `out_dir/exports/<id>/`. `conversion_files`: the shop's own {"cdr", "pdf", "preview", ...} in `out_dir`."""
    out_dir = Path(out_dir)
    matching = [e for e in exports if e.get("status") == "done" and e.get("files") and e.get("ops") == current_ops]

    def from_exports(*kinds):
        for e in matching:
            for kind in kinds:
                name = e["files"].get(kind)
                if name:
                    p = out_dir / "exports" / e["id"] / name
                    if p.is_file():
                        return p
        return None

    def from_conversion(kind, exts=None):
        name = (conversion_files or {}).get(kind)
        p = out_dir / name if name else None
        if p is None or not p.is_file() or (exts and p.suffix.lower() not in exts):
            return None
        return p

    notes: list[str] = []
    picked = {
        "cdr": from_exports("cdr") or from_conversion("cdr"),
        "pdf": from_exports("pdf") or from_conversion("pdf"),
        "image": from_exports("jpeg", "png") or from_conversion("preview", RASTER),
    }
    if current_ops:
        stale = [k for k in ("cdr", "pdf", "image") if picked[k] is not None and "exports" not in picked[k].parts]
        if stale:
            notes.append(f"has editor edits that were not exported, so its {', '.join('jpg' if k == 'image' else k for k in stale)} "
                         "is the conversion without them")
    return {**picked, "notes": notes}


def write_zip(shops: list[ShopAssets], out_path: Path) -> dict:
    """Write the archive; returns {"shops": n, "files": n, "missing": ["01_X.pdf", ...], "notes": [...]}.
    Every member is STORED (already compressed - a CDR is itself a zip, CorelDRAW's PDFs and the JPGs are compressed)."""
    from PIL import Image                        # lazily, like every other Pillow use in the app

    Image.MAX_IMAGE_PIXELS = None
    missing, notes, count = [], [], 0
    used: set[str] = set()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for s in shops:
            base = s.base
            n = 2
            while base in used:                  # two shops with the same number and name
                base = f"{s.base} ({n})" if s.stem else f"{s.base}_{n}"
                n += 1
            used.add(base)
            notes += [f"{base}: {t}" for t in s.notes]
            if s.image is not None:
                if s.image.suffix.lower() in (".jpg", ".jpeg"):
                    z.write(s.image, f"{base}.jpg")
                else:
                    with Image.open(s.image) as im, z.open(f"{base}.jpg", "w", force_zip64=True) as dst:
                        rgb = im.convert("RGBA")
                        flat = Image.new("RGB", rgb.size, (255, 255, 255))
                        flat.paste(rgb, mask=rgb.split()[-1])
                        flat.save(dst, "JPEG", quality=95, subsampling=0, dpi=im.info.get("dpi", (72, 72)))
                count += 1
            else:
                missing.append(f"{base}.jpg")
            # PDFs are STORED too: CorelDRAW's PDFs are already compressed - measured on a real 30 MB board PDF, deflating
            # took 2.17 s to save 1 % (stored: 0.04 s), i.e. ~2 minutes of a 50-shop ZIP for nothing
            for kind, path, mode in (("cdr", s.cdr, zipfile.ZIP_STORED), ("pdf", s.pdf, zipfile.ZIP_STORED)):
                if path is not None:
                    z.write(path, f"{FILES_DIR}/{kind}/{base}.{kind}", compress_type=mode)
                    count += 1
                else:
                    missing.append(f"{FILES_DIR}/{kind}/{base}.{kind}")
    return {"shops": len(shops), "files": count, "missing": missing, "notes": notes}


def export_record(row: dict) -> dict:
    """An `exports` table row as pick_sources wants it."""
    return {"id": row["id"], "status": row["status"],
            "files": json.loads(row["files_json"]) if row.get("files_json") else {},
            "ops": json.loads(row["ops_json"]) if row.get("ops_json") else []}


def write_cdr_zip(shops: list[ShopAssets], out_path: Path) -> dict:
    """"Download All CDRs": just the CDR of every shop, at the archive root under its standard name. STORED (a CDR is
    itself a zip). Returns {"shops", "files", "missing": [names]}."""
    missing, count, used = [], 0, set()
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for s in shops:
            base, n = s.base, 2
            while base in used:
                base = f"{s.base} ({n})"
                n += 1
            used.add(base)
            if s.cdr is None:
                missing.append(f"{base}.cdr")
                continue
            z.write(s.cdr, f"{base}.cdr")
            count += 1
    return {"shops": len(shops), "files": count, "missing": missing}


SINGLE_FORMATS = ("cdr", "jpg", "png", "pdf")


def pick_single(out_dir: Path, conversion_files: dict | None, exports: list[dict], current_ops: list, fmt: str) -> dict:
    """The file one shop's row "Download" gives for `fmt` (cdr / jpg / png / pdf) - the same preference as the ZIP
    (`pick_sources`): the newest finished editor export made from the shop's CURRENT edits, else the conversion's own file.

    Returns {"path": Path | None, "to_jpeg": bool (a PNG to re-encode as JPEG), "source": "editor export" | "conversion" |
    None, "note": str | None, "reason": str | None (why it is unavailable)}."""
    out_dir = Path(out_dir)
    matching = [e for e in exports if e.get("status") == "done" and e.get("files") and e.get("ops") == current_ops]

    def exported(kind):
        for e in matching:
            name = e["files"].get(kind)
            if name and (out_dir / "exports" / e["id"] / name).is_file():
                return out_dir / "exports" / e["id"] / name
        return None

    def converted(kind, exts=None):
        name = (conversion_files or {}).get(kind)
        p = out_dir / name if name else None
        return p if p is not None and p.is_file() and (not exts or p.suffix.lower() in exts) else None

    path, to_jpeg = None, False
    if fmt in ("cdr", "pdf"):
        path = exported(fmt) or converted(fmt)
    elif fmt == "png":
        path = exported("png") or converted("preview", (".png",))
    elif fmt == "jpg":
        path = exported("jpeg")
        if path is None:
            path = exported("png") or converted("preview", (".png",))
            to_jpeg = path is not None
    else:
        raise ValueError(f"unknown format {fmt!r}")
    if path is None:
        reason = {"pdf": "no PDF was made for this board", "cdr": "no CDR was made for this board"}.get(
            fmt, "no raster image of this board exists (the preview is not a PNG)")
        return {"path": None, "to_jpeg": False, "source": None, "note": None, "reason": reason}
    source = "editor export" if "exports" in path.parts else "conversion"
    notes = []
    if source == "conversion" and current_ops:
        notes.append("this board has editor edits that were never exported - this file is the conversion without them")
    if source == "conversion" and fmt in ("png", "jpg"):
        notes.append("preview resolution (about 1600 px) - use More export options for a print-resolution image")
    return {"path": path, "to_jpeg": to_jpeg, "source": source, "note": " · ".join(notes) or None, "reason": None}
