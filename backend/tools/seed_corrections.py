"""Seed the correction memory from a brand's designer files (the engine's output vs the designer's own board of the same size).

    python tools/seed_corrections.py <brand> [--dry-run] [--no-eval]

For every designer file of the brand that `validate_all.py` has regenerated (`dataset_analysis/validation/<brand>/worker_results.json`:
the engine's output dump) and dumped (`real_dumps_cache/<brand>/`: the designer's board), this records where the designer put each
logo unit relative to where the engine put it - the same record the editor writes when a designer fixes a board
(`app/corrections.py`), stored `approved` with `source: "designer-dataset"`.

Logos in these masters are 100+ loose curves, so matching curve to curve would scramble them. Instead whole UNITS are matched: loose
shapes close together form a unit on both sides (`layout.cluster_fragments`, the engine's own rule), the engine's unit is matched to the
designer's unit of similar size nearest to it, and every member of the engine's unit gets the same scale + shift. A unit that already
sits where the designer put it records nothing. Text is never recorded.

Unless --no-eval, it also measures whether this generalises: for each board size that has two or more designer files, one file is held
out, the others' corrections are applied to the engine's output for it, and the distance to the held-out designer's own board is
compared with the engine alone.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import corrections  # noqa: E402
from app.layout import FRAGMENT_GAP_FRAC, Obj, cluster_fragments  # noqa: E402

MAX_UNIT_SIZE_RATIO = 1.6      # a unit matches a designer unit at most this much bigger / smaller on either axis
MAX_UNIT_DISTANCE = 0.2        # ... and whose centre is within this share of the page of it
UNIT_MIN_SHIFT = 0.01         # a unit must differ from the designer's by this share of the page to be worth recording (hand-nudging below it is noise)
BG_AREA = 0.9                  # a shape this large is the background, never a unit
SOURCE = "designer-dataset"


class _P:
    """The few fields of layout.Placed that corrections.apply_to_placed uses."""

    def __init__(self, i, role, x, y, w, h):
        self.id, self.role, self.x, self.y, self.w, self.h, self.warnings = str(i), role, x, y, w, h, []


def _bbox(items):
    x0, y0 = min(i["x"] for i in items), min(i["y"] for i in items)
    x1, y1 = max(i["x"] + i["w"] for i in items), max(i["y"] + i["h"] for i in items)
    return x0, y0, x1 - x0, y1 - y0


def _foreground(shapes: list[dict], pw: float, ph: float) -> list[dict]:
    return [s for s in shapes if s["type"] != "text" and s["w"] > 0 and s["h"] > 0 and s["w"] * s["h"] < BG_AREA * pw * ph]


def units_of(shapes: list[dict], pw: float, ph: float) -> list[list[dict]]:
    """The visual units of a set of shapes: loose neighbours merged (the engine's own clustering)."""
    objs = [Obj(str(i), s.get("name", ""), s["type"], s["x"], s["y"], s["w"], s["h"], None) for i, s in enumerate(shapes)]
    return [[shapes[int(o.id)] for o in c] for c in cluster_fragments(objs, pw, ph, FRAGMENT_GAP_FRAC)]


def ours_objects(dump: dict, pw: float, ph: float) -> list[dict]:
    """What the engine placed: the top-level shapes of its output (the master's own top-level objects), foreground only."""
    return _foreground([s for s in dump["shapes"] if not s.get("group_path")], pw, ph)


def real_objects(dump: dict, pw: float, ph: float) -> list[dict]:
    """The designer's board, as leaves: she groups her logos differently from the master, a leaf's box does not depend on that."""
    return _foreground([s for s in dump["shapes"] if s["type"] != "group"], pw, ph)


def match_units(ours: list[list[dict]], real: list[list[dict]], pw: float, ph: float) -> list[tuple[list[dict], list[dict]]]:
    """Each engine unit (biggest first) with the designer unit nearest to it that is of similar size; a designer unit is used once."""
    free = list(range(len(real)))
    out = []
    for u in sorted(ours, key=lambda u: -(_bbox(u)[2] * _bbox(u)[3])):
        ox, oy, ow, oh = _bbox(u)
        best = None
        for j in free:
            rx, ry, rw, rh = _bbox(real[j])
            if not (1 / MAX_UNIT_SIZE_RATIO <= rw / ow <= MAX_UNIT_SIZE_RATIO and 1 / MAX_UNIT_SIZE_RATIO <= rh / oh <= MAX_UNIT_SIZE_RATIO):
                continue
            dx, dy = abs((rx + rw / 2) - (ox + ow / 2)) / pw, abs((ry + rh / 2) - (oy + oh / 2)) / ph
            if max(dx, dy) <= MAX_UNIT_DISTANCE and (best is None or max(dx, dy) < best[0]):
                best = (max(dx, dy), j)
        if best:
            free.remove(best[1])
            out.append((u, real[best[1]]))
    return out


def _transform(u: list[dict], r: list[dict]):
    ox, oy, ow, oh = _bbox(u)
    rx, ry, rw, rh = _bbox(r)
    sx, sy = rw / ow, rh / oh
    return lambda m: {"x": rx + (m["x"] - ox) * sx, "y": ry + (m["y"] - oy) * sy, "w": m["w"] * sx, "h": m["h"] * sy}


def _frac(b: dict, pw: float, ph: float) -> dict:
    return {"cx": round((b["x"] + b["w"] / 2) / pw, 5), "cy": round((b["y"] + b["h"] / 2) / ph, 5),
            "w": round(b["w"] / pw, 5), "h": round(b["h"] / ph, 5)}


def build_changes(ours_dump: dict, real_dump: dict, pw: float, ph: float) -> tuple[list[dict], dict]:
    """The recorded changes (one per member of every matched unit that is not already where the designer put it) and match statistics."""
    ours = ours_objects(ours_dump, pw, ph)
    ou, ru = units_of(ours, pw, ph), units_of(real_objects(real_dump, pw, ph), pw, ph)
    pairs = match_units(ou, ru, pw, ph)
    changes, moved_units = [], 0
    for u, r in pairs:
        t = _transform(u, r)
        box = dict(zip("xywh", _bbox(u)))
        ub, tb = _frac(box, pw, ph), _frac(t(box), pw, ph)
        if max(abs(ub[k] - tb[k]) for k in ub) < UNIT_MIN_SHIFT:
            continue                                     # the engine already put this unit where the designer did
        moved_units += 1
        for m in u:
            before, after = _frac(m, pw, ph), _frac(t(m), pw, ph)
            action = ("moved+resized" if max(abs(before["w"] - after["w"]), abs(before["h"] - after["h"])) >= corrections.MIN_SHIFT_FRAC
                      else "moved")
            changes.append({"id": m.get("name") or "", "signature": {"kind": m["type"]}, "action": action, "before": before, "after": after})
    stats = {"engine_units": len(ou), "designer_units": len(ru), "matched": len(pairs), "units_changed": moved_units, "changes": len(changes)}
    return changes, stats


def seed_id(brand: str, stem: str) -> str:
    return f"seed:{brand}:{stem}"


def board_record(brand: str, master_file: str, stem: str, board_type: str | None, ours_dump: dict, real_dump: dict,
                 pw: float, ph: float) -> tuple[dict | None, dict]:
    changes, stats = build_changes(ours_dump, real_dump, pw, ph)
    if not changes:
        return None, stats
    sid = seed_id(brand, stem)
    return {"shop_id": sid, "brand": brand, "master_file": master_file, "page_w_mm": round(pw, 3), "page_h_mm": round(ph, 3),
            "board_type": board_type, "status": "approved", "source": SOURCE, "file": stem, "changes": changes, "text_edits": 0,
            "template": None, "confidence": None}, stats


# ------------------------------------------------------------------ does it generalise?

def _unit_errors(objs: list[dict], placed: list[_P], real_units: list[list[dict]], pw: float, ph: float) -> list[float]:
    """Distance (mm, centre) of every placed object from where the designer put it: each matched engine unit is mapped onto its designer
    unit and every member's ideal box compared with where the member is now."""
    ou = units_of(objs, pw, ph)
    by_key = {(o["x"], o["y"], o["w"], o["h"]): p for o, p in zip(objs, placed)}
    errs = []
    for u, r in match_units(ou, real_units, pw, ph):
        t = _transform(u, r)
        for m in u:
            ideal, p = t(m), by_key[(m["x"], m["y"], m["w"], m["h"])]
            errs.append(max(abs((ideal["x"] + ideal["w"] / 2) - (p.x + p.w / 2)), abs((ideal["y"] + ideal["h"] / 2) - (p.y + p.h / 2))))
    return errs


def evaluate(boards: list[dict]) -> list[dict]:
    """Leave-one-out per board size. `boards`: {stem, brand, master_file, board_type, pw, ph, ours_dump, real_dump, record}."""
    rows = []
    for b in boards:
        peers = [x for x in boards if x is not b and x["record"] and x["master_file"] == b["master_file"]
                 and abs(x["pw"] / b["pw"] - 1) < corrections.SAME_SIZE_TOL and abs(x["ph"] / b["ph"] - 1) < corrections.SAME_SIZE_TOL]
        if not peers:
            continue
        objs = ours_objects(b["ours_dump"], b["pw"], b["ph"])
        real_units = units_of(real_objects(b["real_dump"], b["pw"], b["ph"]), b["pw"], b["ph"])
        base = [_P(i, "logo", o["x"], o["y"], o["w"], o["h"]) for i, o in enumerate(objs)]
        learned = [_P(i, "logo", o["x"], o["y"], o["w"], o["h"]) for i, o in enumerate(objs)]
        recs = [dict(p["record"], updated_at=i) for i, p in enumerate(peers)]
        summary = corrections.apply_to_placed(learned, b["pw"], b["ph"], sorted(recs, key=lambda r: -r["updated_at"]))
        e0, e1 = _unit_errors(objs, base, real_units, b["pw"], b["ph"]), _unit_errors(objs, learned, real_units, b["pw"], b["ph"])
        if not e0:
            continue
        rows.append({"file": b["stem"], "peers": len(peers), "applied": summary["applied"], "objects": len(e0),
                     "engine_mean_mm": statistics.mean(e0), "learned_mean_mm": statistics.mean(e1),
                     "engine_median_mm": statistics.median(e0), "learned_median_mm": statistics.median(e1)})
    return rows


# ------------------------------------------------------------------ command line

def _load_boards(brand: str) -> list[dict]:
    from tools.validate_all import BRAND_MASTER, REAL_DUMPS_CACHE_ROOT, VALIDATION_ROOT, _safe  # noqa: E402
    from app.batch_import import parse_shop_lines  # noqa: E402

    results = json.loads((VALIDATION_ROOT / brand / "worker_results.json").read_text(encoding="utf-8"))
    entries = results["results"] if isinstance(results, dict) and "results" in results else results
    out = []
    for e in entries:
        if e.get("status") != "done":
            continue
        stem = Path(e["real_file"]).stem if e.get("real_file") else None
        if stem is None:
            continue
        cache = REAL_DUMPS_CACHE_ROOT / brand / f"{_safe(stem)}.json"
        if not cache.exists():
            continue
        page = e["result"]["report"]["new_page_mm"]
        parsed = parse_shop_lines(stem)
        out.append({"stem": stem, "brand": brand, "master_file": BRAND_MASTER[brand]["file"], "pw": page["w"], "ph": page["h"],
                    "board_type": parsed.shops[0].type if parsed.shops else None, "ours_dump": e["ours_dump"],
                    "real_dump": json.loads(cache.read_text(encoding="utf-8"))})
    return out


def _load_boards_from_cache(brand: str) -> list[dict]:
    """Boards from the dumps already on disk (`ours_dumps_cache` + `real_dumps_cache`): the engine's output as of the last time
    `cache_ours_dumps.py` ran, so it can be older than the engine - use it for a preview, regenerate with validate_all for real seeding."""
    from tools.validate_all import BRAND_MASTER, DATASET, REAL_DUMPS_CACHE_ROOT, _safe  # noqa: E402
    from app.batch_import import parse_shop_lines  # noqa: E402

    folder = DATASET / ("Agarpathi" if brand == "agarpathi" else brand)
    stems = {_safe(f.stem): f.stem for f in folder.glob("*.cdr")}
    out = []
    for ours in sorted((ROOT / "dataset_analysis" / "ours_dumps_cache" / brand).glob("*.json")):
        real = REAL_DUMPS_CACHE_ROOT / brand / ours.name
        if ours.stem not in stems or not real.exists():
            continue
        od = json.loads(ours.read_text(encoding="utf-8"))
        parsed = parse_shop_lines(stems[ours.stem])
        out.append({"stem": stems[ours.stem], "brand": brand, "master_file": BRAND_MASTER[brand]["file"], "pw": od["page_mm"]["w"],
                    "ph": od["page_mm"]["h"], "board_type": parsed.shops[0].type if parsed.shops else None, "ours_dump": od,
                    "real_dump": json.loads(real.read_text(encoding="utf-8"))})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brand")
    ap.add_argument("--from-cache", action="store_true", help="use the cached dumps instead of the last validate_all run (may be stale)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be stored, store nothing")
    ap.add_argument("--no-eval", action="store_true")
    a = ap.parse_args()
    from tools.validate_all import KNOWN_OUTLIERS  # noqa: E402
    from app import db  # noqa: E402

    boards = _load_boards_from_cache(a.brand) if a.from_cache else _load_boards(a.brand)
    _derive_records(a.brand, boards, KNOWN_OUTLIERS)
    if not a.dry_run:
        db.init_db()
        stored = [b["record"] for b in boards if b["record"]]
        for rec in stored:
            db.save_correction(rec)
        print(f"stored {len(stored)} correction record(s) for {a.brand} in {db.DB_PATH}")
    if not a.no_eval:
        _print_eval(evaluate(boards))


def _derive_records(brand: str, boards: list[dict], outliers) -> None:
    """Fill each board's `record` (None for a known outlier or when there is nothing to learn) and print one line per board."""
    for b in boards:
        if any(k in b["stem"] for k in outliers):
            print(f"skip (known outlier, a one-off recomposition): {b['stem']}")
            b["record"] = None
            continue
        rec, stats = board_record(brand, b["master_file"], b["stem"], b["board_type"], b["ours_dump"], b["real_dump"], b["pw"], b["ph"])
        b["record"] = rec
        print(f"{b['stem'][:70]:70} units {stats['matched']}/{stats['engine_units']} matched, {stats['units_changed']} changed, "
              f"{stats['changes']} objects" + ("" if rec else " - nothing to learn"))


def _print_eval(rows: list[dict]) -> None:
    print("\nleave-one-out (held-out designer board vs the engine alone), mean centre error in mm:")
    for r in rows:
        print(f"  {r['file'][:60]:60} peers={r['peers']} applied={r['applied']:4}/{r['objects']:4}  "
              f"engine {r['engine_mean_mm']:7.1f}  learned {r['learned_mean_mm']:7.1f}")
    if not rows:
        print("  (no board size has two designer files)")
        return
    print(f"  overall mean: engine {statistics.mean(r['engine_mean_mm'] for r in rows):.1f} mm, "
          f"learned {statistics.mean(r['learned_mean_mm'] for r in rows):.1f} mm")


if __name__ == "__main__":
    main()
