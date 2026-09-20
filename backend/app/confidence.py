"""Step 5: a confidence label (GOOD / REVIEW / MANUAL) for a signage request,
computable at CONVERT TIME - before generation, with no designer file for
this specific board. It combines three things that are each independently
knowable without one:

  1. the content check (does the generated text say what was requested) -
     only knowable AFTER generation, so this only applies once available;
     omit it (or pass None) to skip that part of the judgement.
  2. layout_checks (metrics.layout_checks) - also only meaningful after
     generation, from the generated file's own shape dump; no ground truth
     needed (see that function's docstring).
  3. whether the requested size falls inside the range of aspect ratios this
     brand has ever been VALIDATED at (`app/confidence_bounds.json`, derived
     offline by `tools/derive_confidence_bounds.py` from real dalmia
     boards - see CLAUDE.md "Step 5") - this one IS knowable purely from the
     requested width/height and brand, before generation even starts.

`confidence_bounds.json` stores, per brand and per regime (tiled/untiled -
tiling is decided the same way `layout._tile_plan` decides it, using the
brand's own master page size), the aspect-ratio range actually validated and
the median/worst historical position/size error (mm) seen there. This is a
LOOKUP over past results, not a live comparison against a real file for the
new request - there is no real file for a brand-new size. Known outliers
(see `validate_all.KNOWN_OUTLIERS`) are excluded when the bounds are derived.

`confidence_label` can be called with only `width_mm`/`height_mm`/`brand`
(e.g. right after the size is entered, before any conversion happens) - it
then judges purely on the aspect-ratio-range check. Content check and layout
checks, when available (after generation), can only ever make the label
worse, never better - a request already flagged for being outside every
validated range doesn't become trustworthy just because nothing else went
wrong.
"""
from __future__ import annotations

import json
from pathlib import Path

GOOD, REVIEW, MANUAL = "GOOD", "REVIEW", "MANUAL"
_RANK = {GOOD: 0, REVIEW: 1, MANUAL: 2}

BOUNDS_PATH = Path(__file__).with_name("confidence_bounds.json")

# Above this, a bucket's own historical position/size error is "large enough
# to warn about, even for a request that lands squarely in the validated
# range" - calibrated by eye against the dalmia numbers (median untiled
# error ~115-140mm is fine; median tiled error ~430-1900mm is not) rather
# than tuned to hit any particular pass rate.
REVIEW_ERROR_MM = 200.0


def _load_bounds() -> dict:
    if not BOUNDS_PATH.exists():
        return {}
    return json.loads(BOUNDS_PATH.read_text(encoding="utf-8"))


def _worse(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b


def confidence_label(width_mm: float, height_mm: float, brand: str,
                      content_check: dict | None = None, layout_checks: list[dict] | None = None,
                      bounds: dict | None = None) -> dict:
    """No designer file needed for `width_mm`/`height_mm`/`brand` alone -
    `content_check` (from `content_check.check_content`) and `layout_checks`
    (from `metrics.layout_checks`) are optional and only available after a
    real generation; passing them can only raise the label, never lower it.
    `bounds` overrides the on-disk `confidence_bounds.json` (tests only).
    """
    bounds = _load_bounds() if bounds is None else bounds
    reasons: list[str] = []
    label = GOOD

    brand_bounds = bounds.get(brand)
    if brand_bounds is None:
        return {"label": MANUAL, "reasons": [f"brand {brand!r} has no validated samples at all - never benchmarked"],
                "aspect_ratio": (max(width_mm, height_mm) / min(width_mm, height_mm)) if min(width_mm, height_mm) > 0 else None,
                "tiled": None, "regime": None}

    if width_mm <= 0 or height_mm <= 0:
        return {"label": MANUAL, "reasons": ["requested width/height must be positive"],
                "aspect_ratio": None, "tiled": None, "regime": None}

    aspect = max(width_mm, height_mm) / min(width_mm, height_mm)
    master = brand_bounds["master_mm"]
    tiled = _would_tile(master["w"], master["h"], width_mm, height_mm)
    regime = "tiled" if tiled else "untiled"
    rb = brand_bounds["regimes"].get(regime)

    if rb is None:
        label = _worse(label, MANUAL)
        reasons.append(f"no validated {regime} samples for {brand!r} at all - would be pure extrapolation")
    elif not (rb["aspect_min"] <= aspect <= rb["aspect_max"]):
        label = _worse(label, MANUAL)
        reasons.append(
            f"aspect ratio {aspect:.2f} is outside the validated {regime} range "
            f"[{rb['aspect_min']:.2f}, {rb['aspect_max']:.2f}] ({rb['n_samples']} sample(s)) - would be extrapolation"
        )
    else:
        if rb["median_position_error_mm"] > REVIEW_ERROR_MM or rb["median_size_error_mm"] > REVIEW_ERROR_MM:
            label = _worse(label, REVIEW)
            reasons.append(
                f"similar {regime} boards historically had a median position/size error of "
                f"{rb['median_position_error_mm']:.0f}/{rb['median_size_error_mm']:.0f}mm "
                f"(worst case {rb['max_position_error_mm']:.0f}/{rb['max_size_error_mm']:.0f}mm, "
                f"{rb['n_samples']} sample(s)) - accuracy here is inherently less certain"
            )
        else:
            reasons.append(
                f"aspect ratio {aspect:.2f} is within the validated {regime} range "
                f"[{rb['aspect_min']:.2f}, {rb['aspect_max']:.2f}], where accuracy has historically been good "
                f"(median {rb['median_position_error_mm']:.0f}/{rb['median_size_error_mm']:.0f}mm, {rb['n_samples']} sample(s))"
            )

    if content_check is not None:
        overall = content_check.get("overall")
        if overall == "CONTENT_FAIL":
            label = _worse(label, MANUAL)
            bad = [f for f in ("shop_name", "phone", "gst") if content_check.get(f, {}).get("status") == "CONTENT_FAIL"]
            reasons.append(f"content check failed on {', '.join(bad) or 'a field'} - generated text does not match what was requested")

    if layout_checks:
        hard = [c["check"] for c in layout_checks if c["status"] == "fail"]
        warn = [c["check"] for c in layout_checks if c["status"] == "warn"]
        if hard:
            label = _worse(label, MANUAL)
            reasons.append(f"layout check(s) failed: {', '.join(hard)}")
        if warn:
            label = _worse(label, REVIEW)
            reasons.append(f"layout check(s) warned: {', '.join(warn)}")

    return {"label": label, "reasons": reasons, "aspect_ratio": round(aspect, 4), "tiled": tiled, "regime": regime}


def _would_tile(master_w: float, master_h: float, new_w: float, new_h: float) -> bool:
    """Calls the exact same function `compute_layout` itself uses to decide
    tiling, so this can never silently drift from the real decision."""
    from .layout import _tile_plan

    axis, _n = _tile_plan(master_w, master_h, new_w, new_h)
    return axis is not None
