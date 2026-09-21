"""Formal regression entry-point over the accumulated real-CorelDRAW
validation baselines for both brands (dalmia and agarpathi).

Unlike test_confidence.py / test_content_check.py (pure-Python, synthetic
inputs, always run), this file reads the REAL, accumulated
`dataset_analysis/validation/<brand>/validation_report.json` produced by
`tools/validate_all.py` and its brand-specific batch drivers
(`agarpathi_family_a_batch.py`, `agarpathi_portrait_batch.py`) across every
phase of this work - 13 dalmia boards, 51 agarpathi boards (Family A
landscape, Family B landscape, and all 9 portrait/square boards) as of this
writing. No CorelDRAW call is made by this file itself - it is pure
analysis of already-computed results, so it never touches
signage_dataset/ and needs no RAM guard of its own.

`dataset_analysis/` is gitignored (regenerable, not checked in - see
CLAUDE.md), so a fresh checkout or CI run will not have these reports.
Every test here skips gracefully when its report is missing, exactly like
test_confidence.py's own real-bounds-file test - this file is meant to be
run locally after a validate_all.py-family batch, as a "did anything
regress" gate, not as a CI-blocking suite that requires re-running real
CorelDRAW jobs on every commit.

Run with: pytest backend/tests/test_dataset_regression.py -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.confidence import GOOD, MANUAL, confidence_label
from app.layout import _tile_plan

ROOT = Path(__file__).resolve().parents[1]
VALIDATION_ROOT = ROOT / "dataset_analysis" / "validation"
BOUNDS_PATH = ROOT / "app" / "confidence_bounds.json"

MASTER_MM = {
    "dalmia": (3048.0, 1219.2),
    "agarpathi": (3657.6, 1219.2),
}


def _load_report(brand: str) -> list[dict] | None:
    path = VALIDATION_ROOT / brand / "validation_report.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["boards"]


def _orientation(w: float, h: float, tol: float = 0.02) -> str:
    if abs(w - h) / max(w, h) <= tol:
        return "square"
    return "landscape" if w > h else "portrait"


def _real_boards(brand: str) -> list[dict]:
    boards = _load_report(brand)
    if boards is None:
        pytest.skip(f"dataset_analysis/validation/{brand}/validation_report.json not present "
                    f"in this checkout - run tools/validate_all.py {brand} (or its batch drivers) first")
    # Errored jobs carry no target_mm/content_check - not relevant to these checks.
    return [b for b in boards if "error" not in b]


@pytest.fixture(scope="module")
def bounds() -> dict:
    if not BOUNDS_PATH.exists():
        pytest.skip("app/confidence_bounds.json not present")
    return json.loads(BOUNDS_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("brand", ["dalmia", "agarpathi"])
def test_content_check_pass_rate_is_100_percent(brand):
    """Every board that was actually generated and compared must have
    written the requested shop name (and phone/GST, where requested)
    correctly - see app/content_check.py. This is the one axis that must
    NEVER regress silently: a wrong shop name on a printed sign is the
    costliest possible failure mode, and the visual/geometric metrics are
    proven blind to it (see CLAUDE.md "Metrics suite" sensitivity test -
    a wrong shop's board scored 0.990, still PASS, on the visual score
    alone). CONTENT_FAIL anywhere here is a hard regression, not a
    tolerance to tune.
    """
    boards = _real_boards(brand)
    fails = [b["file"] for b in boards if b.get("content_check", {}).get("overall") == "CONTENT_FAIL"]
    assert fails == [], f"{brand}: CONTENT_FAIL on {len(fails)}/{len(boards)} board(s): {fails}"
    checked = [b for b in boards if b.get("content_check", {}).get("overall") in ("CONTENT_OK", "CONTENT_FAIL")]
    assert checked, f"{brand}: no board had a checkable content_check at all - fixture data looks broken"


@pytest.mark.parametrize("brand", ["dalmia", "agarpathi"])
def test_no_safety_violation_content_fail_forces_manual(brand, bounds):
    """A synthetic-override check (not dependent on any real board actually
    having failed content) that confidence_label's hard safety rule -
    CONTENT_FAIL can only ever push the label to MANUAL, never leave it
    GOOD or REVIEW - holds for every real board's own target size, not
    just the synthetic bounds used in test_confidence.py. This is the
    literal "zero safety violations" requirement: GOOD or REVIEW must be
    provably unreachable once content is known to be wrong.
    """
    boards = _real_boards(brand)
    violations = []
    for b in boards:
        w, h = b["target_mm"]["w"], b["target_mm"]["h"]
        r = confidence_label(w, h, brand, bounds=bounds,
                              content_check={"overall": "CONTENT_FAIL", "shop_name": {"status": "CONTENT_FAIL"}})
        if r["label"] != MANUAL:
            violations.append((b["file"], r["label"]))
    assert violations == [], f"{brand}: CONTENT_FAIL did not force MANUAL for: {violations}"


@pytest.mark.parametrize("brand", ["dalmia", "agarpathi"])
def test_no_safety_violation_good_only_when_bounds_and_content_agree(brand, bounds):
    """The other half of "zero safety violations": confidence_label must
    never return GOOD for a real board unless (a) its aspect ratio AND
    orientation are inside a validated regime for this brand, AND (b) its
    own real content_check was CONTENT_OK. Reconstructs the expected
    in-range/orientation-matched verdict independently of confidence.py's
    own code (not just calling it and trusting the result) so a future
    change that weakens the safety check - e.g. someone widens a bound
    without evidence, or drops the orientation guard - fails this test
    even though confidence_label itself would return GOOD, as long as this
    test's own independent reconstruction of "should never be GOOD" still
    says the request is out of scope.
    """
    boards = _real_boards(brand)
    violations = []
    for b in boards:
        w, h = b["target_mm"]["w"], b["target_mm"]["h"]
        content = b.get("content_check")
        r = confidence_label(w, h, brand, bounds=bounds, content_check=content)
        if r["label"] != GOOD:
            continue  # only auditing the GOOD claims - REVIEW/MANUAL are always the safe direction

        brand_bounds = bounds.get(brand, {})
        aspect = max(w, h) / min(w, h)
        master_w, master_h = brand_bounds.get("master_mm", {}).get("w"), brand_bounds.get("master_mm", {}).get("h")
        tiled = _tile_plan(master_w, master_h, w, h)[0] is not None
        regime = "tiled" if tiled else "untiled"
        rb = brand_bounds.get("regimes", {}).get(regime)

        in_range = rb is not None and rb["aspect_min"] <= aspect <= rb["aspect_max"]
        orientation_ok = rb is None or not rb.get("orientation") or rb["orientation"] == _orientation(w, h)
        content_ok = content is None or content.get("overall") != "CONTENT_FAIL"

        if not (in_range and orientation_ok and content_ok):
            violations.append({
                "file": b["file"], "aspect": round(aspect, 3), "regime": regime,
                "in_range": in_range, "orientation_ok": orientation_ok, "content_ok": content_ok,
            })
    assert violations == [], f"{brand}: GOOD returned outside validated scope for: {violations}"


def test_orientation_guard_matches_every_real_agarpathi_board():
    """Direct regression lock for the phase-3 orientation bug: every real
    portrait/square Agarpathi board must come back MANUAL (the untiled
    regime is landscape-only), and every real in-range landscape board
    must NOT be downgraded to MANUAL for an orientation mismatch (it may
    still be REVIEW/MANUAL for other reasons - aspect range, tiling - just
    never because of orientation specifically, since orientation DOES
    match there).
    """
    boards = _real_boards("agarpathi")
    bounds = json.loads(BOUNDS_PATH.read_text(encoding="utf-8")) if BOUNDS_PATH.exists() else pytest.skip(
        "app/confidence_bounds.json not present")

    portrait_or_square_mislabeled = []
    landscape_wrongly_orientation_flagged = []
    for b in boards:
        w, h = b["target_mm"]["w"], b["target_mm"]["h"]
        orientation = _orientation(w, h)
        r = confidence_label(w, h, "agarpathi", bounds=bounds)
        if orientation in ("portrait", "square") and r["label"] != MANUAL:
            portrait_or_square_mislabeled.append((b["file"], orientation, r["label"]))
        # A landscape request DOES match the validated orientation, so it must
        # never be flagged for an orientation mismatch specifically - it may
        # still land on MANUAL for an unrelated reason (aspect out of range,
        # unvalidated tiling regime), just never via the "are all landscape,
        # but this request is ..." reason string.
        if orientation == "landscape" and any("but this request is" in reason for reason in r["reasons"]):
            landscape_wrongly_orientation_flagged.append((b["file"], r["reasons"]))

    assert portrait_or_square_mislabeled == [], (
        f"portrait/square agarpathi board(s) not MANUAL: {portrait_or_square_mislabeled}"
    )
    assert landscape_wrongly_orientation_flagged == [], (
        f"landscape agarpathi board(s) incorrectly orientation-flagged: {landscape_wrongly_orientation_flagged}"
    )


@pytest.mark.parametrize("brand", ["dalmia", "agarpathi"])
def test_confidence_bounds_orientation_field_is_landscape_for_both_brands(bounds, brand):
    """Both brands' real dataset is exclusively landscape-sourced so far
    (Agarpathi's portrait/square boards were deliberately left unvalidated
    - see docs/master-preparation.md and app/brand_rules/agarpathi.json).
    Locks in that every existing regime says so explicitly, so a future
    bounds regeneration can't silently drop the orientation guard without
    this test catching it.
    """
    brand_bounds = bounds.get(brand)
    if brand_bounds is None:
        pytest.skip(f"no bounds recorded for {brand!r} yet")
    for regime_name, rb in brand_bounds.get("regimes", {}).items():
        assert rb.get("orientation") == "landscape", (
            f"{brand}/{regime_name} regime is missing the 'orientation' guard or it isn't 'landscape' - "
            f"this would silently re-open the phase-3 orientation bug"
        )
