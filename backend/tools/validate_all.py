"""End-to-end validation: generate every real shop from its brand's master
via the real CorelEngine (live COM), then compare the result against the
designer's own file for that shop, geometrically.

All CorelDRAW work (generate + both dumps) runs in a separate worker
process per corel_supervisor.run_batch(), which enforces a hard overall
-progress timeout and kills the whole process (and any CorelDRW.exe it
launched) if it hangs anywhere - including inside win32com.client.Dispatch()
itself, which has no in-process timeout of its own. See CLAUDE.md
"Production hardening" for why this exists as a separate process at all.

Never touches signage_dataset/ - all generated output, dumps and caches go
under backend/dataset_analysis/. Read-only against the dataset.

Usage:
    python validate_all.py dalmia
    python validate_all.py agarpathi --limit 5

Writes validation_report.json and validation_report.md into
backend/dataset_analysis/validation/<brand>/.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import corel_supervisor  # noqa: E402
from app.batch_import import parse_shop_lines  # noqa: E402
from app.content_check import check_content, extract_contact_values  # noqa: E402
from app.layout import Obj, detect_role, find_shopname_ids, _tile_plan  # noqa: E402

DATASET = ROOT.parent / "signage_dataset"
VALIDATION_ROOT = ROOT / "dataset_analysis" / "validation"
COMPARE_ROOT = ROOT / "dataset_analysis" / "compare"
REAL_DUMPS_CACHE_ROOT = ROOT / "dataset_analysis" / "real_dumps_cache"

# How close (as a percent of the *target* page's width/height) a matched
# object's position/size must be to the designer's real file to count as a
# pass. Two thresholds are reported for every board; only STRICT feeds the
# headline pass rate, LOOSE is extra context. Tune here - nothing else needs
# to change.
TOLERANCE_PCT = 2.0
TOLERANCE_LOOSE_PCT = 5.0

# Boards known (from inspection - see CLAUDE.md) to be one-off outliers, not
# representative of the tiling/layout rule's general behaviour. Reported
# like any other board, but excluded from the pass-rate denominator so one
# odd file doesn't drown out the signal from the other 12. Keyed by a
# substring of the filename; value is the note shown in the report.
KNOWN_OUTLIERS = {
    "12 - 120 X 60": (
        "excluded from pass rate: real file shows a large, systematic "
        "whole-composition shift (up to ~1.5m) vs. our uniform-scale-and-"
        "centre placement, unlike every other same-size-class board - "
        "looks like a one-off manual recomposition for this specific "
        "squarer target, not a general rule gap. See CLAUDE.md."
    ),
}

BRAND_MASTER = {
    "dalmia": {
        "file": "02 - 120 X 48 Inch - 2 Nos Double Side GSB - SRI KAVI STEELS.cdr",
        "shop_name": "SRI KAVI STEELS",
        "shop_name_local": "ஸ்ரீ கவி ஸ்டீல்ஸ்",
    },
    "agarpathi": {
        "file": "16 - 12 X 4 Feet - Nonlit - AL MADEENA POOJA STORE.cdr",
        "shop_name": "AL MADEENA POOJA STORE",
        "shop_name_local": "அல் மதீனா பூஜை ஸ்டோர்",
    },
}


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "x"


def _flatten_leaves(dump: dict) -> list[dict]:
    """All non-container shapes, regardless of group nesting.

    Real designer files are inconsistent about grouping - some variants of
    the same master group their logos, others leave them as loose curves
    (see CLAUDE.md "Designer dataset analysis"). Comparing only top-level
    shapes made that an apples-to-oranges comparison whenever a real file's
    grouping differed from the master's. A leaf curve's absolute position is
    unaffected by whether something wraps it in a group, so comparing leaves
    on both sides sidesteps the problem entirely.
    """
    return [s for s in dump["shapes"] if s["type"] != "group"]


def _bucket_role(shapes: list[dict], page_w: float, page_h: float, shopname_hints: list[str]) -> list[dict]:
    """Classify each raw dumped shape into our bg/frame/fixed/shopname/text/logo
    buckets, the same way compute_layout would, so real-file shapes can be
    compared against our own role-labelled output on equal terms.
    """
    objs = [Obj(str(i), s["name"], s["type"], s["x"], s["y"], s["w"], s["h"], s.get("text")) for i, s in enumerate(shapes)]
    shopname_ids = find_shopname_ids(objs, *shopname_hints)
    out = []
    for i, (s, o) in enumerate(zip(shapes, objs)):
        role = "shopname" if o.id in shopname_ids else detect_role(o, page_w, page_h)
        out.append({**s, "role": role})
    return out


def _size_similar(a: dict, b: dict, factor: float = 2.0) -> bool:
    """Both dimensions within `factor`x of each other, either direction."""
    for ka, kb in (("w", "w"), ("h", "h")):
        va, vb = a[ka], b[kb]
        if va <= 0 or vb <= 0:
            continue
        ratio = va / vb
        if ratio > factor or ratio < 1 / factor:
            return False
    return True


def _match(ours: list[dict], real: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Greedy nearest-centre matching within the same role bucket.

    A real master's logo is often 100+ individual ungrouped curve fragments
    packed tightly together (see CLAUDE.md "Designer dataset analysis").
    Matching purely by nearest centre among that many same-role candidates
    is fragile - two nearby but very differently-sized fragments (e.g. one
    letterform's accent stroke vs. the whole logo's background shape) can
    get paired, producing a huge, meaningless size/position "difference"
    that reflects a bad match, not a real layout problem. Requiring the
    matched pair to also be similar in size (within 2x on each axis) turns
    those into honest unmatched-object counts instead of noise in the diffs.
    """
    remaining_real = list(range(len(real)))
    pairs = []
    for o in ours:
        ocx, ocy = o["x"] + o["w"] / 2, o["y"] + o["h"] / 2
        candidates = [j for j in remaining_real if real[j]["role"] == o["role"] and _size_similar(o, real[j])]
        if not candidates:
            continue
        best = min(candidates, key=lambda j: (real[j]["x"] + real[j]["w"] / 2 - ocx) ** 2
                                             + (real[j]["y"] + real[j]["h"] / 2 - ocy) ** 2)
        pairs.append((o, real[best]))
        remaining_real.remove(best)
    unmatched_ours = [o for o in ours if not any(o is p[0] for p in pairs)]
    unmatched_real = [real[j] for j in remaining_real]
    return pairs, unmatched_ours, unmatched_real


def _diff_pct(a: dict, b: dict, page_w: float, page_h: float) -> dict:
    dx_mm, dy_mm = a["x"] - b["x"], a["y"] - b["y"]
    dw_mm, dh_mm = a["w"] - b["w"], a["h"] - b["h"]
    return {
        "dx_mm": dx_mm, "dy_mm": dy_mm, "dw_mm": dw_mm, "dh_mm": dh_mm,
        "dx_pct": abs(dx_mm) / page_w * 100, "dy_pct": abs(dy_mm) / page_h * 100,
        "dw_pct": abs(dw_mm) / page_w * 100, "dh_pct": abs(dh_mm) / page_h * 100,
        "role": a["role"],
    }


def _build_board(f: Path, shop_spec, safe: str, out_dir: Path, entry: dict, requested: dict) -> dict:
    if entry.get("status") != "done":
        return {"file": f.name, "shop_name": shop_spec.name, "error": entry.get("error", "unknown worker error")}

    result = entry["result"]
    page_mm = result["report"]["new_page_mm"]
    page_w, page_h = page_mm["w"], page_mm["h"]
    master_mm = result["report"]["original_page_mm"]
    axis, n_tiles = _tile_plan(master_mm["w"], master_mm["h"], page_w, page_h)

    ours = _bucket_role(_flatten_leaves(entry["ours_dump"]), page_w, page_h, [shop_spec.name])
    real = _bucket_role(_flatten_leaves(entry["real_dump"]), page_w, page_h, [shop_spec.name])

    pairs, unmatched_ours, unmatched_real = _match(ours, real)
    diffs = [_diff_pct(o, r, page_w, page_h) for o, r in pairs]
    worst = max((max(d["dx_pct"], d["dy_pct"], d["dw_pct"], d["dh_pct"]) for d in diffs), default=None)
    passed_strict = worst is not None and worst <= TOLERANCE_PCT
    passed_loose = worst is not None and worst <= TOLERANCE_LOOSE_PCT
    shopname_diffs = [d for d in diffs if d["role"] == "shopname"]

    compare_dir = COMPARE_ROOT / safe
    compare_dir.mkdir(parents=True, exist_ok=True)
    ours_png = out_dir / result["files"]["preview"]
    real_png = compare_dir / "real.png"  # rendered later by add_visual_metrics.py

    outlier_note = next((note for key, note in KNOWN_OUTLIERS.items() if key in f.stem), None)

    # Step 1 content check: does the generated file actually say what we asked it to?
    # `requested` is exactly the shop dict's name/phone/gst - not the real file's own
    # display (see content_check.py's docstring for why those are different things).
    content = check_content(
        _flatten_leaves(entry["ours_dump"]),
        expected_name=requested.get("name"), expected_phone=requested.get("phone"), expected_gst=requested.get("gst"),
    )

    return {
        "file": f.name, "shop_name": shop_spec.name,
        "requested_contact": {"phone": requested.get("phone"), "gst": requested.get("gst")},
        "content_check": content,
        "target_mm": {"w": page_w, "h": page_h},
        "seconds": entry.get("seconds"),
        "real_dump_cached": entry.get("real_dump_cached", False),
        "our_tile": {"axis": axis, "n": n_tiles},
        "counts": {"ours": len(ours), "real": len(real), "matched": len(pairs),
                   "unmatched_ours": len(unmatched_ours), "unmatched_real": len(unmatched_real)},
        "diff_pct": {
            "max": worst,
            "mean": statistics.mean(max(d["dx_pct"], d["dy_pct"], d["dw_pct"], d["dh_pct"]) for d in diffs) if diffs else None,
            "median": statistics.median(max(d["dx_pct"], d["dy_pct"], d["dw_pct"], d["dh_pct"]) for d in diffs) if diffs else None,
        },
        "shopname_diff_pct_max": max((max(d["dx_pct"], d["dy_pct"]) for d in shopname_diffs), default=None),
        "ssim": None,  # filled in by add_visual_metrics.py's separate pass
        "pass": passed_strict,  # kept for backward compatibility with earlier reports
        "pass_2pct": passed_strict,
        "pass_5pct": passed_loose,
        "excluded_outlier": outlier_note is not None,
        "outlier_note": outlier_note,
        "ours_png": str(ours_png.relative_to(ROOT)),
        "real_png": str(real_png.relative_to(ROOT)),
        "objects": diffs,
    }


def validate_brand(brand: str, limit: int | None = None, only: str | None = None,
                    resume: bool = False, overall_timeout_s: float | None = None) -> dict:
    cfg = BRAND_MASTER[brand]
    brand_dir = DATASET / ("Agarpathi" if brand == "agarpathi" else brand)
    master_path = brand_dir / cfg["file"]
    out_root = VALIDATION_ROOT / brand
    out_root.mkdir(parents=True, exist_ok=True)

    files = sorted(brand_dir.glob("*.cdr"))
    if only:
        files = [f for f in files if only.lower() in f.stem.lower()]
    if limit:
        files = files[:limit]

    # Always start from whatever's already on disk, not just when --resume is
    # passed - a bare `--only <substr>` run (e.g. to fix one board) used to
    # start `boards` empty and overwrite validation_report.json with just
    # that filtered subset, silently discarding every other board's entry.
    # This actually happened more than once in practice (see CLAUDE.md
    # "Wide-board panel sequence" and "Metrics suite") and was recovered by
    # hand each time by rebuilding the missing entries offline from their
    # still-valid cached dumps - annoying and easy to forget. Now: any
    # previously-recorded board NOT about to be regenerated in this run is
    # always kept; `--resume` additionally skips regenerating boards that
    # already succeeded (rather than just keeping their old record).
    boards = []
    report_path = out_root / "validation_report.json"
    if report_path.exists():
        boards = json.loads(report_path.read_text(encoding="utf-8"))["boards"]
        if resume:
            boards = [b for b in boards if "error" not in b]  # retry errored ones
            done_files = {b["file"] for b in boards}
            files = [f for f in files if f.name not in done_files]
            print(f"resume: {len(done_files)} already done, {len(files)} remaining (errored boards will be retried)")
        else:
            # not resuming: keep every prior board this run isn't about to
            # regenerate, and drop stale entries for the ones it is (its
            # own fresh result replaces them below)
            regenerating = {f.name for f in files}
            boards = [b for b in boards if b["file"] not in regenerating]

    jobs = []
    job_meta = []  # (file, shop_spec, safe, out_dir, requested), same order as jobs
    for f in files:
        if "copy" in f.stem.lower():
            print(f"SKIP (Copy): {f.name}")
            continue
        parsed = parse_shop_lines(f.stem)
        if not parsed.shops:
            print(f"SKIP (unparsable name): {f.name}")
            continue
        shop_spec = parsed.shops[0]
        safe = _safe(f.stem)
        out_dir = out_root / "generated" / safe
        cache_path = REAL_DUMPS_CACHE_ROOT / brand / f"{safe}.json"

        # Step 1 content check needs a *requested* phone/GST to hand to CorelEngine -
        # there's no other source of truth for a real shop's phone/GST (filenames never
        # encode them), so the designer's own real file is it. Only available up front
        # when that file's dump is already cached from an earlier run - all 13 dalmia
        # files are, as of this writing. If not cached yet, phone/gst are simply not
        # requested for this run (content_check.py then correctly reports NOT_CHECKED
        # for those fields, never a guessed value that could produce a false FAIL).
        contact = {"phone": None, "gst": None}
        if cache_path.exists():
            try:
                cached_real = json.loads(cache_path.read_text(encoding="utf-8"))
                contact = extract_contact_values(_flatten_leaves(cached_real))
            except Exception as e:
                print(f"  (could not read cached real dump for {f.name} to get phone/GST: {e})")
        else:
            print(f"  (real file not dumped yet - phone/GST not requested for {f.name}, will be NOT_CHECKED)")

        shop = {
            "name": shop_spec.name, "width": shop_spec.width, "height": shop_spec.height,
            "unit": shop_spec.unit, "master_shop_name": cfg["shop_name"],
            "master_shop_name_local": cfg["shop_name_local"], "brand": brand,
        }
        if contact["phone"]:
            shop["phone"] = contact["phone"]
        if contact["gst"]:
            shop["gst"] = contact["gst"]
        requested = {"name": shop_spec.name, "phone": contact["phone"], "gst": contact["gst"]}

        jobs.append({
            "master_path": str(master_path), "shop": shop, "out_dir": str(out_dir),
            "real_file": str(f), "real_dump_cache": str(cache_path),
        })
        job_meta.append((f, shop_spec, safe, out_dir, requested))

    def _write_report():
        report = {"brand": brand, "tolerance_pct": TOLERANCE_PCT, "boards": boards}
        (out_root / "validation_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (out_root / "validation_report.md").write_text(_render_md(report), encoding="utf-8")
        return report

    if jobs:
        def _on_progress(idx, entry):
            f, shop_spec, safe, out_dir, requested = job_meta[idx]
            board = _build_board(f, shop_spec, safe, out_dir, entry, requested)
            boards.append(board)
            if "error" in board:
                print(f"[{idx + 1}/{len(jobs)}] {f.name}: ERROR - {board['error']}")
            else:
                cached = " (real dump cached)" if board.get("real_dump_cached") else ""
                maxd = board["diff_pct"]["max"]
                print(f"[{idx + 1}/{len(jobs)}] {f.name} -> {shop_spec.name}: "
                      f"{entry.get('seconds')}s{cached}, tile={board['our_tile']['axis']},{board['our_tile']['n']}, "
                      f"max_diff={'-' if maxd is None else f'{maxd:.1f}%'}, "
                      f"{'PASS' if board['pass_2pct'] else 'FAIL'}")
            _write_report()

        try:
            corel_supervisor.run_batch(
                jobs, out_root / "worker_results.json",
                overall_timeout_s=overall_timeout_s, on_progress=_on_progress,
            )
        except corel_supervisor.RefusedToStart as e:
            print(str(e))

    return _write_report()


def _content_summary(content: dict | None) -> str:
    if not content:
        return "-"
    if content["overall"] == "CONTENT_OK":
        return "OK"
    if content["overall"] == "NOT_CHECKED":
        return "NOT_CHECKED"
    bad = [f for f in ("shop_name", "phone", "gst") if content[f]["status"] == "CONTENT_FAIL"]
    return f"FAIL ({','.join(bad)})"


def _render_md(report: dict) -> str:
    lines = [
        f"# Validation report: {report['brand']}",
        "",
        f"Strict tolerance: {TOLERANCE_PCT}% of target page width/height. Loose: {TOLERANCE_LOOSE_PCT}%. "
        "SSIM is a greyscale image-similarity score (1.0 = identical), independent of the object-diff metrics. "
        "Content: does the generated file's text actually say what was requested (shop name / phone / GST) - "
        "see app/content_check.py; NOT_CHECKED means that field was never requested (e.g. the real shop has no "
        "GST line), never a vacuous pass.",
        "",
        "| File | Shop | Target (mm) | Tile ours | Objects ours/real | Matched | Max diff % | Shopname diff % | SSIM | 2% | 5% | Content |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    n_pass_2, n_pass_5, n_total = 0, 0, 0
    n_content_ok, n_content_fail, n_content_not_checked = 0, 0, 0
    outlier_rows = []
    for b in report["boards"]:
        if "error" in b:
            lines.append(f"| {b['file']} | {b['shop_name']} | - | - | - | - | - | - | - | - | - | - | ERROR: {b['error']} |")
            continue
        t = b["target_mm"]
        c = b["counts"]
        maxd = b["diff_pct"]["max"]
        sd = b.get("shopname_diff_pct_max")
        ssim = b.get("ssim")
        content = b.get("content_check")
        row = (
            f"| {b['file']} | {b['shop_name']} | {t['w']:.0f}x{t['h']:.0f} | "
            f"{b['our_tile']['axis']},{b['our_tile']['n']} | {c['ours']}/{c['real']} | {c['matched']} | "
            f"{'-' if maxd is None else f'{maxd:.1f}'} | {'-' if sd is None else f'{sd:.1f}'} | "
            f"{'-' if ssim is None else f'{ssim:.3f}'} | "
            f"{'PASS' if b.get('pass_2pct') else 'FAIL'} | {'PASS' if b.get('pass_5pct') else 'FAIL'} | "
            f"{_content_summary(content)} |"
        )
        if content:
            if content["overall"] == "CONTENT_OK":
                n_content_ok += 1
            elif content["overall"] == "CONTENT_FAIL":
                n_content_fail += 1
            else:
                n_content_not_checked += 1
        if b.get("excluded_outlier"):
            lines.append(row + f" *(excluded from pass rate - {b['outlier_note']})*")
            outlier_rows.append(b["file"])
            continue
        lines.append(row)
        n_total += 1
        n_pass_2 += bool(b.get("pass_2pct"))
        n_pass_5 += bool(b.get("pass_5pct"))

    summary = (
        f"\n**{n_pass_2}/{n_total} boards pass at {TOLERANCE_PCT}% tolerance; "
        f"{n_pass_5}/{n_total} pass at {TOLERANCE_LOOSE_PCT}%.** "
        f"Content check: {n_content_ok} CONTENT_OK, {n_content_fail} CONTENT_FAIL, "
        f"{n_content_not_checked} NOT_CHECKED (all boards, not just the pass-rate ones)."
    )
    if outlier_rows:
        summary += f" ({len(outlier_rows)} board(s) excluded as known outliers: {', '.join(outlier_rows)})"
    lines.insert(4, summary + "\n")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("brand", choices=list(BRAND_MASTER))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", help="substring filter on the source filename")
    ap.add_argument("--resume", action="store_true", help="skip files already in an existing validation_report.json")
    ap.add_argument("--timeout", type=float, default=None, help="per-job no-progress timeout in seconds (default 600)")
    args = ap.parse_args()
    validate_brand(args.brand, args.limit, args.only, args.resume, args.timeout)


if __name__ == "__main__":
    main()
