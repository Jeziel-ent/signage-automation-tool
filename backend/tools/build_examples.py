"""Phase 2: build backend/brand_data/<brand>/examples.json from a brand's
master + its real designer resizes, using already-cached shape dumps (no
CorelDRAW needed here - see cache_ours_dumps.py/validate_all.py for how
those dumps get made).

Usage:
    python build_examples.py dalmia
    python build_examples.py dalmia --exclude "14 - 120"   # leave-one-out
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.example_engine import build_examples, master_entities, save_examples  # noqa: E402
from app.layout import Obj  # noqa: E402
from tools.validate_all import BRAND_MASTER, DATASET, REAL_DUMPS_CACHE_ROOT, _flatten_leaves, _safe  # noqa: E402
from app.batch_import import parse_shop_lines  # noqa: E402

UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "ft": 304.8, "m": 1000.0}


def _leaf_objs(dump: dict) -> list[Obj]:
    return [Obj(str(i), s["name"], s["type"], s["x"], s["y"], s["w"], s["h"], s.get("text"))
            for i, s in enumerate(_flatten_leaves(dump))]


def build(brand: str, exclude: str | None = None) -> dict:
    cfg = BRAND_MASTER[brand]
    shopname_hints = [cfg["shop_name"], cfg["shop_name_local"]]
    brand_dir = DATASET / ("Agarpathi" if brand == "agarpathi" else brand)
    master_path = brand_dir / cfg["file"]

    master_dump_path = REAL_DUMPS_CACHE_ROOT / brand / f"{_safe(master_path.stem)}.json"
    if not master_dump_path.exists():
        raise SystemExit(f"master dump not cached at {master_dump_path} - run validate_all.py {brand} first")
    master_dump = json.loads(master_dump_path.read_text(encoding="utf-8"))
    master_w, master_h = master_dump["page_mm"]["w"], master_dump["page_mm"]["h"]
    m_ents = master_entities(_leaf_objs(master_dump), master_w, master_h, shopname_hints)

    boards = []
    for f in sorted(brand_dir.glob("*.cdr")):
        if "copy" in f.stem.lower() or f.name == master_path.name:
            continue
        if exclude and exclude.lower() in f.stem.lower():
            continue
        parsed = parse_shop_lines(f.stem)
        if not parsed.shops:
            continue
        shop_spec = parsed.shops[0]
        cache_path = REAL_DUMPS_CACHE_ROOT / brand / f"{_safe(f.stem)}.json"
        if not cache_path.exists():
            print(f"skip (no cached dump): {f.name}")
            continue
        dump = json.loads(cache_path.read_text(encoding="utf-8"))
        board_w = shop_spec.width * UNIT_MM[shop_spec.unit]
        board_h = shop_spec.height * UNIT_MM[shop_spec.unit]
        b_ents = master_entities(_leaf_objs(dump), board_w, board_h, shopname_hints)
        boards.append((board_w, board_h, b_ents))

    examples = build_examples(m_ents, master_w, master_h, boards)
    return examples


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brand")
    ap.add_argument("--exclude", help="skip any source filename containing this substring (leave-one-out)")
    ap.add_argument("--out", help="write examples.json here instead of brand_data/<brand>/ (for leave-one-out runs)")
    args = ap.parse_args()
    examples = build(args.brand, args.exclude)
    if args.out:
        Path(args.out).write_text(json.dumps(examples, indent=2), encoding="utf-8")
        print(f"{len(examples['boards'])} example board(s) -> {args.out}")
    else:
        path = save_examples(args.brand, examples)
        print(f"{len(examples['boards'])} example board(s) -> {path}")


if __name__ == "__main__":
    main()
