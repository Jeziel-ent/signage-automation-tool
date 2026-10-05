"""CorelEngine._keep_on_page: a replaced name wider than the page is scaled back inside it; one that fits is left alone."""
from app.engines import CorelEngine


class Shape:
    def __init__(self, left, w, h=100.0, bottom=50.0):
        self.LeftX, self.SizeWidth, self.SizeHeight, self.BottomY = left, w, h, bottom

    def SetSize(self, w, h):
        self.SizeWidth, self.SizeHeight = w, h


def test_wider_than_the_page_is_scaled_and_moved_inside():
    s, warnings = Shape(left=-30.0, w=560.0), []
    CorelEngine._keep_on_page(s, 500.0, warnings)
    assert s.SizeWidth == 500.0 * CorelEngine.PAGE_TEXT_MAX
    assert s.LeftX >= 500.0 * CorelEngine.PAGE_TEXT_MARGIN
    assert s.LeftX + s.SizeWidth <= 500.0 * (1 - CorelEngine.PAGE_TEXT_MARGIN) + 1e-9
    assert round(s.SizeHeight / s.SizeWidth, 6) == round(100.0 / 560.0, 6)   # uniform scale
    assert not warnings


def test_a_name_that_fits_is_untouched():
    s = Shape(left=40.0, w=400.0)
    CorelEngine._keep_on_page(s, 500.0, [])
    assert (s.LeftX, s.SizeWidth, s.SizeHeight) == (40.0, 400.0, 100.0)


def test_no_page_width_means_no_change():
    s = Shape(left=0.0, w=900.0)
    CorelEngine._keep_on_page(s, None, [])
    assert s.SizeWidth == 900.0
