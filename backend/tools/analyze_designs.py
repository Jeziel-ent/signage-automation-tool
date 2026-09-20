"""Validate compute_layout's tile rule against a real designer resize.

Takes a master's object dump and a real designer variant's object dump (both
produced by dump_objects.py, read-only, against real CorelDRAW files - see
backend/dataset_analysis/dumps/ and the top-level CLAUDE.md "Designer dataset
analysis" section) and reports how our automatic layout compares to what the
designer actually did, at the variant's real page size. This is offline
(pure Python against the JSON dumps) - no CorelDRAW/COM needed to re-run it.

There's no way to match individual shapes 1:1 (the designer's file may have
a different shape count/order than our tiled output), so this compares
aggregates: object counts and the bounding box of non-background content.

Usage:
    python analyze_designs.py <master_dump.json> <variant_dump.json> \
        [--shop-name NAME] [--shop-name-local NAME] \
        [--master-shop-name OLD] [--master-shop-name-local OLD_LOCAL]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.layout import Obj, compute_layout, detect_role, find_shopname_ids, _tile_plan  # noqa: E402


def _load_top_level_objs(dump_path: Path) -> tuple[dict, list[Obj]]:
    data = json.loads(dump_path.read_text(encoding="utf-8"))
    objs = [
        Obj(str(i), s["name"], s["type"], s["x"], s["y"], s["w"], s["h"], s.get("text"))
        for i, s in enumerate(data["shapes"])
        if not s["group_path"]  # top-level only - matches what CorelEngine enumerates
    ]
    return data, objs


def _content_bbox(items, page_w, page_h) -> tuple[float, float, float, float] | None:
    """Bounding box of everything that isn't a full-page background."""
    kept = [o for o in items if not (o[2] * o[3] / (page_w * page_h) >= 0.9)]
    if not kept:
        return None
    xs0 = [o[0] for o in kept]
    ys0 = [o[1] for o in kept]
    xs1 = [o[0] + o[2] for o in kept]
    ys1 = [o[1] + o[3] for o in kept]
    return min(xs0), min(ys0), max(xs1) - min(xs0), max(ys1) - min(ys0)


def compare(master_path: Path, variant_path: Path, shop_name=None, shop_name_local=None,
            master_shop_name=None, master_shop_name_local=None) -> None:
    master, master_objs = _load_top_level_objs(master_path)
    variant, variant_objs = _load_top_level_objs(variant_path)

    page_w, page_h = master["page_mm"]["w"], master["page_mm"]["h"]
    new_w, new_h = variant["page_mm"]["w"], variant["page_mm"]["h"]

    shopname_ids = find_shopname_ids(master_objs, master_shop_name, master_shop_name_local)
    placed = compute_layout(
        master_objs, page_w, page_h, new_w, new_h, tile=True,
        shop_name=shop_name, shop_name_local=shop_name_local, shopname_ids=shopname_ids,
    )

    axis, n = _tile_plan(page_w, page_h, new_w, new_h)
    our_count = len(placed)
    real_count = len(variant_objs)
    master_count = len(master_objs)

    our_bbox = _content_bbox([(p.x, p.y, p.w, p.h) for p in placed], new_w, new_h)
    real_bbox = _content_bbox([(o.x, o.y, o.w, o.h) for o in variant_objs], new_w, new_h)

    print(f"master: {master_path.name}  page {page_w:.0f}x{page_h:.0f}mm  {master_count} top-level shapes")
    print(f"variant: {variant_path.name}  page {new_w:.0f}x{new_h:.0f}mm  {real_count} top-level shapes")
    print(f"shopname ids matched in master: {shopname_ids or '(none - pass --master-shop-name)'}")
    print()
    print(f"{'metric':<28}{'ours':<22}{'real file':<22}")
    print(f"{'tile axis / count':<28}{f'{axis}, n={n}':<22}{'(see object-count ratio below)':<22}")
    print(f"{'object count':<28}{our_count:<22}{real_count:<22}")
    if master_count > 1:
        approx_real_multiplier = (real_count - 1) / max(master_count - 1, 1)
        print(f"{'implied real tile count':<28}{'':<22}{approx_real_multiplier:.2f}x master's non-bg shapes")
    if our_bbox and real_bbox:
        print(f"{'content bbox w x h (mm)':<28}{f'{our_bbox[2]:.0f} x {our_bbox[3]:.0f}':<22}{f'{real_bbox[2]:.0f} x {real_bbox[3]:.0f}':<22}")
        print(f"{'content coverage (w%,h%)':<28}"
              f"{f'{100*our_bbox[2]/new_w:.0f}%, {100*our_bbox[3]/new_h:.0f}%':<22}"
              f"{f'{100*real_bbox[2]/new_w:.0f}%, {100*real_bbox[3]/new_h:.0f}%':<22}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("master_dump", type=Path)
    ap.add_argument("variant_dump", type=Path)
    ap.add_argument("--shop-name")
    ap.add_argument("--shop-name-local")
    ap.add_argument("--master-shop-name")
    ap.add_argument("--master-shop-name-local")
    args = ap.parse_args()
    compare(args.master_dump, args.variant_dump, args.shop_name, args.shop_name_local,
            args.master_shop_name, args.master_shop_name_local)


if __name__ == "__main__":
    main()
