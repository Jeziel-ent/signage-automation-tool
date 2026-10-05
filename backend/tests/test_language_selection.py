"""Language selection (Both / English Only / Tamil Only), end to end without CorelDRAW.

A shop is added through the v2 API with its `language`, the server builds the engine job from the stored row
(`main._convert_job`), and the REAL `CorelEngine._process` runs that job against a fake CorelDRAW document: a master
whose shop name is written twice, an English line stacked over a Tamil line. Only process-level plumbing is stubbed
(instance launch, saving, PDF/PNG export, the bitmap cap); finding the shop-name lines, compute_layout, the text
replacement and the language step all run as in production. The test then reads what is left on the page.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app import corel_util
from app.engines import CorelEngine
from tests.test_main_v2 import _fake_cdr_bytes, client  # noqa: F401  (fixture re-export)

EN_NEW, TA_NEW = "Asian Juice Bar", "ஏசியன் ஜூஸ் பார்"
EN_OLD, TA_OLD = "Sri Kanniyamman Stores", "ஸ்ரீ கன்னியம்மன் ஸ்டோர்ஸ்"
PAGE_W, PAGE_H = 3000.0, 1000.0
# the master's shop-name block: English line on top (y 300..400), Tamil line under it (y 150..270)
EN_BOX, TA_BOX = (500.0, 300.0, 2000.0, 100.0), (500.0, 150.0, 2000.0, 120.0)
BLOCK_CY = (150.0 + 400.0) / 2


# ------------------------------------------------------------ a fake CorelDRAW document
class _Story:
    def __init__(self, text):
        self.Text, self.Font, self.Size = text, "Arial", 120.0


class _Text:
    def __init__(self, text):
        self.Story = _Story(text)


class _Shapes:
    def __init__(self, page):
        self._page = page

    @property
    def Count(self):
        return len(self._page.items)

    def Item(self, i):
        return self._page.items[i - 1]


class _Shape:
    Locked = False
    Type = CorelEngine.SHAPE_TEXT

    def __init__(self, page, name, text, box):
        self._page, self.Name, self.Text, self.Visible = page, name, _Text(text), True
        self.LeftX, self.BottomY, self.SizeWidth, self.SizeHeight = box

    def SetSize(self, w, h):
        self.SizeWidth, self.SizeHeight = w, h

    def Delete(self):
        self._page.items.remove(self)


class _Page:
    def __init__(self):
        self.SizeWidth, self.SizeHeight = PAGE_W, PAGE_H
        self.items = [_Shape(self, "ENGLISH_NAME_SHAPE", EN_OLD, EN_BOX), _Shape(self, "TAMIL_NAME_SHAPE", TA_OLD, TA_BOX)]
        self.Shapes = _Shapes(self)

    def SetSize(self, w, h):
        self.SizeWidth, self.SizeHeight = w, h

    def shape(self, name):
        return next((s for s in self.items if s.Name == name), None)


class _Doc:
    Unit = 0
    Dirty = False

    def __init__(self):
        self.ActivePage = _Page()

    def PublishToPDF(self, path):
        Path(path).write_bytes(b"%PDF-1.4 fake")

    def ExportBitmap(self, path, *a, **k):
        return type("F", (), {"Finish": lambda _self: Path(path).write_bytes(b"png")})()

    def Close(self):
        pass


@pytest.fixture
def fake_corel(monkeypatch):
    """Stub only the process plumbing around the layout; returns the document the engine will open."""
    doc = _Doc()
    app = type("App", (), {"OpenDocument": lambda _self, path: doc})()
    monkeypatch.setenv("SIGNAGE_COREL_WATCHDOG", "0")
    monkeypatch.setenv("SIGNAGE_KEEP_MASTER_OPEN", "0")
    monkeypatch.setattr(corel_util, "acquire_instance", lambda: (app, False, None))
    monkeypatch.setattr(corel_util, "release_instance", lambda pid, ok: None)
    monkeypatch.setattr(corel_util, "cleanup_orphaned_instances", lambda: None)
    monkeypatch.setattr(corel_util, "check_memory", lambda *a, **k: 8.0)
    monkeypatch.setattr(corel_util, "save_cdr", lambda d, path, version=None: Path(path).write_bytes(b"cdr") and 0)
    monkeypatch.setattr(corel_util, "check_cdr_format", lambda path, warnings, version=None: {})
    monkeypatch.setattr(corel_util, "cap_bitmap_resolution", lambda d, dpi: {"checked": 0, "resampled": 0})
    monkeypatch.setattr(CorelEngine, "_ensure_tamil_font_renders", staticmethod(lambda *a, **k: None))
    return doc


def _convert(client, tmp_path, doc, language, local=TA_NEW):
    """API add-shop with `language` -> the server's engine job -> CorelEngine._process on the fake document."""
    up = client.post("/api/v2/upload", data={"brand": "Adinn"},
                     files={"master": (f"01 - 3000 X 1000 MM - Frontlit - {EN_OLD}.cdr", _fake_cdr_bytes(), "application/octet-stream")})
    assert up.status_code == 200, up.text
    body = {"name": EN_NEW, "width": PAGE_W, "height": PAGE_H, "unit": "mm", "language": language}
    if local:
        body["shop_name_local"] = local
    shop = client.post(f"/api/v2/jobs/{up.json()['id']}/shops", json=body)
    assert shop.status_code == 200, shop.text
    import app.main as main
    job, _ = main._convert_job(shop.json()["id"])
    assert job["shop"]["master_shop_name"] == EN_OLD            # how the engine finds the master's name lines
    out = CorelEngine()._process(Path(tmp_path / "master.cdr"), job["shop"], tmp_path / "out")
    page = doc.ActivePage
    return page.shape("ENGLISH_NAME_SHAPE"), page.shape("TAMIL_NAME_SHAPE"), out["report"]


def _cy(shape):
    return shape.BottomY + shape.SizeHeight / 2


def _gone(shape):
    """Removed from the page, hidden, or emptied - any of them means the line does not print."""
    return shape is None or shape.Visible is False or not (shape.Text.Story.Text or "").strip()


# ------------------------------------------------------------ 1. English Only
def test_english_only(client, tmp_path, fake_corel):
    en, ta, report = _convert(client, tmp_path, fake_corel, "en")
    assert en is not None and en.Visible and en.Text.Story.Text == EN_NEW
    assert _gone(ta)
    # 4. the English line moves to the middle of the space the two lines shared - no gap where Tamil was
    assert _cy(en) == pytest.approx(BLOCK_CY)
    assert not report["warnings"]


# ------------------------------------------------------------ 2. Tamil Only
def test_tamil_only(client, tmp_path, fake_corel):
    en, ta, report = _convert(client, tmp_path, fake_corel, "ta")
    assert ta is not None and ta.Visible and ta.Text.Story.Text == TA_NEW
    assert _gone(en)
    assert _cy(ta) == pytest.approx(BLOCK_CY)
    assert not report["warnings"]


# ------------------------------------------------------------ 3. Both
def test_both(client, tmp_path, fake_corel):
    en, ta, report = _convert(client, tmp_path, fake_corel, "both")
    assert en.Visible and en.Text.Story.Text == EN_NEW
    assert ta.Visible and ta.Text.Story.Text == TA_NEW
    # both stay where the master had them
    assert (en.BottomY, ta.BottomY) == pytest.approx((EN_BOX[1], TA_BOX[1]))


# ------------------------------------------------------------ edge: Tamil Only with no Tamil name
def test_tamil_only_without_a_tamil_name_keeps_english(client, tmp_path, fake_corel):
    # removing the English line would leave the MASTER's own Tamil name as the only one - English is kept instead
    en, ta, report = _convert(client, tmp_path, fake_corel, "ta", local=None)
    assert en.Text.Story.Text == EN_NEW and _gone(ta)
    assert any("no Tamil name" in w for w in report["warnings"])
