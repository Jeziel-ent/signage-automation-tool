"""Missing-font substitution: the per-shop mapping API, its use in exports, and the CorelDRAW-side font swap."""
from __future__ import annotations

import pytest

from app import export_replay as er
from app import main
from tests.test_editor_api import _converted_shop, _export, _scene, _wait_export, client  # noqa: F401

INSTALLED = {"available": True, "fonts": ["Arial", "Nirmala UI", "Times New Roman"]}


@pytest.fixture()
def installed(monkeypatch):
    monkeypatch.setattr(main.fonts, "installed_fonts", lambda refresh=False: INSTALLED)


def test_permanent_substitution_is_saved_listed_and_removed(client, installed):
    job, shop = _converted_shop(client)
    post = lambda **kw: client.post("/api/fonts/substitute", json={"shop_id": shop, **kw})  # noqa: E731
    r = post(original_font="Copperplate Gothic Bold", substitute_font="arial", is_permanent=True, project_id="22071195")
    assert r.status_code == 200 and r.json()["saved"] is True
    assert r.json()["substitute_font"] == "Arial"                              # the installed family's own spelling
    assert client.get(f"/api/fonts/substitutions?shop_id={shop}").json()["substitutions"] == {"Copperplate Gothic Bold": "Arial"}
    post(original_font="Copperplate Gothic Bold", substitute_font="Times New Roman", is_permanent=True)
    assert main.db.get_font_substitutions(shop) == {"Copperplate Gothic Bold": "Times New Roman"}   # replaced, not duplicated
    r = client.delete("/api/fonts/substitute", params={"shop_id": shop, "original_font": "Copperplate Gothic Bold"})
    assert r.json() == {"deleted": True, "substitutions": {}}


def test_temporary_is_not_stored_and_bad_requests_are_refused(client, installed):
    job, shop = _converted_shop(client)
    post = lambda **kw: client.post("/api/fonts/substitute", json={"shop_id": shop, **kw})  # noqa: E731
    r = post(original_font="AvantGarde-Demi", substitute_font="Arial", is_permanent=False)
    assert r.status_code == 200 and r.json()["saved"] is False and main.db.get_font_substitutions(shop) == {}
    assert post(original_font="AvantGarde-Demi", substitute_font="Copperplate Gothic Bold").status_code == 422   # not installed
    assert post(original_font="Arial", substitute_font="arial").status_code == 422                                  # same font
    assert post(original_font=" ", substitute_font="Arial").status_code == 422
    assert client.post("/api/fonts/substitute", json={"shop_id": "nope", "original_font": "X", "substitute_font": "Arial"}).status_code == 404
    assert client.get("/api/fonts/substitutions?shop_id=nope").status_code == 404


def test_deleting_a_shop_forgets_its_substitutions(client, installed):
    job, shop = _converted_shop(client)
    client.post("/api/fonts/substitute", json={"shop_id": shop, "original_font": "X", "substitute_font": "Arial"})
    main.db.delete_shop(shop)
    assert main.db.get_font_substitutions(shop) == {}


def test_an_export_carries_the_shops_substitutions(client, installed):
    job, shop = _converted_shop(client)
    _scene(client, job, shop)
    client.post("/api/fonts/substitute", json={"shop_id": shop, "original_font": "Copperplate Gothic Bold", "substitute_font": "Arial"})
    export_id = _export(client, job, shop, ["png"])["export_id"]
    done = _wait_export(client, job, shop, export_id)
    assert done["report"]["font_substitutions"]["Copperplate Gothic Bold"]["to"] == "Arial"


# ------------------------------------------------------------------ CorelDRAW side (fake COM)

class _Story:
    def __init__(self, font, accepts=True, mixed=False):
        self._font, self.accepts, self.mixed = font, accepts, mixed

    @property
    def Font(self):
        if self.mixed:
            raise RuntimeError("mixed fonts in one run")          # what COM does for a multi-font text
        return self._font

    @Font.setter
    def Font(self, v):
        if self.accepts:                                            # CorelDRAW ignores an uninstalled font silently
            self._font = v


class _Shape:
    def __init__(self, sid, typ, story=None, kids=()):
        self.StaticID, self.Type, self.kids, self.PowerClip = sid, typ, list(kids), None
        self.Text = type("T", (), {"Story": story})() if story else None

    @property
    def Shapes(self):
        return _Coll(self.kids)


class _Coll:
    def __init__(self, items):
        self.items, self.Count = items, len(items)

    def Item(self, i):
        return self.items[i - 1]


class _Page:
    def __init__(self, shapes):
        layer = type("L", (), {"IsSpecialLayer": False, "Shapes": _Coll(shapes)})()
        self.Layers = _Coll([layer])


def test_apply_font_substitutions_swaps_matching_text_and_reads_it_back():
    top = _Story("Copperplate Gothic Bold")
    nested = _Story("copperplate gothic bold")                      # matched case-insensitively
    other = _Story("Arial")
    stuck = _Story("AvantGarde-Demi", accepts=False)
    mixed = _Story(None, mixed=True)
    page = _Page([_Shape(1, 6, top), _Shape(2, 7, kids=[_Shape(3, 6, nested), _Shape(4, 3)]),
                  _Shape(5, 6, other), _Shape(6, 6, stuck), _Shape(7, 6, mixed)])
    warnings = []
    out = er.apply_font_substitutions(page, {"Copperplate Gothic Bold": "Arial", "AvantGarde-Demi": "Nirmala UI"}, warnings)
    assert top.Font == "Arial" and nested.Font == "Arial" and other.Font == "Arial"
    assert out["Copperplate Gothic Bold"] == {"to": "Arial", "changed": 2, "not_applied": 0}
    assert out["AvantGarde-Demi"] == {"to": "Nirmala UI", "changed": 0, "not_applied": 1}
    assert out["_mixed_font_texts_skipped"] == 1
    assert len(warnings) == 1 and "AvantGarde-Demi" in warnings[0] and "installed" in warnings[0]
    assert er.apply_font_substitutions(page, {}, warnings) == {}
