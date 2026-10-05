"""app/name_check.py on small synthetic dumps."""
from app.name_check import check_names


def sh(type_, x, y, w, h, text=None):
    d = {"type": type_, "x": x, "y": y, "w": w, "h": h}
    if text is not None:
        d["text"] = text
    return d


def dump(*shapes):
    return {"page_mm": {"w": 1000, "h": 300}, "shapes": list(shapes)}


def test_a_clean_board_passes():
    assert check_names(dump(sh("text", 100, 20, 300, 60, "SHOP NAME"), sh("text", 100, 120, 300, 60, "OTHER LINE"),
                            sh("bitmap", 700, 0, 200, 250), sh("text", 900, 5, 80, 20, "ADINN/06/26"))) == {"ok": True, "issues": []}


def test_a_name_off_the_page_two_names_on_top_of_each_other_and_a_covered_name_are_flagged():
    r = check_names(dump(sh("text", 900, 20, 300, 60, "RUNS OFF"), sh("text", 100, 20, 300, 60, "ONE"), sh("text", 120, 30, 300, 60, "TWO")))
    assert not r["ok"]
    assert any("off the page" in i for i in r["issues"])
    assert any("overlaps" in i for i in r["issues"])
    r = check_names(dump(sh("text", 700, 20, 150, 60, "UNDER A PICTURE"), sh("bitmap", 650, 0, 300, 200)))
    assert r["issues"] == ["'UNDER A PICTURE' is covered by a picture"]


def test_date_stamps_and_background_sized_bitmaps_are_ignored():
    assert check_names(dump(sh("text", 10, 10, 100, 20, "ADINN/06/26"), sh("text", 50, 100, 300, 60, "NAME"), sh("bitmap", 0, 0, 1000, 300)))["ok"]
