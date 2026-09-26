from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app import fonts


def test_registry_names_are_normalised():
    assert fonts.parse_registry_name("Arial (TrueType)") == ["Arial"]
    assert fonts.parse_registry_name("Yu Gothic Medium & Yu Gothic UI Semibold (TrueType)") == ["Yu Gothic Medium", "Yu Gothic UI Semibold"]
    assert fonts.parse_registry_name("Nirmala UI Semilight (OpenType)") == ["Nirmala UI Semilight"]


def test_installed_fonts_falls_back_to_registry_and_caches(monkeypatch):
    monkeypatch.setattr(fonts, "_cache", None)
    monkeypatch.setattr(fonts.sys, "platform", "win32")
    monkeypatch.setattr(fonts, "_from_powershell", lambda: (_ for _ in ()).throw(RuntimeError("no powershell")))
    calls = []
    monkeypatch.setattr(fonts, "_from_registry", lambda: calls.append(1) or ["Zeta", "arial"])
    got = fonts.installed_fonts()
    assert got == {"available": True, "fonts": ["arial", "Zeta"], "source": "registry"}
    fonts.installed_fonts()
    assert len(calls) == 1                      # cached
    fonts.installed_fonts(refresh=True)
    assert len(calls) == 2


def test_unreadable_list_means_unavailable_not_an_empty_allowlist(monkeypatch):
    monkeypatch.setattr(fonts, "_cache", None)
    monkeypatch.setattr(fonts.sys, "platform", "win32")
    boom = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    monkeypatch.setattr(fonts, "_from_powershell", boom)
    monkeypatch.setattr(fonts, "_from_registry", boom)
    assert fonts.installed_fonts()["available"] is False


def test_fonts_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    monkeypatch.setattr(main_module.fonts, "_cache", {"available": True, "fonts": ["Arial"], "source": "test"})
    assert TestClient(main_module.app).get("/api/fonts").json() == {"available": True, "fonts": ["Arial"], "source": "test"}


# ------------------------------------------------------------- font files (GET /api/fonts/file)

ENTRIES = [
    ("Arial (TrueType)", "arial.ttf"),
    ("Arial Bold (TrueType)", "arialbd.ttf"),
    ("Nirmala UI & Nirmala UI Semilight (TrueType)", "Nirmala.ttc"),
    ("Book Antiqua Regular (TrueType)", "BKANT.TTF"),
    ("Some Raster (VGA res)", "some.fon"),
    ("My Font (TrueType)", r"C:\Users\x\AppData\Local\Microsoft\Windows\Fonts\MyFont.otf"),
]


def test_resolve_font_file_exact_family_and_file_name(tmp_path):
    d = str(tmp_path)
    assert fonts.resolve_font_file("Arial", ENTRIES, d) == str(tmp_path / "arial.ttf")
    assert fonts.resolve_font_file("  arial ", ENTRIES, d) == str(tmp_path / "arial.ttf")     # case/space-insensitive
    assert fonts.resolve_font_file("Arial Bold", ENTRIES, d) == str(tmp_path / "arialbd.ttf")
    assert fonts.resolve_font_file("Nirmala UI Semilight", ENTRIES, d) == str(tmp_path / "Nirmala.ttc")


def test_resolve_font_file_plain_style_word_but_never_bold_for_a_plain_family(tmp_path):
    d = str(tmp_path)
    assert fonts.resolve_font_file("Book Antiqua", ENTRIES, d) == str(tmp_path / "BKANT.TTF")
    only_bold = [("Foo Bold (TrueType)", "foob.ttf")]
    assert fonts.resolve_font_file("Foo", only_bold, d) is None


def test_resolve_font_file_rejects_unknown_empty_and_non_font_files(tmp_path):
    d = str(tmp_path)
    assert fonts.resolve_font_file("Not Installed", ENTRIES, d) is None
    assert fonts.resolve_font_file("", ENTRIES, d) is None
    assert fonts.resolve_font_file("Some Raster", ENTRIES, d) is None                        # .fon is not a web font


def test_resolve_font_file_keeps_full_paths_from_per_user_installs(tmp_path):
    assert fonts.resolve_font_file("My Font", ENTRIES, str(tmp_path)).endswith("MyFont.otf")


def test_font_file_only_serves_files_inside_a_fonts_folder(monkeypatch, tmp_path):
    fonts_dir = tmp_path / "Fonts"
    fonts_dir.mkdir()
    (fonts_dir / "good.ttf").write_bytes(b"x")
    outside = tmp_path / "secret.ttf"
    outside.write_bytes(b"x")
    monkeypatch.setattr(fonts.sys, "platform", "win32")
    monkeypatch.setattr(fonts, "_registry_entries", lambda: ([("Good (TrueType)", "good.ttf"), ("Evil (TrueType)", str(outside)),
                                                              ("Gone (TrueType)", "gone.ttf")], [str(fonts_dir)]))
    assert fonts.font_file("Good") == (fonts_dir / "good.ttf").resolve()
    assert fonts.font_file("Evil") is None          # a registry value pointing outside the fonts folders is refused
    assert fonts.font_file("Gone") is None          # registered but the file is missing


def test_font_file_endpoint(monkeypatch, tmp_path):
    main = importlib.import_module("app.main")
    f = tmp_path / "good.ttf"
    f.write_bytes(b"\x00\x01\x00\x00fontdata")
    monkeypatch.setattr(main.fonts, "font_file", lambda family: f if family == "Good" else None)
    c = TestClient(main.app)
    r = c.get("/api/fonts/file", params={"family": "Good"})
    assert r.status_code == 200 and r.content == b"\x00\x01\x00\x00fontdata"
    assert r.headers["content-type"] == "font/ttf"
    assert c.get("/api/fonts/file", params={"family": "Missing"}).status_code == 404
