"""Stage 4 wide-board composition (example_layout._wide_compose): logo pair re-scaled from the designers' wide boards.
Synthetic library + units - no CorelDRAW. The rule is flag-gated (SIGNAGE_WIDE_COMPOSE) and wide-only."""
import pytest

from app import example_layout as X


class U:
    """Stand-in for example_layout.Unit: one single-member group."""

    def __init__(self, uid, x, y, w, h):
        self.id, self.x, self.y, self.w, self.h = uid, x, y, w, h
        self.members = [self]

    @property
    def aspect(self):
        return self.w / self.h


MASTER = {"id": "m0", "elements": [
    {"k": "e0", "kind": "group", "aspect": 1.21, "n_desc": 2, "area": 0.05},
    {"k": "e3", "kind": "group", "aspect": 1.55, "n_desc": 14, "area": 0.12},
    {"k": "e4", "kind": "group", "aspect": 1.55, "n_desc": 14, "area": 0.12},
    {"k": "e1", "kind": "group", "aspect": 14.0, "n_desc": 5, "area": 0.01},
    {"k": "e2", "kind": "group", "aspect": 14.0, "n_desc": 5, "area": 0.01}]}


def wide_board(name, W, H, h=0.8, cxl=0.17, cxr=0.84):
    asp = 1.55 * h * H / W        # a logo box with the logo's own proportions, as a page fraction
    return {"file": name, "master": "m1", "W": W, "H": H, "els": {"e1": {"cx": cxl, "cy": 0.5, "w": asp, "h": h, "loose": False}},
            "copies": {"e1": [{"cx": cxr, "cy": 0.5, "w": asp, "h": h}]}, "clip": {}, "texts": {}}


LIB = {"masters": [MASTER], "boards": [
    wide_board("20x3 a.cdr", 6096, 914.4, 0.84), wide_board("20x3 b.cdr", 6096, 914.4, 0.84), wide_board("20x4 c.cdr", 6096, 1219.2, 0.72),
    {"file": "10x3.cdr", "master": "m0", "W": 3048, "H": 914.4, "els": {"e3": {"cx": .17, "cy": .5, "w": .26, "h": .57, "loose": False}}, "copies": {}, "clip": {}, "texts": {}}]}


def units(page_w=3048, page_h=914.4):
    h = 0.57 * page_h
    w = 1.55 * h
    return {"e0": U("0", 1400, 200, 400, 330), "e3": U("3", 100, 200, w, h), "e4": U("4", 2000, 200, w, h),
            "e1": U("1", 50, 800, 700, 50), "e2": U("2", 2200, 800, 700, 50)}


def boxes_for(m):
    return {u.id: (u.x, u.y, u.w, u.h, k) for k, u in m.items()}


def test_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SIGNAGE_WIDE_COMPOSE", raising=False)
    assert not X.wide_compose_enabled()
    monkeypatch.setenv("SIGNAGE_WIDE_COMPOSE", "1")
    assert X.wide_compose_enabled()


def test_logo_pair_is_the_biggest_identical_pair():
    assert X._logo_pair(MASTER, units()) == ("e3", "e4")


def test_donor_uses_nearest_wide_boards_and_respects_exclusion():
    d = X._wide_logo_donor(LIB, 6096 / 914.4, 6096, 914.4, 1.55, None)
    assert d["left"]["h"] == pytest.approx(0.84) and "10x3.cdr" not in d["donors"]            # 10x3 is not wide
    d2 = X._wide_logo_donor(LIB, 6096 / 914.4, 6096, 914.4, 1.55, {"20x3 a.cdr", "20x3 b.cdr"})
    assert d2["left"]["h"] == pytest.approx(0.72) and "20x3 a.cdr" not in d2["donors"]        # leave-one-out: excluded boards never vote


def test_compose_scales_logo_pair_without_stretching():
    m = units(6096, 914.4)
    boxes = boxes_for(m)
    rec = X._wide_compose(boxes, MASTER, m, LIB, 6096, 914.4, None)
    assert rec and rec["pair"] == ["e3", "e4"]
    for oid in ("3", "4"):
        x, y, w, h, k = boxes[oid]
        assert h == pytest.approx(0.84 * 914.4) and w / h == pytest.approx(1.55)             # donor height share, logo's own aspect
    assert boxes["3"][0] + boxes["3"][2] / 2 < 6096 / 2 < boxes["4"][0] + boxes["4"][2] / 2   # left one stays left
    assert boxes["0"] == (1400, 200, 400, 330, "e0") and boxes["1"][2] == 700                  # nothing else moved


def test_compose_caps_width_and_still_keeps_aspect():
    m = units(6096, 914.4)
    lib = {"masters": [MASTER], "boards": [wide_board("huge.cdr", 6096, 914.4, 1.9, 0.17, 0.84)]}
    boxes = boxes_for(m)
    X._wide_compose(boxes, MASTER, m, lib, 6096, 914.4, None)
    x, y, w, h, k = boxes["3"]
    assert w <= 0.30 * 6096 + 1e-6 and w / h == pytest.approx(1.55)


def test_no_donor_or_no_pair_changes_nothing():
    m = units()
    boxes = boxes_for(m)
    before = dict(boxes)
    assert X._wide_compose(boxes, MASTER, m, {"masters": [MASTER], "boards": []}, 6096, 914.4, None) is None and boxes == before
    lone = {"masters": [MASTER], "boards": [], }
    assert X._logo_pair({"elements": [MASTER["elements"][0]]}, units()) is None
