"""corel_util.cap_bitmap_resolution against a fake CorelDRAW document that models the behaviour verified live on the real
199 MB master: Bitmap.Resample keeps the image's own dpi, so it SHRINKS the shape and moves it - the function must put the
box back. No CorelDRAW needed."""
from __future__ import annotations

import pytest

from app import corel_util

MM = 25.4


class FakeShapes:
    def __init__(self, items):
        self.items = list(items)

    @property
    def Count(self):
        return len(self.items)

    def Item(self, i):  # CorelDRAW collections are 1-based
        return self.items[i - 1]


class FakeBitmap:
    def __init__(self, shape, px_w, px_h, fail=False):
        self.shape, self.SizeWidth, self.SizeHeight, self.fail = shape, px_w, px_h, fail
        self.calls = []

    def Resample(self, w, h, anti_alias, rx, ry):
        if self.fail:
            raise RuntimeError("COM error")
        self.calls.append((w, h, anti_alias))
        # the real behaviour: the image keeps its dpi, so the placed box shrinks with the pixel count, about its corner
        s = self.shape
        s.SizeWidth *= w / self.SizeWidth
        s.SizeHeight *= h / self.SizeHeight
        s.LeftX += 40.0
        s.BottomY += 25.0
        self.SizeWidth, self.SizeHeight = w, h


class FakeShape:
    def __init__(self, type_, x=0.0, y=0.0, w=0.0, h=0.0, rot=0.0, children=(), powerclip=()):
        self.Type, self.LeftX, self.BottomY, self.SizeWidth, self.SizeHeight = type_, x, y, w, h
        self.RotationAngle = rot
        self.Shapes = FakeShapes(children)
        self.PowerClip = type("PC", (), {"Shapes": FakeShapes(powerclip)})() if powerclip else None
        self.Bitmap = None

    def SetSize(self, w, h):
        self.SizeWidth, self.SizeHeight = w, h


def bitmap(px_w, px_h, w_in, h_in, x=10.0, y=20.0, rot=0.0, fail=False):
    s = FakeShape(5, x, y, w_in * MM, h_in * MM, rot)
    s.Bitmap = FakeBitmap(s, px_w, px_h, fail)
    return s


class FakeLayer:
    def __init__(self, shapes, special=False):
        self.Shapes = FakeShapes(shapes)
        self.IsSpecialLayer = special


class FakeDoc:
    def __init__(self, layers):
        self.Unit = None
        self.ActivePage = type("Page", (), {"Layers": layers})()


def box(s):
    return (round(s.LeftX, 6), round(s.BottomY, 6), round(s.SizeWidth, 6), round(s.SizeHeight, 6))


def test_oversampled_bitmap_is_capped_and_keeps_its_box():
    # a 125 in master photo shrunk onto a 10 in board: 3117 px across 1.25 in = ~2500 dpi
    s = bitmap(3117, 6960, 1.25, 2.78)
    before = box(s)
    stats = corel_util.cap_bitmap_resolution(FakeDoc([FakeLayer([s])]), 300)
    assert s.Bitmap.calls == [(375, 834, True)]  # 1.25 in x 300, 2.78 in x 300
    assert box(s) == before  # Resample shrank and moved it; the function put it back
    assert stats["resampled"] == 1 and stats["pixels_before"] == 3117 * 6960 and stats["pixels_after"] == 375 * 834


def test_bitmaps_at_or_under_the_cap_are_never_touched_or_upsampled():
    full_size = bitmap(14937, 4900, 149.4, 49.0)  # the master's own 100 dpi background at full size
    exactly = bitmap(600, 300, 2.0, 1.0)  # exactly 300 dpi
    one_axis_low = bitmap(3000, 200, 2.0, 1.0)  # 1500 x 200 dpi: capping would distort / upsample one axis
    stats = corel_util.cap_bitmap_resolution(FakeDoc([FakeLayer([full_size, exactly, one_axis_low])]), 300)
    assert [b.Bitmap.calls for b in (full_size, exactly, one_axis_low)] == [[], [], []]
    assert stats == {"checked": 3, "resampled": 0, "skipped": 0, "pixels_before": 0, "pixels_after": 0}


def test_bitmaps_inside_groups_and_powerclips_are_found():
    in_group = bitmap(4000, 2000, 1.0, 0.5)
    in_clip = bitmap(4000, 2000, 1.0, 0.5)
    group = FakeShape(7, children=[in_group])
    clipper = FakeShape(3, powerclip=[in_clip])  # a curve holding a PowerClip
    stats = corel_util.cap_bitmap_resolution(FakeDoc([FakeLayer([group, clipper])]), 300)
    assert stats["resampled"] == 2 and in_group.Bitmap.calls and in_clip.Bitmap.calls


def test_rotated_bitmaps_and_failures_are_skipped_and_left_alone():
    rotated = bitmap(4000, 2000, 1.0, 0.5, rot=15.0)
    broken = bitmap(4000, 2000, 1.0, 0.5, fail=True)
    ok = bitmap(4000, 2000, 1.0, 0.5)
    before = box(broken)
    stats = corel_util.cap_bitmap_resolution(FakeDoc([FakeLayer([rotated, broken, ok])]), 300)
    assert rotated.Bitmap.calls == [] and box(broken) == before
    assert stats["skipped"] == 2 and stats["resampled"] == 1


def test_special_layers_are_ignored_and_a_zero_cap_disables_everything():
    guides = bitmap(4000, 2000, 1.0, 0.5)
    doc = FakeDoc([FakeLayer([guides], special=True)])
    assert corel_util.cap_bitmap_resolution(doc, 300)["checked"] == 0
    s = bitmap(4000, 2000, 1.0, 0.5)
    assert corel_util.cap_bitmap_resolution(FakeDoc([FakeLayer([s])]), 0)["checked"] == 0 and s.Bitmap.calls == []


@pytest.mark.parametrize("env, expected", [(None, 300), ("150", 150), ("0", 0), ("junk", 300), ("-5", 0)])
def test_max_bitmap_dpi_env(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("SIGNAGE_MAX_BITMAP_DPI", raising=False)
    else:
        monkeypatch.setenv("SIGNAGE_MAX_BITMAP_DPI", env)
    assert corel_util.max_bitmap_dpi() == expected
