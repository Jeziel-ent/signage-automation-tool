"""Step 1: content check (app/content_check.py) - pure logic, small synthetic
shape lists, no CorelDRAW. See that module's docstring for what "expected"
means for each field and why.
"""
from __future__ import annotations

from app.content_check import (
    CONTENT_FAIL, CONTENT_OK, NOT_CHECKED, check_content, extract_contact_values,
)


def shape(type_="text", text=None, name="", x=0, y=0, w=10, h=10):
    return {"type": type_, "text": text, "name": name, "x": x, "y": y, "w": w, "h": h}


def test_all_fields_ok_when_everything_matches():
    shapes = [
        shape(text="NR TRADERS"),
        shape(text="Phone No. 82208 20580\rGST NO. 33DFLPR6498E1ZV"),
        shape(type_="curve"),
    ]
    r = check_content(shapes, expected_name="NR TRADERS", expected_phone="82208 20580", expected_gst="33DFLPR6498E1ZV")
    assert r["shop_name"]["status"] == CONTENT_OK
    assert r["phone"]["status"] == CONTENT_OK
    assert r["gst"]["status"] == CONTENT_OK
    assert r["overall"] == CONTENT_OK


def test_nothing_requested_is_not_checked_never_a_vacuous_pass():
    shapes = [shape(text="ANYTHING"), shape(text="Phone No. 111 GST NO. ABC")]
    r = check_content(shapes)
    assert r["shop_name"]["status"] == NOT_CHECKED
    assert r["phone"]["status"] == NOT_CHECKED
    assert r["gst"]["status"] == NOT_CHECKED
    assert r["overall"] == NOT_CHECKED


def test_gst_not_requested_when_the_real_shop_has_no_gst_line_stays_not_checked():
    # e.g. dalmia's real "M Pandi" file - phone only, no GST line at all
    shapes = [shape(text="M.PANDI"), shape(text="Phone No. 94865 31551")]
    r = check_content(shapes, expected_name="M.PANDI", expected_phone="94865 31551", expected_gst=None)
    assert r["phone"]["status"] == CONTENT_OK
    assert r["gst"]["status"] == NOT_CHECKED
    assert r["overall"] == CONTENT_OK  # at least one real check passed, none failed


def test_wrong_shop_name_fails_the_exact_regression_a_visual_score_missed():
    """sensitivity_test.py's "wrong shop's board" case scored 0.990 on the visual
    metric (still PASS) - the whole reason this module exists. A board carrying
    a different shop's name must never come back CONTENT_OK."""
    ours_from_board_a = [shape(text="AHMED TRADERS"), shape(type_="curve")]
    r = check_content(ours_from_board_a, expected_name="SEETHARAMAN TRADERS")
    assert r["shop_name"]["status"] == CONTENT_FAIL
    assert r["shop_name"]["expected"] == "SEETHARAMAN TRADERS"
    assert r["shop_name"]["found"] is None  # "AHMED TRADERS" exists, but nothing reads the expected text
    assert r["overall"] == CONTENT_FAIL


def test_wrong_phone_or_gst_fails_independently():
    shapes = [shape(text="Phone No. 111 22333\rGST NO. WRONGCODE")]
    r = check_content(shapes, expected_phone="999 88777", expected_gst="WRONGCODE")
    assert r["phone"]["status"] == CONTENT_FAIL
    assert r["gst"]["status"] == CONTENT_OK
    assert r["overall"] == CONTENT_FAIL  # one bad field fails the whole board


def test_shop_name_matches_through_a_manual_wrap():
    # CorelEngine._fit_text inserts one \r line break for a name too wide for its box
    shapes = [shape(text="SAFI STEEL TRADERS\rPRIVATE LIMITED")]
    r = check_content(shapes, expected_name="SAFI STEEL TRADERS PRIVATE LIMITED")
    assert r["shop_name"]["status"] == CONTENT_OK


def test_shop_name_is_case_and_whitespace_insensitive_but_requires_an_exact_match():
    shapes = [shape(text="  nr   traders  ")]
    assert check_content(shapes, expected_name="NR Traders")["shop_name"]["status"] == CONTENT_OK
    # "NR TRADERS PVT LTD" is a different (longer) name, not just whitespace/case noise -
    # a real content mismatch, so this must fail rather than match on partial overlap.
    shapes2 = [shape(text="NR TRADERS PVT LTD")]
    assert check_content(shapes2, expected_name="NR TRADERS")["shop_name"]["status"] == CONTENT_FAIL


def test_missing_shopname_text_altogether_fails_not_not_checked():
    shapes = [shape(type_="curve"), shape(text="Phone No. 123")]
    r = check_content(shapes, expected_name="SOME SHOP")
    assert r["shop_name"]["status"] == CONTENT_FAIL
    assert r["shop_name"]["found"] is None


def test_extract_contact_values_reads_the_designers_ground_truth():
    real_shapes = [shape(text="ANY OLD NAME"), shape(text="Phone No. 82208 20580\rGST NO. 33DFLPR6498E1ZV")]
    assert extract_contact_values(real_shapes) == {"phone": "82208 20580", "gst": "33DFLPR6498E1ZV"}


def test_extract_contact_values_handles_no_gst_line():
    real_shapes = [shape(text="Phone No. 94865 31551")]
    assert extract_contact_values(real_shapes) == {"phone": "94865 31551", "gst": None}


def test_extract_contact_values_handles_no_contact_shape_at_all():
    assert extract_contact_values([shape(type_="curve")]) == {"phone": None, "gst": None}


def test_overall_is_ok_only_if_something_was_actually_checked_and_none_failed():
    shapes = [shape(text="RIGHT NAME")]
    assert check_content(shapes, expected_name="RIGHT NAME")["overall"] == CONTENT_OK
    assert check_content(shapes)["overall"] == NOT_CHECKED


# ---- address (appended onto the same contact shape as phone/GST - see
# layout._contact_replacement and content_check._address_field) ----

def test_address_ok_when_every_requested_line_is_found_in_the_contact_shape():
    shapes = [
        shape(text="NR TRADERS"),
        shape(text="Phone No. 82208 20580\rGST NO. 33DFLPR6498E1ZV\r12 Main Street\rChennai 600001"),
    ]
    r = check_content(shapes, expected_address_lines=["12 Main Street", "Chennai 600001"])
    assert r["address"]["status"] == CONTENT_OK
    assert r["address"]["expected"] == "12 Main Street\nChennai 600001"
    assert r["overall"] == CONTENT_OK


def test_address_not_checked_when_not_requested():
    shapes = [shape(text="Phone No. 111\r12 Main Street")]
    r = check_content(shapes)
    assert r["address"]["status"] == NOT_CHECKED
    assert r["address"]["expected"] is None


def test_address_fails_when_a_requested_line_is_missing():
    shapes = [shape(text="Phone No. 82208 20580")]  # no address lines were actually written
    r = check_content(shapes, expected_address_lines=["12 Main Street"])
    assert r["address"]["status"] == CONTENT_FAIL
    assert r["address"]["found"] is None
    assert r["overall"] == CONTENT_FAIL


def test_address_matches_regardless_of_case_and_cr_lf_normalization():
    shapes = [shape(text="Phone No. 111\r12 MAIN STREET\r\nchennai 600001")]
    r = check_content(shapes, expected_address_lines=["12 main street", "Chennai 600001"])
    assert r["address"]["status"] == CONTENT_OK


def test_address_alongside_phone_and_gst_all_checked_independently():
    shapes = [shape(text="Phone No. 82208 20580\rGST NO. 33DFLPR6498E1ZV\r12 Main Street")]
    r = check_content(shapes, expected_phone="82208 20580", expected_gst="33DFLPR6498E1ZV",
                       expected_address_lines=["12 Main Street"])
    assert r["phone"]["status"] == CONTENT_OK
    assert r["gst"]["status"] == CONTENT_OK
    assert r["address"]["status"] == CONTENT_OK
    assert r["overall"] == CONTENT_OK


def test_empty_address_lines_list_is_not_checked_not_a_vacuous_pass():
    shapes = [shape(text="Phone No. 111")]
    r = check_content(shapes, expected_address_lines=[])
    assert r["address"]["status"] == NOT_CHECKED
