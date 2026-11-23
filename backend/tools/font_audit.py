"""Step 0 of the fidelity plan: which fonts do the cached boards' text objects use, and does this PC have them?

Read-only. Walks every cached editor scene (`<SIGNAGE_DATA>/jobs_v2/*/out/*/scene/scene.json`), counts text objects per
font family and reports, per family, whether `fonts.installed_fonts()` lists it and whether it has Tamil glyphs.
Usage: python tools/font_audit.py [data_dir]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import fonts  # noqa: E402


def walk(nodes):
    for n in nodes:
        yield n
        yield from walk(n.get("children") or [])


def collect(data_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"texts": 0, "boards": set(), "sample": ""})
    for scene_file in sorted(data_dir.glob("jobs_v2/*/out/*/scene/scene.json")):
        try:
            scene = json.loads(scene_file.read_text(encoding="utf8"))
        except (OSError, ValueError):
            continue
        board = f"{scene_file.parents[2].name}/{scene_file.parents[1].name}"
        for layer in scene.get("layers", []):
            for n in walk(layer.get("children") or []):
                t = n.get("text")
                if not t:
                    continue
                fam = t.get("font") or "(mixed/unknown)"
                row = out[fam]
                row["texts"] += 1
                row["boards"].add(board)
                if not row["sample"] and t.get("content"):
                    row["sample"] = str(t["content"]).replace("\r", " ")[:30]
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    data = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data"
    rows = collect(data)
    inst = fonts.installed_fonts()
    have = {f.lower() for f in inst.get("fonts", [])} if inst.get("available") else None
    print(f"{len(rows)} font families in cached boards ({data})")
    print(f"{'family':36} {'texts':>5} {'boards':>6}  installed  sample")
    for fam, r in sorted(rows.items(), key=lambda kv: -kv[1]["texts"]):
        ok = "?" if have is None else ("yes" if fam.lower() in have else "NO")
        print(f"{fam[:36]:36} {r['texts']:5d} {len(r['boards']):6d}  {ok:9}  {r['sample']}")


if __name__ == "__main__":
    main()
