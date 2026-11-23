"""Stage 2 regeneration of the 50 Hangyo boards through the REAL production engine (CorelEngine / corel_worker / corel_supervisor, real CorelDRAW), plus the comparison
against the designers' CDRs with Stage 1's method.

  python tools/stage2_eval.py gen     --run NAME [--typography 0|1] [--score 0|1] [--pool 0|1] [--loo 0|1]
  python tools/stage2_eval.py extract --run NAME          # real CorelDRAW read + render of the generated CDRs (Stage 1 extractor, same render size)
  python tools/stage2_eval.py compare --run NAME          # Stage 1's visual + structural comparison against the designer CDRs
  python tools/stage2_eval.py verify  --run NAME
  python tools/stage2_eval.py diff    --run NAME [--baseline stage1|NAME2]   # per-board before/after

The board inputs (names, language, size, type, side) are EXACTLY those of the Stage 1 baseline (read from its baseline_generation_results.json).
`gen` runs the same preparation as the API (`main._convert_job`: master selection, shop dict) in-process and hands the jobs to `corel_supervisor.run_batch` with the
production time limits - one CorelDRAW job at a time, RAM guard untouched. `--loo 1` is leave-one-SHOP-out: every designer file of the board's S.N is removed from the example
library for master selection (in-process) and from layout (`shop["template_exclude"]`), so a designer board never helps make itself. Flags map to env vars the engine reads:
SIGNAGE_TYPOGRAPHY_V2, SIGNAGE_MASTER_SCORE, SIGNAGE_NAME_POOL. The spatial planner is never enabled.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import csv
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tools"))

S1 = BACKEND / "dataset_analysis" / "stage1"
S2 = BACKEND / "dataset_analysis" / "stage2"
CREATIVE = BACKEND.parent / "signage_dataset" / "Hangyo" / "Creative"
FT = 304.8


def run_dir(name: str) -> Path:
    d = S2 / "runs" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _free_gb() -> float:
    import ctypes

    class M(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("p", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in "tp ap tpf apf tv av ae".split()]

    m = M()
    m.l = ctypes.sizeof(M)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ap / 2 ** 30


@contextlib.contextmanager
def loo_library(excluded: set[str]):
    """In-process only: the example library the master selection reads has these designer boards removed."""
    from app import example_layout as ex

    orig_lib, orig_cl = ex.library, ex.candidate_libraries

    def filt(lib):
        if not lib:
            return lib
        out = copy.copy(lib)
        out["boards"] = [b for b in lib["boards"] if b["file"] not in excluded]
        return out

    ex.library = lambda brand: filt(orig_lib(brand))
    ex.candidate_libraries = lambda brand: [filt(l) for l in orig_cl(brand)]
    try:
        yield
    finally:
        ex.library, ex.candidate_libraries = orig_lib, orig_cl


def cmd_gen(a):
    run = run_dir(a.run)
    gdir = run / "generated"
    gdir.mkdir(exist_ok=True)
    data = run / "_server_data"
    data.mkdir(exist_ok=True)
    # environment BEFORE the app is imported / the worker is spawned (the worker inherits it)
    os.environ.update(SIGNAGE_DATA=str(data), SIGNAGE_ENGINE="corel", SIGNAGE_AUTH="off", SIGNAGE_SHOP_TIMEOUT_S="240", SIGNAGE_MASTER_ANALYSIS="0",
                      SIGNAGE_TYPOGRAPHY_V2=str(a.typography), SIGNAGE_MASTER_SCORE=str(a.score), SIGNAGE_NAME_POOL=str(a.pool))
    os.environ.pop("SIGNAGE_SPATIAL_PLANNER", None)
    from fastapi.testclient import TestClient

    from app import corel_supervisor, main

    client = TestClient(main.app)
    base = json.loads((S1 / "baseline_generation_results.json").read_text(encoding="utf-8"))
    cases = [r for r in base["rows"] if not r.get("skipped")]
    by_sno: dict = {}
    all_cases = cases
    if getattr(a, "limit", 0):
        cases = [r for r in cases if r.get("language") and r.get("name_ta")][: a.limit] or cases[: a.limit]
    for r in all_cases:
        by_sno.setdefault(r["sno"], []).append(r["designer_file"])
    masters = {}
    for fname, orient in (("10 X 3.cdr", "landscape"), ("6 X 3.cdr", "landscape"), ("4 X 8.cdr", "portrait")):
        r = client.post("/api/masters/upload", data={"brand": "Hangyo", "orientation": orient}, files={"master": (fname, (CREATIVE / fname).read_bytes(), "application/octet-stream")})
        assert r.status_code == 200, r.text
        masters[fname] = r.json()["id"]
    by_id = {v: k for k, v in masters.items()}
    land, ports = masters["10 X 3.cdr"], masters["4 X 8.cdr"]
    prepared = []
    for c in cases:
        payload = {"name": c["name_en"], "shop_name_local": c["name_ta"] or None, "width": c["w_ft"], "height": c["h_ft"], "width_unit": "ft", "height_unit": "ft", "unit": "ft",
                   "board_type": c["board_type"], "language": c["language"], "sno": str(c["sno"]), "landscape_master_id": land, "portrait_master_id": ports}
        sid = client.post(f"/api/v2/jobs/{land}/shops", json=payload).json()["id"]
        excl = set(by_sno[c["sno"]]) if a.loo else set()
        with loo_library(excl):
            job, master_used = main._convert_job(sid)
        if a.loo:
            job["shop"]["template_exclude"] = sorted(excl)
        prepared.append({"case": c, "shop_id": sid, "job": job, "master_used": master_used})
    results: dict = {}
    pending = list(range(len(prepared)))
    attempt = 0
    while pending and attempt < a.retries:
        attempt += 1
        for k in range(0, len(pending), 5):
            idx = pending[k:k + 5]
            jobs = [prepared[i]["job"] for i in idx]
            while True:
                try:
                    res = corel_supervisor.run_batch(jobs, run / "_batch.json", **main._convert_limits())
                    break
                except corel_supervisor.RefusedToStart as e:
                    print("RAM guard (not weakened) - waiting 45 s:", e, flush=True)
                    time.sleep(45)
            for i, r in zip(idx, res):
                results[i] = r
                print(f"  {prepared[i]['case']['case'][:48]}: {r.get('status')} {r.get('error') or ''}", flush=True)
        pending = [i for i in pending if results[i].get("status") != "done"]
        if pending:
            print(f"{len(pending)} boards failed; retrying alone", flush=True)
    rows = []
    for i, p in enumerate(prepared):
        c, r = p["case"], results.get(i, {})
        r = {**r, **(r.get("result") or {})}          # run_batch nests the engine result (files, report) under "result"
        row = {k: c.get(k) for k in ("case", "designer_file", "sno", "side", "w_ft", "h_ft", "board_type", "language", "name_en", "name_ta", "derivation", "mapping_status")}
        row.update(status=r.get("status"), error=r.get("error"), attempts=attempt)
        if r.get("status") == "done":
            rep = r.get("report") or {}
            lay = rep.get("layout") or {}
            mu = p["master_used"]
            row.update(master_used=mu.get("name"), master_used_detail=mu, master_file=by_id.get(mu.get("job_id")), engine_mode=lay.get("mode"), example_board=lay.get("file"),
                       example_exact=lay.get("exact"), example_distance=lay.get("distance"), warnings=rep.get("warnings"), seconds=rep.get("timings_s"), typography=rep.get("typography"),
                       layout_review=lay.get("review"), corel=rep.get("corel"))
            for kind, name in (r.get("files") or {}).items():
                src = Path(p["job"]["out_dir"]) / name
                if not src.is_file():
                    continue
                ext = src.suffix.lower()
                dest = gdir / (f"{c['case']}{ext}" if ext in (".cdr", ".pdf") else f"{c['case']}__{kind}{ext}")
                shutil.copyfile(src, dest)
                row[f"file_{kind}"] = dest.name
        rows.append(row)
    cfg = {"run": a.run, "typography_v2": a.typography, "master_score": a.score, "name_pool": a.pool, "leave_one_shop_out": a.loo, "planner": "OFF",
           "date": time.strftime("%Y-%m-%d %H:%M:%S"), "shop_timeout_s": 240, "inputs_from": "stage1 baseline_generation_results.json (identical names / sizes / languages / types)"}
    (run / "baseline_generation_results.json").write_text(json.dumps({"masters": masters, "rows": rows, "run_config": cfg}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("generated", sum(1 for r in rows if r.get("status") == "done"), "of", len(rows), "->", run)


def _stage1(cmd: str, run: str, extra: list[str] | None = None):
    run_path = run_dir(run)
    for fname in ("shop_reference_mapping.json", "dataset_inventory.json"):
        if not (run_path / fname).exists():
            shutil.copyfile(S1 / fname, run_path / fname)
    os.environ["STAGE1_REF_ROOT"] = str(S1)
    import stage1_audit as s1

    ns = argparse.Namespace(out=str(run_path), group="generated", batch=6)
    getattr(s1, "cmd_" + cmd)(ns)


def cmd_extract(a):
    _stage1("extract", a.run)


def cmd_compare(a):
    _stage1("compare", a.run)


def cmd_verify(a):
    _stage1("verify", a.run)


def _load(p: Path):
    return list(csv.DictReader(open(p, encoding="utf-8-sig")))


def cmd_diff(a):
    """Per-board before / after: Stage 1 baseline (or another run) vs this run. Visual only where both pairs are meaningful; structural counts separately."""
    new = run_dir(a.run)
    old = S1 if a.baseline == "stage1" else run_dir(a.baseline)
    ov, ns_ = {r["case"]: r for r in _load(old / "visual_comparison_results.csv")}, {r["case"]: r for r in _load(new / "visual_comparison_results.csv")}
    os_, nst = {r["case"]: r for r in _load(old / "structural_comparison_results.csv")}, {r["case"]: r for r in _load(new / "structural_comparison_results.csv")}
    rows = []
    for case, n in ns_.items():
        o = ov.get(case)
        if not o:
            continue
        fo = lambda k, d=o: float(d[k]) if d.get(k) not in (None, "") else None  # noqa: E731
        fn = lambda k: float(n[k]) if n.get(k) not in (None, "") else None  # noqa: E731
        so, sn = os_.get(case, {}), nst.get(case, {})
        do = set(json.loads(so.get("defect_codes") or "[]"))
        dn = set(json.loads(sn.get("defect_codes") or "[]"))
        meaningful = o["score_meaningful"] == "True" and n["score_meaningful"] == "True" and o["mapping_status"] == "CONFIRMED"
        rows.append({"case": case, "size_ft": n["size_ft"], "language": n["language"], "mapping_status": n["mapping_status"], "side": n["side"],
                     "engine_mode_old": o["engine_mode"], "engine_mode_new": n["engine_mode"], "engine_master_old": o.get("engine_master_file"), "engine_master_new": n.get("engine_master_file"),
                     "combined_old": fo("combined"), "combined_new": fn("combined"), "delta_combined": (fn("combined") - fo("combined")) if fo("combined") is not None and fn("combined") is not None else None,
                     "ssim_old": fo("ssim"), "ssim_new": fn("ssim"), "delta_e_old": fo("mean_delta_e76"), "delta_e_new": fn("mean_delta_e76"),
                     "comparable_for_claim": meaningful, "defects_old": len(do), "defects_new": len(dn), "defects_fixed": sorted(do - dn), "defects_introduced": sorted(dn - do),
                     "name_h_ratio_tamil_old": so.get("tamil_height_ratio"), "name_h_ratio_tamil_new": sn.get("tamil_height_ratio"),
                     "name_h_ratio_english_old": so.get("english_height_ratio"), "name_h_ratio_english_new": sn.get("english_height_ratio")})
    out = new / "stage2_comparison_results.csv"
    cols = sorted({k for r in rows for k in r})
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v) if isinstance(v, list) else v) for k, v in r.items()})
    print("wrote", out, len(rows))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("--run", required=True)
    g.add_argument("--typography", type=int, default=1)
    g.add_argument("--score", type=int, default=0)
    g.add_argument("--pool", type=int, default=0)
    g.add_argument("--loo", type=int, default=0)
    g.add_argument("--retries", type=int, default=3)
    g.add_argument("--limit", type=int, default=0, help="only the first N boards that have a Tamil name (smoke test)")
    for n in ("extract", "compare", "verify"):
        sub.add_parser(n).add_argument("--run", required=True)
    d = sub.add_parser("diff")
    d.add_argument("--run", required=True)
    d.add_argument("--baseline", default="stage1")
    a = ap.parse_args()
    globals()["cmd_" + a.cmd](a)


if __name__ == "__main__":
    main()
