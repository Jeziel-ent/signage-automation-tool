import pytest
from app.batch_import import parse_shop_lines


def test_parses_standard_line():
    r = parse_shop_lines("16 - 12 X 4 Feet - Nonlit - AL MADEENA POOJA STORE")
    assert len(r.shops) == 1 and not r.errors
    s = r.shops[0]
    assert (s.name, s.width, s.height, s.unit, s.type) == ("AL MADEENA POOJA STORE", 12.0, 4.0, "ft", "Nonlit")


def test_inch_unit_and_multiword_type():
    r = parse_shop_lines("02 - 120 X 48 Inch - 2 Nos Double Side GSB - SRI KAVI STEELS")
    s = r.shops[0]
    assert (s.width, s.height, s.unit, s.type) == (120.0, 48.0, "in", "2 Nos Double Side GSB")


def test_name_with_trailing_copy_suffix_kept_whole():
    r = parse_shop_lines("66 - 8 X 4 Feet - Nonlit - VASANTHAM ENTERPRISES - Copy")
    assert r.shops[0].name == "VASANTHAM ENTERPRISES - Copy"


def test_name_with_comma_kept_whole():
    r = parse_shop_lines("60 - 60 X 30 Inch - Nonlit - RRR TRADERS, SARASWATI TRADERS")
    assert r.shops[0].name == "RRR TRADERS, SARASWATI TRADERS"


def test_type_typo_is_passed_through_unvalidated():
    r = parse_shop_lines("28 - 10 X 3 Feet - Nonlt - YASIN NATTU MARUNDHU KADAI")
    assert r.shops[0].type == "Nonlt"


def test_multiple_lines_and_blank_lines():
    text = "16 - 12 X 4 Feet - Nonlit - A STORE\n\n23 - 13 X 2 Feet - Nonlit - B MARKET\n"
    r = parse_shop_lines(text)
    assert [s.name for s in r.shops] == ["A STORE", "B MARKET"]


def test_unparseable_line_reported_as_error():
    r = parse_shop_lines("this is not a valid line")
    assert not r.shops
    assert len(r.errors) == 1 and r.errors[0]["line"] == "this is not a valid line"


def test_bad_size_segment_is_an_error():
    r = parse_shop_lines("16 - not a size - Nonlit - A STORE")
    assert not r.shops and r.errors


def test_master_name_drops_windows_copy_suffixes():
    from app.batch_import import shop_name_from_filename, strip_copy_suffix
    assert shop_name_from_filename("02 - 3 X 6 Feet - Double Side GSB - Sri Sai cafe (1).cdr") == "Sri Sai cafe"
    assert strip_copy_suffix("Sri Sai cafe (1)") == "Sri Sai cafe"
    assert strip_copy_suffix("X (1) - Copy") == "X"
    assert strip_copy_suffix("K. V. C. & CO (R) POOJA STORE") == "K. V. C. & CO (R) POOJA STORE"   # (R) is not a copy number
