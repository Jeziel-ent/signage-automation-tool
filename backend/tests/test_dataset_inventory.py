"""tools/dataset_inventory.py - pure logic (orientation, preview zip-extraction,
filename-safety), no CorelDRAW and no dependency on the real signage_dataset/
being present (a synthetic .cdr-shaped zip is built in tmp_path for the
extraction test). Never touches signage_dataset/ itself.
"""
from __future__ import annotations

import zipfile

from tools.dataset_inventory import (
    ORIENTATION_TOLERANCE, _safe_name, extract_preview, orientation_of,
)


def test_orientation_landscape_portrait_square():
    assert orientation_of(3048.0, 1219.2) == "landscape"
    assert orientation_of(914.4, 1219.2) == "portrait"
    assert orientation_of(1524.0, 1524.0) == "square"


def test_orientation_square_tolerance_is_not_exact_equality():
    # within tolerance of equal counts as square even if not bit-identical
    near = 1000.0 * (1 + ORIENTATION_TOLERANCE / 2)
    assert orientation_of(1000.0, near) == "square"
    # just outside tolerance is a real (if narrow) landscape/portrait
    far = 1000.0 * (1 + ORIENTATION_TOLERANCE * 3)
    assert orientation_of(far, 1000.0) == "landscape"


def test_safe_name_strips_unsafe_characters_but_keeps_readability():
    assert _safe_name("60 - 60 X 30 Inch - Nonlit - RRR TRADERS, SARASWATI TRADERS") == \
        "60_-_60_X_30_Inch_-_Nonlit_-_RRR_TRADERS__SARASWATI_TRADERS"
    assert _safe_name("") == "x"


def test_extract_preview_reads_the_zips_own_page1_png(tmp_path):
    cdr = tmp_path / "fake.cdr"
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
        "0049454e44ae426082"
    )
    with zipfile.ZipFile(cdr, "w") as z:
        z.writestr("mimetype", "application/x-cdr")
        z.writestr("previews/page1.png", png_bytes)
    out = tmp_path / "out" / "preview.png"
    err = extract_preview(cdr, out)
    assert err is None
    assert out.read_bytes() == png_bytes


def test_extract_preview_reports_a_clear_error_for_a_non_zip_file(tmp_path):
    cdr = tmp_path / "old.cdr"
    cdr.write_bytes(b"not a zip at all")
    out = tmp_path / "out" / "preview.png"
    err = extract_preview(cdr, out)
    assert err is not None and "zip" in err.lower()
    assert not out.exists()


def test_extract_preview_reports_a_clear_error_when_no_preview_entry_exists(tmp_path):
    cdr = tmp_path / "empty.cdr"
    with zipfile.ZipFile(cdr, "w") as z:
        z.writestr("mimetype", "application/x-cdr")
    out = tmp_path / "out" / "preview.png"
    err = extract_preview(cdr, out)
    assert err is not None and "no preview" in err.lower()
    assert not out.exists()
