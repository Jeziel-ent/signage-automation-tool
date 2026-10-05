"""Small pure helpers touched by the static-analysis fixes: the "(1)" / "(2)" double-sided marker is stripped from a shop name
(no backtracking regex), and the print sheet still gets a usable font when no TrueType font can be loaded."""
from __future__ import annotations

import pytest
from PIL import ImageFont

from app import print_sheet
from tools.build_example_library import shop_name_of
from tools.example_eval import clean_name


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Sri Sai cafe (1)", "Sri Sai cafe"),
        ("Sri Sai cafe (2)  ", "Sri Sai cafe"),
        ("Sri Sai cafe", "Sri Sai cafe"),
        ("Cafe (12)", "Cafe"),
        ("Shop (A)", "Shop (A)"),  # only digits are a marker
        ("(1) Shop", "(1) Shop"),  # only a TRAILING marker
        ("", ""),
    ],
)
def test_clean_name_strips_only_a_trailing_numeric_marker(raw, expected):
    assert clean_name(raw) == expected


def test_clean_name_is_linear_on_long_whitespace():
    assert clean_name(" " * 200000 + "(1)") == ""


def test_shop_name_of_reads_the_name_from_a_designer_file_name():
    assert shop_name_of("16 - 12 X 4 Feet - Nonlit - Sri Sai cafe (1).cdr") == "Sri Sai cafe"
    assert shop_name_of("not a designer file name.cdr") is None


def test_font_falls_back_to_pillows_default_when_no_truetype_font_loads(monkeypatch):
    real_truetype = ImageFont.truetype

    def _no_named_fonts(font, *args, **kwargs):
        if isinstance(font, (str, bytes)) or hasattr(font, "__fspath__"):  # a file on disk: none are installed
            raise OSError("cannot open resource")
        return real_truetype(font, *args, **kwargs)  # Pillow's own built-in font is loaded from memory

    monkeypatch.setattr(ImageFont, "truetype", _no_named_fonts)
    monkeypatch.setattr(print_sheet, "_font_cache", {})
    font = print_sheet._font("head", 10, "Shop")
    assert font is not None
    assert font.getbbox("Shop")[2] > 0  # it can measure text, so the sheet can still be drawn
