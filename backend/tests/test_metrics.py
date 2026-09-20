"""Tests for backend/tools/metrics.py's pure-Python pieces (cluster
matching, counts, layout checks, dhash/edge math) - no CorelDRAW, no real
PNGs beyond tiny in-memory synthetic ones the tests draw themselves.
"""
import math

import numpy as np
import pytest
from PIL import Image

from tools import metrics

CONFIG = metrics.load_config()


def _shape(name, role, x, y, w, h, kind="curve", text=None, font_size=None):
    d = {"name": name, "type": kind, "role": role, "x": x, "y": y, "w": w, "h": h}
    if text is not None:
        d["text"] = text
    if font_size is not None:
        d["font_size"] = font_size
    return d


def test_load_config_has_all_sections():
    cfg = metrics.load_config()
    assert set(cfg) >= {"visual", "cluster", "layout"}
    assert 0 < cfg["visual"]["pass_threshold"] <= 1


def test_dhash_identical_images_have_zero_distance():
    img = Image.new("L", (64, 64))
    px = img.load()
    for x in range(64):
        for y in range(64):
            px[x, y] = (x * 4) % 256
    ha = metrics._dhash(img)
    hb = metrics._dhash(img.copy())
    assert metrics._hamming(ha, hb) == 0


def test_dhash_distinguishes_very_different_images():
    black = Image.new("L", (64, 64), color=0)
    white = Image.new("L", (64, 64), color=255)
    # a flat image has zero gradient everywhere, so hash to identical -
    # use a checkerboard vs. its inverse instead, which does have gradients
    checker = Image.new("L", (64, 64))
    px = checker.load()
    for x in range(64):
        for y in range(64):
            px[x, y] = 255 if (x // 8 + y // 8) % 2 == 0 else 0
    inv = Image.eval(checker, lambda v: 255 - v)
    ha, hb = metrics._dhash(checker), metrics._dhash(inv)
    assert metrics._hamming(ha, hb) > 0
    assert metrics._dhash(black) is not None and metrics._dhash(white) is not None


def test_edge_similarity_identical_images_is_high(tmp_path):
    arr = np.zeros((100, 100), dtype=np.uint8)
    arr[20:80, 20:80] = 255
    img = Image.fromarray(arr)
    p1, p2 = tmp_path / "a.png", tmp_path / "b.png"
    img.save(p1)
    img.save(p2)
    assert metrics.edge_similarity(p1, p2) == pytest.approx(1.0, abs=1e-6)


def test_edge_similarity_unrelated_images_is_low(tmp_path):
    a = np.zeros((100, 100), dtype=np.uint8)
    a[20:80, 20:80] = 255
    b = np.zeros((100, 100), dtype=np.uint8)
    b[0:10, 0:10] = 255
    p1, p2 = tmp_path / "a.png", tmp_path / "b.png"
    Image.fromarray(a).save(p1)
    Image.fromarray(b).save(p2)
    assert metrics.edge_similarity(p1, p2) < 0.5


def test_cluster_compare_matches_identical_layouts():
    shapes = [
        _shape("logo_a", "logo", 0, 0, 100, 100),
        _shape("logo_b", "logo", 100, 0, 100, 100),
        _shape("shopname", "shopname", 0, 200, 300, 50),
    ]
    result = metrics.cluster_compare(shapes, shapes, 1000, 1000, CONFIG)
    assert result["ours_clusters"] == result["real_clusters"] == result["matched"]
    assert result["unmatched_ours"] == 0 and result["unmatched_real"] == 0
    assert result["max_diff_pct"] == pytest.approx(0.0)


def test_cluster_compare_flags_position_drift():
    ours = [_shape("logo_a", "logo", 0, 0, 100, 100)]
    real = [_shape("logo_a", "logo", 50, 0, 100, 100)]  # shifted 50mm on a 1000mm-wide page = 5%
    result = metrics.cluster_compare(ours, real, 1000, 1000, CONFIG)
    assert result["matched"] == 1
    assert result["max_diff_pct"] == pytest.approx(5.0, abs=0.01)


def test_cluster_compare_excludes_bg_and_frame_from_clustering():
    shapes = [
        _shape("bg", "bg", 0, 0, 1000, 1000),
        _shape("frame", "frame", 10, 10, 980, 980),
        _shape("logo", "logo", 100, 100, 50, 50),
    ]
    result = metrics.cluster_compare(shapes, shapes, 1000, 1000, CONFIG)
    assert result["ours_clusters"] == 1  # only the logo, bg/frame never enter clustering


# --------------------------------------------- Step 2: geometric_accuracy (mm)

def test_geometric_accuracy_identical_layout_is_zero_error_full_area_matched():
    shapes = [_shape("logo_a", "logo", 0, 0, 100, 60), _shape("shopname", "shopname", 0, 200, 300, 50)]
    r = metrics.geometric_accuracy(shapes, shapes, CONFIG)
    assert r["matched"] == 2 and r["unmatched_ours"] == 0 and r["unmatched_real"] == 0
    assert r["position_error_mm"] == {"max": pytest.approx(0.0), "mean": pytest.approx(0.0)}
    assert r["size_error_mm"] == {"max": pytest.approx(0.0), "mean": pytest.approx(0.0)}
    for row in r["area_matched_pct"]:
        assert row["pct"] == pytest.approx(100.0)


def test_geometric_accuracy_reports_mm_not_percent_of_page():
    # a 30mm shift is 30mm regardless of page size - cluster_compare's %-of-page number
    # would differ between a 300mm-wide and a 3000mm-wide page; this must not.
    ours = [_shape("logo_a", "logo", 30, 0, 100, 100)]
    real = [_shape("logo_a", "logo", 0, 0, 100, 100)]
    small_page = metrics.geometric_accuracy(ours, real, CONFIG)
    assert small_page["position_error_mm"]["max"] == pytest.approx(30.0)
    assert small_page["size_error_mm"]["max"] == pytest.approx(0.0)


def test_geometric_accuracy_area_weighted_tolerance_buckets():
    # a big, accurately-placed logo (90% of the real content's area) and a small,
    # badly-placed one (10%) - area_matched_pct should read close to 90%, not 50%
    # (a plain per-cluster count would say "1 of 2 clusters matched", i.e. 50%).
    ours = [_shape("big", "logo", 0, 0, 300, 300), _shape("small", "logo", 500, 500, 30, 30)]
    real = [_shape("big", "logo", 0, 0, 300, 300), _shape("small", "logo", 550, 500, 30, 30)]  # small shifted 50mm
    r = metrics.geometric_accuracy(ours, real, CONFIG)
    assert r["matched"] == 2
    tight = next(row for row in r["area_matched_pct"] if row["tolerance_mm"] == 2.0)
    loose = next(row for row in r["area_matched_pct"] if row["tolerance_mm"] == 10.0)
    big_area, small_area = 300 * 300, 30 * 30
    assert tight["pct"] == pytest.approx(big_area / (big_area + small_area) * 100, abs=0.5)  # only the big one qualifies
    # the shift is 50mm, bigger than every configured default tolerance (2/5/10mm) - the
    # small cluster never qualifies at any of them, so the loosest bucket matches the tightest
    assert loose["pct"] == pytest.approx(tight["pct"], abs=0.01)


def test_geometric_accuracy_unmatched_clusters_are_excluded_but_still_reduce_area_matched_pct():
    ours = [_shape("logo_a", "logo", 0, 0, 100, 100)]
    real = [_shape("logo_a", "logo", 0, 0, 100, 100), _shape("logo_b", "logo", 500, 500, 100, 100)]
    r = metrics.geometric_accuracy(ours, real, CONFIG)
    assert r["matched"] == 1 and r["unmatched_real"] == 1
    # only half the real content's area was even matched, let alone within tolerance
    for row in r["area_matched_pct"]:
        assert row["pct"] == pytest.approx(50.0, abs=0.1)


def test_geometric_accuracy_per_cluster_rows_carry_role_and_signed_mm_diffs():
    ours = [_shape("logo_a", "logo", 10, 5, 100, 100)]
    real = [_shape("logo_a", "logo", 0, 0, 90, 80)]
    r = metrics.geometric_accuracy(ours, real, CONFIG)
    row = r["per_cluster"][0]
    assert row["role"] == "logo"
    assert row["dx_mm"] == pytest.approx(10.0) and row["dy_mm"] == pytest.approx(5.0)
    assert row["dw_mm"] == pytest.approx(10.0) and row["dh_mm"] == pytest.approx(20.0)
    # position error is centre-to-centre, not corner-to-corner: ours' centre is (60, 55),
    # real's is (45, 40) - the sizes differ too, so this is NOT hypot(dx_mm, dy_mm).
    assert row["position_error_mm"] == pytest.approx(math.hypot(60 - 45, 55 - 40))
    assert row["size_error_mm"] == pytest.approx(math.hypot(10, 20))


def test_counts_compare_counts_text_and_shopname_together():
    shapes = [
        _shape("logo", "logo", 0, 0, 10, 10),
        _shape("t1", "text", 0, 0, 10, 10, kind="text"),
        _shape("sn", "shopname", 0, 0, 10, 10, kind="text"),
    ]
    result = metrics.counts_compare(shapes, shapes)
    assert result["ours_shapes"] == 3
    assert result["ours_text"] == 2


def test_layout_checks_flags_shape_outside_page():
    shapes = [_shape("logo", "logo", -10, 0, 50, 50)]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["within_page"]["status"] == "fail"


def test_layout_checks_passes_for_well_behaved_layout():
    shapes = [
        _shape("bg", "bg", 0, 0, 1000, 1000),
        _shape("logo", "logo", 100, 100, 200, 200),
        _shape("shopname", "shopname", 400, 100, 300, 60, kind="text", font_size=60),
    ]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["within_page"]["status"] == "pass"
    assert checks["no_cluster_overlap"]["status"] == "pass"
    assert checks["min_margin"]["status"] == "pass"
    assert checks["text_legibility"]["status"] == "pass"


def test_layout_checks_flags_overlapping_clusters():
    # An L-shaped logo cluster (a vertical bar + a horizontal bar that touch,
    # so they union-find into one cluster) has a rectangular bbox *envelope*
    # that includes the L's empty inner corner. A text shape sitting in that
    # empty corner never touches either bar individually, but its bbox is
    # still fully inside the cluster's envelope - the coarse check this
    # exercises is envelope-vs-envelope, not shape-vs-shape.
    shapes = [
        _shape("logo_bar_v", "logo", 0, 0, 40, 200),
        _shape("logo_bar_h", "logo", 0, 180, 200, 40),
        _shape("caption", "text", 100, 50, 50, 50, kind="text"),
    ]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["no_cluster_overlap"]["status"] == "fail"


def test_layout_checks_flags_small_text():
    shapes = [_shape("t", "text", 100, 100, 200, 40, kind="text", font_size=3.0)]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["text_legibility"]["status"] == "warn"


def test_layout_checks_flags_tight_margin():
    shapes = [_shape("logo", "logo", 1, 1, 50, 50)]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["min_margin"]["status"] == "warn"


def test_layout_checks_ignores_full_bleed_strip_in_margin_check():
    # a full-width accent line touching the left/right edges is a deliberate
    # edge-to-edge design element, not a misplaced object (GATE 1 feedback -
    # every dalmia board flagged a spurious 0.0mm margin from exactly this)
    shapes = [_shape("rule_line", "logo", 0, 500, 1000, 5)]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["min_margin"]["status"] == "pass"


def test_layout_checks_still_flags_tight_margin_alongside_a_full_bleed_element():
    shapes = [
        _shape("rule_line", "logo", 0, 500, 1000, 5),  # full-bleed, exempt
        _shape("badge", "logo", 1, 1, 50, 50),  # genuinely tight to the corner
    ]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["min_margin"]["status"] == "warn"


def test_layout_checks_flags_overlapping_text():
    shapes = [
        _shape("shopname", "shopname", 100, 100, 200, 50, kind="text"),
        _shape("phone", "text", 250, 110, 200, 50, kind="text"),  # overlaps the shop name
    ]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["text_overlap"]["status"] == "fail"


def test_layout_checks_passes_text_overlap_for_non_overlapping_text():
    shapes = [
        _shape("shopname", "shopname", 100, 100, 200, 50, kind="text"),
        _shape("phone", "text", 400, 100, 200, 50, kind="text"),
    ]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["text_overlap"]["status"] == "pass"


def test_layout_checks_text_overlap_ignores_non_text_clusters():
    # a logo overlapping another logo is covered by no_cluster_overlap, not text_overlap
    shapes = [
        _shape("logo_a", "logo", 100, 100, 200, 200),
        _shape("logo_b", "logo", 150, 150, 200, 200),
    ]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["text_overlap"]["status"] == "pass"


def test_layout_checks_flags_shopname_outside_bottom_bar():
    shapes = [
        _shape("footer", "text", 100, 50, 200, 30, kind="text"),
        _shape("phone", "text", 700, 60, 200, 30, kind="text"),
        _shape("shopname", "shopname", 400, 480, 200, 30, kind="text"),  # near page vertical centre, not the bottom bar
    ]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["shopname_in_bottom_bar"]["status"] == "fail"


def test_layout_checks_passes_shopname_in_bottom_bar():
    shapes = [
        _shape("footer", "text", 100, 50, 200, 30, kind="text"),
        _shape("phone", "text", 700, 60, 200, 30, kind="text"),
        _shape("shopname", "shopname", 400, 55, 200, 30, kind="text"),  # same band as the other fixed text
    ]
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["shopname_in_bottom_bar"]["status"] == "pass"


def test_layout_checks_shopname_in_bottom_bar_passes_when_nothing_to_compare():
    shapes = [_shape("logo", "logo", 100, 100, 200, 200)]  # no shopname, no other text
    checks = {c["check"]: c for c in metrics.layout_checks(shapes, 1000, 1000, CONFIG)}
    assert checks["shopname_in_bottom_bar"]["status"] == "pass"
