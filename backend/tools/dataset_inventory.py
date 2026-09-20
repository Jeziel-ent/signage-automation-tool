"""Read-only dataset inventory for signage_dataset/{dalmia,Agarpathi}.

Never opens CorelDRAW - no COM, no live app, no engine call (compute_layout,
brand rules, validate_all's matching, etc. are all untouched here; this is
pure filename/zip/filesystem inspection). Never writes to signage_dataset/;
every output goes under backend/dataset_analysis/{previews,inventory}/.

For each file: parses shop name/width/height/unit from the filename via the
existing `batch_import.parse_shop_lines` (pure text parsing, no engine
logic), converts to mm via `layout.to_mm` (pure arithmetic), computes
orientation and aspect ratio, records the file size, and extracts the
`.cdr`'s own embedded `previews/page1.png` (a `.cdr` from CorelDRAW X4+ is a
zip archive with a ready-made preview inside - see CLAUDE.md - no rendering
needed) into `dataset_analysis/previews/<brand>/`.

Usage:
    python tools/dataset_inventory.py            # both brands
    python tools/dataset_inventory.py dalmia
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.batch_import import parse_shop_lines  # noqa: E402
from app.layout import to_mm  # noqa: E402
from tools.validate_all import BRAND_MASTER  # noqa: E402

DATASET = ROOT.parent / "signage_dataset"
PREVIEW_ROOT = ROOT / "dataset_analysis" / "previews"
INVENTORY_ROOT = ROOT / "dataset_analysis" / "inventory"

BRAND_DIRS = {"dalmia": "dalmia", "agarpathi": "Agarpathi"}

# How close a file's aspect ratio must be to the master's own to count as
# "in range" for Agarpathi's inside/outside summary (there is no validated
# sample set for this brand yet - see CLAUDE.md "No engine work on Agarpathi
# yet"). Chosen to match `layout.TILE_ASPECT_THRESHOLD` (1.4) for
# consistency with how the rest of this codebase already draws this line,
# WITHOUT importing or calling anything from the layout engine itself - a
# plain ratio-of-ratios comparison, computed independently here.
ASPECT_RANGE_FACTOR = 1.4

ORIENTATION_TOLERANCE = 0.005  # 0.5% - treats a file within this of w==h as "square"


def _safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "x"


def extract_preview(cdr_path: Path, out_path: Path) -> str | None:
    """Mirrors app/main.py's `_extract_cdr_preview` (same zip-based approach,
    reused here read-only against the dataset instead of an upload). Returns
    an error string on failure/no-preview, or None on success.
    """
    try:
        with zipfile.ZipFile(cdr_path) as z:
            names = z.namelist()
            candidate = next((n for n in names if n.lower() == "previews/page1.png"), None) \
                or next((n for n in names if n.lower().endswith("page1.png")), None) \
                or next((n for n in names if "preview" in n.lower() and n.lower().endswith(".png")), None)
            if not candidate:
                return "no preview image found inside this .cdr"
            data = z.read(candidate)
    except zipfile.BadZipFile:
        return "not a zip-based .cdr (pre-X4 format?) - no instant preview available"
    except Exception as e:
        return f"could not read preview: {e}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return None


def orientation_of(w_mm: float, h_mm: float) -> str:
    if abs(w_mm - h_mm) <= ORIENTATION_TOLERANCE * max(w_mm, h_mm):
        return "square"
    return "landscape" if w_mm > h_mm else "portrait"


def inventory_brand(brand: str) -> dict:
    brand_dir = DATASET / BRAND_DIRS[brand]
    master_file = BRAND_MASTER[brand]["file"]
    files = sorted(brand_dir.glob("*.cdr"))

    rows = []
    for f in files:
        parsed = parse_shop_lines(f.stem)
        size_mb = f.stat().st_size / 1_000_000  # decimal MB
        safe = _safe_name(f.stem)
        preview_rel = f"{brand}/{safe}.png"
        preview_err = extract_preview(f, PREVIEW_ROOT / brand / f"{safe}.png")

        row = {
            "file": f.name, "is_master": f.name == master_file,
            "size_mb": round(size_mb, 2),
            "preview": None if preview_err else preview_rel,
            "preview_error": preview_err,
        }
        if not parsed.shops:
            row.update({"parse_error": (parsed.errors[0]["reason"] if parsed.errors else "unparsable filename")})
            rows.append(row)
            continue
        shop = parsed.shops[0]
        w_mm, h_mm = to_mm(shop.width, shop.unit), to_mm(shop.height, shop.unit)
        row.update({
            "shop_name": shop.name, "width_raw": shop.width, "height_raw": shop.height, "unit": shop.unit,
            "w_mm": round(w_mm, 1), "h_mm": round(h_mm, 1),
            "orientation": orientation_of(w_mm, h_mm),
            "aspect_ratio": round(max(w_mm, h_mm) / min(w_mm, h_mm), 3),
        })
        rows.append(row)

    master_rows = [r for r in rows if r["is_master"]]
    master_aspect = master_rows[0]["aspect_ratio"] if master_rows and "aspect_ratio" in master_rows[0] else None

    parsed_rows = [r for r in rows if "aspect_ratio" in r]
    size_key = lambda r: (r["w_mm"], r["h_mm"])  # noqa: E731
    sizes: dict[tuple, list[str]] = {}
    for r in parsed_rows:
        sizes.setdefault(size_key(r), []).append(r["file"])

    # Only requested for Agarpathi - dalmia already has a real, engine-validated
    # aspect-ratio range from Steps 1-5 (see CLAUDE.md), which this simple
    # ratio-of-ratios heuristic would only muddy.
    if master_aspect and brand == "agarpathi":
        for r in parsed_rows:
            ratio_of_ratios = max(r["aspect_ratio"], master_aspect) / min(r["aspect_ratio"], master_aspect)
            r["vs_master_aspect"] = "in_range" if ratio_of_ratios <= ASPECT_RANGE_FACTOR else "out_of_range"

    return {
        "brand": brand, "master_file": master_file, "master_aspect_ratio": master_aspect,
        "rows": rows,
        "summary": {
            "n_files": len(rows),
            "n_parsed": len(parsed_rows),
            "n_unparsable": len(rows) - len(parsed_rows),
            "unparsable_files": [r["file"] for r in rows if "aspect_ratio" not in r],
            "distinct_sizes": len(sizes),
            "sizes_with_multiple_samples": {
                f"{w:.1f}x{h:.1f}mm": names for (w, h), names in sizes.items() if len(names) > 1
            },
            "portrait_or_square": [
                {"file": r["file"], "orientation": r["orientation"]} for r in parsed_rows
                if r["orientation"] in ("portrait", "square")
            ],
        },
    }


def _render_table_md(brand_data: dict) -> str:
    lines = [
        f"# Dataset inventory: {brand_data['brand']}",
        "",
        f"Master: `{brand_data['master_file']}` (aspect ratio {brand_data['master_aspect_ratio']})",
        "",
        "| File | W x H (mm) | Orientation | Aspect | MB | Master | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in brand_data["rows"]:
        if "aspect_ratio" not in r:
            lines.append(f"| {r['file']} | - | - | - | {r['size_mb']} | {'MASTER' if r['is_master'] else ''} | unparsable: {r.get('parse_error')} |")
            continue
        lines.append(
            f"| {r['file']} | {r['w_mm']:.1f} x {r['h_mm']:.1f} | {r['orientation']} | {r['aspect_ratio']} | "
            f"{r['size_mb']} | {'MASTER' if r['is_master'] else ''} | {'-' if not r['preview_error'] else 'preview: ' + r['preview_error']} |"
        )
    s = brand_data["summary"]
    lines += [
        "",
        "## Summary",
        f"- Files: {s['n_files']} ({s['n_parsed']} parsed, {s['n_unparsable']} unparsable)",
        f"- Distinct sizes: {s['distinct_sizes']}",
        f"- Sizes with more than one sample: {len(s['sizes_with_multiple_samples']) or 'none'}",
    ]
    for size, names in s["sizes_with_multiple_samples"].items():
        lines.append(f"  - {size}: {', '.join(names)}")
    lines.append(f"- Portrait or square boards: {len(s['portrait_or_square']) or 'none'}")
    for p in s["portrait_or_square"]:
        lines.append(f"  - {p['file']} ({p['orientation']})")
    lines.append(f"- Unparsable filenames: {', '.join(s['unparsable_files']) or 'none'}")
    if brand_data["brand"] == "agarpathi" and brand_data["master_aspect_ratio"]:
        in_range = [r["file"] for r in brand_data["rows"] if r.get("vs_master_aspect") == "in_range" and not r["is_master"]]
        out_range = [r["file"] for r in brand_data["rows"] if r.get("vs_master_aspect") == "out_of_range"]
        lines += [
            f"- vs. master aspect ratio ({brand_data['master_aspect_ratio']}, +/- {ASPECT_RANGE_FACTOR}x - "
            "a plain ratio-of-ratios check defined in this script, not an engine/tiling decision):",
            f"  - in range: {len(in_range)}",
            f"  - out of range: {len(out_range)} - {', '.join(out_range) if out_range else 'none'}",
        ]
    return "\n".join(lines) + "\n"


def _render_contact_sheet(brand_data: dict) -> str:
    def esc(s):
        return html.escape(str(s)) if s is not None else "-"

    rows = sorted(brand_data["rows"], key=lambda r: (not r["is_master"], r["file"]))
    cards = []
    for r in rows:
        if r["preview"]:
            img = f'<img src="../previews/{esc(r["preview"])}" loading="lazy">'
        else:
            img = f'<div class="missing">no preview<br>{esc(r.get("preview_error"))}</div>'
        if "aspect_ratio" in r:
            caption = f"{r['w_mm']:.0f} x {r['h_mm']:.0f} mm &middot; {r['orientation']} &middot; aspect {r['aspect_ratio']} &middot; {r['size_mb']}MB"
        else:
            caption = f"unparsable name &middot; {r['size_mb']}MB"
        cards.append(f"""
        <figure class="{'master' if r['is_master'] else ''}">
          {img}
          <figcaption><b>{esc(r['file'])}</b>{' (MASTER)' if r['is_master'] else ''}<br>{caption}</figcaption>
        </figure>""")

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>{esc(brand_data['brand'])} contact sheet</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #1a1a1e; color: #eee; margin: 0; padding: 24px; }}
  h1 {{ font-weight: 600; }}
  .grid {{ display: flex; flex-wrap: wrap; gap: 16px; }}
  figure {{ margin: 0; background: #24242a; border-radius: 8px; padding: 10px; width: 240px; border: 1px solid #333; }}
  figure.master {{ border-color: #3ecf5f; }}
  figure img {{ max-width: 100%; max-height: 140px; display: block; margin: 0 auto 8px; background: repeating-conic-gradient(#333 0% 25%, #222 0% 50%) 50% / 12px 12px; }}
  .missing {{ height: 100px; display: flex; align-items: center; justify-content: center; text-align: center; color: #a66; font-size: 11px; margin-bottom: 8px; }}
  figcaption {{ font-size: 11px; color: #bbb; line-height: 1.4; word-break: break-word; }}
</style></head>
<body>
<h1>{esc(brand_data['brand'])}: dataset contact sheet ({len(rows)} files, master first)</h1>
<div class="grid">{''.join(cards)}</div>
</body></html>
"""


def run(brand: str) -> dict:
    data = inventory_brand(brand)
    out_dir = INVENTORY_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{brand}_inventory.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / f"{brand}_inventory.md").write_text(_render_table_md(data), encoding="utf-8")
    (out_dir / f"{brand}_contact_sheet.html").write_text(_render_contact_sheet(data), encoding="utf-8")
    print(_render_table_md(data))
    print(f"-> {out_dir / f'{brand}_inventory.md'}, .json, and {out_dir / f'{brand}_contact_sheet.html'}")
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brand", nargs="?", choices=list(BRAND_DIRS), default=None)
    args = ap.parse_args()
    for brand in ([args.brand] if args.brand else list(BRAND_DIRS)):
        run(brand)


if __name__ == "__main__":
    main()
