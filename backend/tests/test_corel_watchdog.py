"""Which CorelDRAW windows the watchdog may treat as a blocking dialog (and dismiss after 20 s). Pure classification - the
window classes were read live from CorelDRAW 27.0.0.121 and 2019."""
from __future__ import annotations

import pytest

pytest.importorskip("win32gui")
from app.corel_watchdog import is_dialog  # noqa: E402


@pytest.mark.parametrize("cls", ["CorelDRAW21", "CorelDRAW27", "CorelDRAW30"])
def test_the_main_frame_of_any_version_is_never_a_dialog(cls):
    assert not is_dialog({"class": cls, "title": "CorelDRAW - Untitled", "children": ["x"]})


def test_the_hidden_helper_window_is_not_a_dialog():
    # the window the old check dismissed with WM_CLOSE on every conversion longer than 20 s
    assert not is_dialog({"class": "Internet Explorer_Hidden", "title": "", "children": []})


def test_an_untitled_window_without_controls_is_not_a_dialog():
    assert not is_dialog({"class": "SomeCorelClass", "title": "  ", "children": []})


def test_real_dialogs_are():
    assert is_dialog({"class": "#32770", "title": "Save Drawing", "children": ["OK", "Cancel"]})
    assert is_dialog({"class": "#32770", "title": "", "children": ["Font substitution", "OK"]})
