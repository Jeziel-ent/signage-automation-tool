"""Build backend/brand_data/<brand>/library.json (see app/example_layout.py) from COM dumps made with PowerClip contents
(tools/dump_brand_designers.py).
    python tools/build_example_library.py <brand> <dump_cache_dir_name> <master file name fragment> [<master fragment> ...]
e.g.  python tools/build_example_library.py agarpathi agarpathi "76 - 125 X 48" "76 - 36 X 48"
Every dump is expressed against every master; a board belongs to the master it matches best."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import example_layout as X  # noqa: E402
from app.batch_import import parse_shop_lines  # noqa: E402


def shop_name_of(file: str) -> str | None:
    try:
        name = parse_shop_lines(Path(file).stem).shops[0].name
        return re.sub(r"\s*\(\d+\)\s*$", "", name)          # "Sri Sai cafe (1)": the (1) / (2) marks the double-sided variant
    except Exception:
        return None


def master_name(dump: dict, fname: str) -> str | None:
    """The master's shop name: from a designer-style file name, else the biggest English text on it that is not a stamp
    (a master named just '6 X 3.cdr' still carries its sample shop name as a text)."""
    n = shop_name_of(fname)
    if n:
        return n
    best = None
    for s in X.top_level(dump):
        t = (s.get("text") or "").strip()
        if s["kind"] != "text" or not t or X.TAMIL.search(t) or re.search(r"\d{2}/\d{2}", t):
            continue
        if best is None or s["w"] * s["h"] > best[0]:
            best = (s["w"] * s["h"], t)
    return " ".join(best[1].split()) if best else None


def name_indexes(dump: dict, name: str | None) -> set[int]:
    norm = lambda t: re.sub(r"[^A-Z0-9]", "", (t or "").upper())
    top = X.top_level(dump)
    idx = set()
    for i, s in enumerate(top):
        if s["kind"] != "text":
            continue
        if X.TAMIL.search(s.get("text") or "") or (name and norm(name) in norm(s.get("text"))):
            idx.add(i)
    return idx


def main():
    brand, cache = sys.argv[1], sys.argv[2]
    frags = sys.argv[3:]
    dumps = {}
    for f in sorted((ROOT / "dataset_analysis" / "real_dumps_cache" / cache).glob("*.json")):
        if f.name.startswith("_"):
            continue
        d = json.load(open(f, encoding="utf-8"))
        dumps[d["file"]] = d
    masters = []
    for i, frag in enumerate(frags):
        fname = next(k for k in dumps if frag in k)
        d = dumps[fname]
        mname = master_name(d, fname)
        m = X.describe_master(d, name_indexes(d, mname))
        m["id"], m["file"], m["shop_name"] = f"m{i}", fname, mname
        masters.append(m)
        print("master", m["id"], fname, "elements", [(e["kind"], e["aspect"], e["n_desc"]) for e in m["elements"]],
              "composite aspect", m["composite_aspect"])
    boards, orphans = [], []
    for fname, d in dumps.items():
        if "copy" in fname.lower():
            continue
        name = shop_name_of(fname)
        best = None
        for m in masters:
            rec = X.board_record(m, d, name, fname)
            if rec and (best is None or (rec["strict"], rec["coverage"]) > (best[1]["strict"], best[1]["coverage"])):
                best = (m, rec)
        if best is None:
            orphans.append(fname)
            continue
        best[1]["master"] = best[0]["id"]
        best[1]["sno"] = int(re.match(r"\d+", fname).group(0)) if re.match(r"\d+", fname) else None
        boards.append(best[1])
    out = ROOT / "brand_data" / brand
    out.mkdir(parents=True, exist_ok=True)
    keep = {}                                                   # hand-set routing flags survive a rebuild
    if (out / "library.json").exists():
        old = json.load(open(out / "library.json", encoding="utf-8"))
        keep = {k: v for k, v in old.items() if k not in ("brand", "masters", "boards")}
    (out / "library.json").write_text(json.dumps({"brand": brand, **keep, "masters": masters, "boards": boards}, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print(f"{len(boards)} boards in the library, {len(orphans)} not built from these masters: {orphans[:6]}")
    by_m = {}
    for b in boards:
        by_m[b["master"]] = by_m.get(b["master"], 0) + 1
    print("boards per master:", by_m)


if __name__ == "__main__":
    main()
