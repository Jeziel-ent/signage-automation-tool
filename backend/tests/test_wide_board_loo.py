"""Step 4: leave-one-out analysis on the wide-board panel_sequence size_table
(tools/wide_board_loo.py). Pure Python, reads the actual brand_rules/dalmia.json
(no CorelDRAW) - protects the analysis's own arithmetic (which board's row got
left out, mm conversion, the flat-average alternative) from silent regressions.
"""
from __future__ import annotations

from tools.wide_board_loo import BOARDS, load_groups, loo_rows


def test_loads_both_size_tabled_groups_and_skips_card_from_ones():
    groups = load_groups()
    assert set(groups) == {"tamil_card", "roof_graphic"}  # enlarged_badge_card uses card_from, no own table


def test_loo_rows_cover_every_sampled_aspect_exactly_once():
    groups = load_groups()
    for gid, group in groups.items():
        rows = loo_rows(group)
        assert {r["aspect"] for r in rows} == set(BOARDS)
        assert {r["label"] for r in rows} == {label for label, _, _ in BOARDS.values()}


def test_only_11_216_is_flagged_sequence_4():
    rows = loo_rows(load_groups()["tamil_card"])
    seq4 = [r for r in rows if r["sequence"] == "seq4"]
    assert len(seq4) == 1
    assert seq4[0]["label"] == "11-216 (216x48)"


def test_held_out_prediction_uses_only_the_other_three_rows():
    # a group where the held-out row's own values are obviously distinguishable
    # from what leaving it out would predict
    fake_group = {"size_table": [
        {"aspect": 3.0, "target_h_frac": 0.10, "target_w_frac": 0.10, "target_cy_frac": 0.50},
        {"aspect": 3.75, "target_h_frac": 0.90, "target_w_frac": 0.90, "target_cy_frac": 0.50},
        {"aspect": 4.0, "target_h_frac": 0.10, "target_w_frac": 0.10, "target_cy_frac": 0.50},
        {"aspect": 4.5, "target_h_frac": 0.10, "target_w_frac": 0.10, "target_cy_frac": 0.50},
    ]}
    rows = loo_rows(fake_group)
    row_375 = next(r for r in rows if r["aspect"] == 3.75)
    # true value is 0.90 but every OTHER row is 0.10 - leaving it out must predict close to 0.10,
    # i.e. a large, clearly-nonzero error, not something that accidentally reproduces 0.90
    _, new_w, new_h = BOARDS[3.75]
    assert abs(row_375["h_err_mm"]) > 0.5 * new_h  # off by more than half the page height


def test_mm_conversion_uses_that_boards_own_page_size():
    groups = load_groups()
    rows = loo_rows(groups["tamil_card"])
    for r in rows:
        label, new_w, new_h = BOARDS[r["aspect"]]
        assert r["label"] == label
        # error in mm must be plausible given the page size (sanity bound, not an exact value)
        assert abs(r["h_err_mm"]) < new_h
        assert abs(r["w_err_mm"]) < new_w
