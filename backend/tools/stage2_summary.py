"""Stage 2 summary of the real-CorelDRAW runs: Stage 1 baseline vs s2_prod (typography fix, same conditions) vs old_loo / new_loo (leave-one-shop-out, old vs new code).
Reads the CSV / extraction files the harness wrote; writes dataset_analysis/stage2/stage2_summary.json and prints markdown tables.  No CorelDRAW."""
from __future__ import annotations

import csv
import json
import re
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
S1 = BACKEND / "dataset_analysis" / "stage1"
S2 = BACKEND / "dataset_analysis" / "stage2"
RUNS = S2 / "runs"
TAMIL = re.compile("[஀-௿]")
EPS = 0.02          # a change smaller than this is a tie


def load(p: Path) -> list[dict]:
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def fnum(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def cls(size: str) -> str:
    w, h = (float(x) for x in size.lower().split("x"))
    r = w / h
    return "portrait" if r < 0.8 else "compact landscape" if r < 2.5 else "mid landscape" if r < 4.2 else "wide"


def orient(size: str) -> str:
    w, h = (float(x) for x in size.lower().split("x"))
    return "portrait" if w < h else "landscape"


SETS = {"stage1": S1, "s2_prod": RUNS / "s2_prod", "old_loo": RUNS / "old_loo", "new_loo": RUNS / "new_loo"}


def table(name: str):
    d = SETS[name]
    return {r["case"]: r for r in load(d / "visual_comparison_results.csv")}, {r["case"]: r for r in load(d / "structural_comparison_results.csv")}


def pair(a: str, b: str, vis):
    """a = before, b = after.  Only boards whose score is meaningful in BOTH sets and whose mapping is CONFIRMED."""
    rows = []
    for case, ob in vis[a].items():
        nb = vis[b].get(case)
        if not nb:
            continue
        o, n = fnum(ob["combined"]), fnum(nb["combined"])
        ok = ob["score_meaningful"] == "True" and nb["score_meaningful"] == "True" and ob["mapping_status"] == "CONFIRMED" and o is not None and n is not None
        rows.append({"case": case, "size": nb["size_ft"], "class": cls(nb["size_ft"]), "orientation": orient(nb["size_ft"]), "language": nb["language"], "ok": ok, "old": o, "new": n, "d": (n - o) if ok else None,
                     "master_old": ob.get("engine_master_file"), "master_new": nb.get("engine_master_file"), "mode_old": ob["engine_mode"], "mode_new": nb["engine_mode"],
                     "ssim_old": fnum(ob["ssim"]), "ssim_new": fnum(nb["ssim"]), "de_old": fnum(ob["mean_delta_e76"]), "de_new": fnum(nb["mean_delta_e76"])})
    return rows


def agg(rows, key=None):
    groups = defaultdict(list)
    for r in rows:
        if r["ok"]:
            groups[r[key] if key else "all"].append(r)
    out = {}
    for k, v in sorted(groups.items()):
        d = [r["d"] for r in v]
        out[k] = {"n": len(v), "before": round(st.mean(r["old"] for r in v), 4), "after": round(st.mean(r["new"] for r in v), 4), "delta": round(st.mean(d), 4),
                  "improved": sum(1 for x in d if x > EPS), "regressed": sum(1 for x in d if x < -EPS), "unchanged": sum(1 for x in d if abs(x) <= EPS)}
    return out


def tamil_editable(name: str, vis):
    """Boards whose Tamil name is still a TEXT object after the generated CDR was saved and re-opened in CorelDRAW (vs input boards that have a Tamil name)."""
    d = SETS[name]
    ext = d / "extractions" / "generated"
    res = {"boards_with_tamil_input": 0, "tamil_text_object_after_reopen": 0, "tamil_font_after_reopen": Counter(), "no_tamil_text_object": []}
    for case, row in vis.items():
        if row["language"] not in ("both", "ta", "tamil"):
            continue
        f = ext / case / "extraction.json"
        if not f.is_file():
            continue
        res["boards_with_tamil_input"] += 1
        e = json.loads(f.read_text(encoding="utf-8"))
        fonts = [(o["text"] or {}).get("font") for o in e["objects"] if o["type"] == "text" and TAMIL.search((o.get("text") or {}).get("content") or "")]
        if fonts:
            res["tamil_text_object_after_reopen"] += 1
            res["tamil_font_after_reopen"].update(fonts)
        else:
            res["no_tamil_text_object"].append(case)
    res["tamil_font_after_reopen"] = dict(res["tamil_font_after_reopen"])
    res["no_tamil_text_object_count"] = len(res["no_tamil_text_object"])
    res["no_tamil_text_object"] = res["no_tamil_text_object"][:60]
    return res


def main():
    vis, struct = {}, {}
    for n in SETS:
        vis[n], struct[n] = table(n)
    out = {"epsilon_tie": EPS, "sets": {}}
    for n in SETS:
        v = vis[n]
        ok = [r for r in v.values() if r["score_meaningful"] == "True" and r["mapping_status"] == "CONFIRMED"]
        out["sets"][n] = {"boards": len(v), "mean_combined_confirmed_meaningful": round(st.mean(fnum(r["combined"]) for r in ok), 4) if ok else None, "n_confirmed_meaningful": len(ok),
                          "mean_combined_all": round(st.mean(fnum(r["combined"]) for r in v.values() if fnum(r["combined"]) is not None), 4),
                          "engine_modes": dict(Counter(r["engine_mode"] for r in v.values())), "engine_master": dict(Counter(r["engine_master_file"] for r in v.values())),
                          "master_matches_structural_guess": sum(1 for r in v.values() if r.get("engine_master_matches_structural_guess") == "True"),
                          "defects": dict(Counter(c for r in struct[n].values() for c in json.loads(r.get("defect_codes") or "[]"))),
                          "tamil_editability": tamil_editable(n, v)}
    pairs = {"typography_same_conditions (stage1 -> s2_prod)": ("stage1", "s2_prod"), "leakage_effect (stage1 -> old_loo)": ("stage1", "old_loo"), "code_effect_fair (old_loo -> new_loo)": ("old_loo", "new_loo")}
    out["pairs"] = {}
    for label, (a, b) in pairs.items():
        rows = pair(a, b, vis)
        reg = sorted((r for r in rows if r["ok"] and r["d"] < -EPS), key=lambda r: r["d"])
        imp = sorted((r for r in rows if r["ok"] and r["d"] > EPS), key=lambda r: -r["d"])
        out["pairs"][label] = {"overall": agg(rows).get("all"), "by_class": agg(rows, "class"), "by_orientation": agg(rows, "orientation"), "by_language": agg(rows, "language"),
                               "not_comparable": [r["case"] for r in rows if not r["ok"]],
                               "regressions": [{k: r[k] for k in ("case", "size", "language", "old", "new", "d", "master_old", "master_new", "mode_old", "mode_new")} for r in reg],
                               "improvements": [{k: r[k] for k in ("case", "size", "language", "old", "new", "d", "master_old", "master_new", "mode_old", "mode_new")} for r in imp],
                               "master_changed": sum(1 for r in rows if r["master_old"] != r["master_new"]), "per_board": rows}
    (S2 / "stage2_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print("SETS")
    for n, s in out["sets"].items():
        print(f" {n:8s} boards={s['boards']} mean(confirmed&meaningful n={s['n_confirmed_meaningful']})={s['mean_combined_confirmed_meaningful']} modes={s['engine_modes']} masters={s['engine_master']} master_matches_guess={s['master_matches_structural_guess']}")
        t = s["tamil_editability"]
        print(f"          tamil input boards={t['boards_with_tamil_input']} tamil still TEXT after reopen={t['tamil_text_object_after_reopen']} fonts={t['tamil_font_after_reopen']}")
        print(f"          defects={s['defects']}")
    for label, p in out["pairs"].items():
        print("\n" + label, "overall", p["overall"], "| master changed on", p["master_changed"], "boards | not comparable", len(p["not_comparable"]))
        for k in ("by_class", "by_orientation", "by_language"):
            print(" ", k)
            for g, v in p[k].items():
                print(f"    {g:20s} n={v['n']:2d} before={v['before']:.3f} after={v['after']:.3f} delta={v['delta']:+.3f} improved={v['improved']} regressed={v['regressed']} same={v['unchanged']}")
        print("  regressions:", [(r["case"][:34], round(r["d"], 3)) for r in p["regressions"][:12]])
        print("  improvements:", [(r["case"][:34], round(r["d"], 3)) for r in p["improvements"][:8]])


if __name__ == "__main__":
    main()
