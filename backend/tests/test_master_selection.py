"""Evidence-based master selection (app/master_selection.py) - pure scoring on a synthetic library, plus the production wiring behind SIGNAGE_MASTER_SCORE."""
from __future__ import annotations

import importlib
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import master_selection as ms

FT = 304.8


def lib():
    """A tiny brand library: 6 X 3 (landscape 2:1), 4 X 8 (portrait 1:2). Designers made portrait 3x6-ish boards from the 6 X 3 master, 8x3-ish from 10x3."""
    masters = [{"id": "m6", "file": "6 X 3.cdr", "page": [6 * FT, 3 * FT]}, {"id": "m4", "file": "4 X 8.cdr", "page": [4 * FT, 8 * FT]}, {"id": "m10", "file": "10 X 3.cdr", "page": [10 * FT, 3 * FT]}]
    boards = [{"file": f"{i:02d} - {w} X {h} Feet - Double Side GSB - S{i}.cdr", "W": w * FT, "H": h * FT, "master": m, "els": {}, "texts": {}}
              for i, (w, h, m) in enumerate([(3, 6, "m6"), (3, 6, "m6"), (4, 7, "m6"), (3, 5, "m6"), (6, 3, "m6"), (6, 3, "m6"), (10, 3, "m10"), (10, 3, "m10")], 1)]
    return {"brand": "t", "masters": masters, "boards": boards}


def cands():
    return [ms.MasterCandidate("6 X 3.cdr", 6 * FT, 3 * FT, "landscape"), ms.MasterCandidate("4 X 8.cdr", 4 * FT, 8 * FT, "portrait"), ms.MasterCandidate("10 X 3.cdr", 10 * FT, 3 * FT, "landscape")]


def test_designers_evidence_beats_the_orientation_rule_for_portrait_boards():
    best, ranked = ms.choose(lib(), cands(), 3 * FT, 6 * FT, "Double Side GSB")
    assert best.file == "6 X 3.cdr"                                        # the nearest designer boards were made from it, although it is a landscape master
    assert best.orientation_penalty > 0 and "made from" in " ".join(best.reasons)
    assert ranked[0].evidence > ranked[-1].evidence


def test_without_evidence_aspect_decides_and_the_cross_orientation_penalty_applies():
    best, _ = ms.choose(None, cands(), 3 * FT, 6 * FT)                     # no library: only aspect / size
    assert best.file == "4 X 8.cdr"
    best2, _ = ms.choose(None, cands(), 12 * FT, 4 * FT)
    assert best2.file in ("10 X 3.cdr", "6 X 3.cdr") and best2.orientation_penalty == 0


def test_leave_one_out_removes_a_board_from_the_evidence():
    L = lib()
    # a single board of the 4x7 shape: with it the vote for the 4x7 target is its master; without it the neighbours decide
    with_all, _ = ms.choose(L, cands(), 4 * FT, 7 * FT)
    excluded = {b["file"] for b in L["boards"] if (b["W"], b["H"]) == (4 * FT, 7 * FT)}
    without, ranked = ms.choose(L, cands(), 4 * FT, 7 * FT, exclude=excluded)
    assert with_all.file == "6 X 3.cdr" and without.file == "6 X 3.cdr"
    # the excluded boards can no longer vote: leave ONLY them as evidence and the vote disappears
    only = {"masters": lib()["masters"], "boards": [b for b in L["boards"] if b["file"] in excluded]}
    _, ranked = ms.choose(only, cands(), 4 * FT, 7 * FT, exclude=excluded)
    assert all(s.evidence == 0 for s in ranked)


def test_inferred_evidence_weights_override_the_library_master():
    L = lib()
    ev = {b["file"]: {"masters": {"4 X 8.cdr": 1.0}, "status": "inferred_corroborated"} for b in L["boards"] if b["H"] > b["W"]}
    best, _ = ms.choose(L, cands(), 3 * FT, 6 * FT, evidence=ev)
    assert best.file == "4 X 8.cdr"
    disputed = {b["file"]: {"masters": {"4 X 8.cdr": 0.4, "6 X 3.cdr": 0.4}, "status": "disputed"} for b in L["boards"] if b["H"] > b["W"]}
    d, ranked = ms.choose(L, cands(), 3 * FT, 6 * FT, evidence=disputed)
    assert {r.file for r in ranked[:2]} >= {"4 X 8.cdr", "6 X 3.cdr"}


def test_scores_are_explainable_and_bounded():
    _, ranked = ms.choose(lib(), cands(), 8 * FT, 3 * FT)
    for s in ranked:
        assert -0.2 <= s.total <= 1.0 and s.reasons and 0 <= s.evidence <= 1 and 0 <= s.aspect <= 1


def test_a_brand_with_no_evidence_file_gets_an_empty_map():
    assert ms.load_evidence("no-such-brand") == {}
    ev = ms.load_evidence("hangyo")
    assert ev and all("masters" in e and e["status"].startswith(("inferred", "disputed")) for e in ev.values())     # nothing is marked confirmed


# ------------------------------------------------------------------------------------------------------------- production wiring


def _cdr() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/x-cdr")
    return buf.getvalue()


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    client = TestClient(main_module.app)
    ids = {}
    for fname, orient in (("10 X 3.cdr", "landscape"), ("6 X 3.cdr", "landscape"), ("4 X 8.cdr", "portrait")):
        r = client.post("/api/masters/upload", data={"brand": "Hangyo", "orientation": orient}, files={"master": (fname, _cdr(), "application/octet-stream")})
        ids[fname] = r.json()["id"]
    return client, main_module, db_module, ids


def _shop(world, w, h, **extra):
    client, main, db, ids = world
    payload = {"name": "S", "width": w, "height": h, "width_unit": "ft", "height_unit": "ft", "landscape_master_id": ids["10 X 3.cdr"], "portrait_master_id": ids["4 X 8.cdr"],
               "board_type": "Double Side GSB", **extra}
    sid = client.post(f"/api/v2/jobs/{ids['10 X 3.cdr']}/shops", json=payload).json()["id"]
    row = db.get_shop(sid)
    return main._select_shop_master(row, db.get_job(row["job_id"]), w * FT, h * FT)


def test_default_selection_is_unchanged_without_the_flag(world, monkeypatch):
    monkeypatch.delenv("SIGNAGE_MASTER_SCORE", raising=False)
    path, used = _shop(world, 3, 6)
    assert path.parent.name == world[3]["4 X 8.cdr"] and "selection" not in used


def test_flag_makes_a_portrait_board_use_the_masters_the_designers_used(world, monkeypatch):
    monkeypatch.setenv("SIGNAGE_MASTER_SCORE", "1")
    path, used = _shop(world, 3, 6)
    assert path.parent.name == world[3]["6 X 3.cdr"] and used["selection"] == "score" and "master score" in used["reason"]


def test_an_explicit_master_choice_always_overrides_the_score(world, monkeypatch):
    monkeypatch.setenv("SIGNAGE_MASTER_SCORE", "1")
    client, main, db, ids = world
    path, used = _shop(world, 3, 6, master_id=ids["4 X 8.cdr"])
    assert path.parent.name == ids["4 X 8.cdr"] and used.get("selected") is True and "selection" not in used
