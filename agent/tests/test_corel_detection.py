"""CorelDRAW detection from the registry, on a fake `winreg` (no CorelDRAW, any OS)."""
from __future__ import annotations

import sys
import types

from signage_agent import corel


class _Key:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_winreg(tree: dict[str, str], top: list[str]):
    """tree: registry path -> default value; top: the names directly under HKEY_CLASSES_ROOT."""
    m = types.ModuleType("winreg")
    m.HKEY_CLASSES_ROOT = "ROOT"

    def open_key(root, path, *a):
        if root == "ROOT" and path == "":
            return _Key("")
        if path in tree:
            return _Key(path)
        raise OSError(path)

    def enum_key(key, i):
        if key.path != "" or i >= len(top):
            raise OSError
        return top[i]

    m.OpenKey, m.EnumKey = open_key, enum_key
    m.QueryValue = lambda key, sub: tree[key.path]
    return m


def _setup(monkeypatch, tmp_path, versions, extra_top=()):
    tree, top = {}, list(extra_top)
    for major in versions:
        exe = tmp_path / f"cdr{major}.exe"
        exe.write_bytes(b"x")
        clsid = "{ID%d}" % major
        top.append(f"CorelDRAW.Application.{major}")
        tree[f"CorelDRAW.Application.{major}\CLSID"] = clsid
        tree[f"CLSID\{clsid}\LocalServer32"] = f'"{exe}" /Automation'
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(tree, top))
    monkeypatch.setattr(corel, "_file_version", lambda p: "0.0.0.0")


def test_installs_are_listed_newest_first(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [21, 27, 24], extra_top=["Word.Document", "CorelDRAW.Application"])   # generic / unrelated names ignored
    found = corel.find_installs()
    assert [i["major"] for i in found] == [27, 24, 21]
    assert found[0]["progid"] == "CorelDRAW.Application.27" and found[0]["exe"].endswith("cdr27.exe")


def test_a_registered_version_whose_program_is_gone_is_skipped(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [21, 27])
    (tmp_path / "cdr27.exe").unlink()
    assert [i["major"] for i in corel.find_installs()] == [21]


def test_no_corel_or_not_windows_gives_an_empty_list(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, [])
    assert corel.find_installs() == []
    monkeypatch.setattr(sys, "platform", "linux")
    assert corel.find_installs() == []


def test_server_path_handles_quoted_and_bare_commands():
    tree = {"P\CLSID": "{X}", "CLSID\{X}\LocalServer32": r'"C:\Program Files\Corel\CorelDRAW.exe" -Embedding'}
    assert corel._server_path(_fake_winreg(tree, []), "P").endswith("CorelDRAW.exe")
    tree["CLSID\{X}\LocalServer32"] = r"C:\Corel\Draw.exe"
    assert corel._server_path(_fake_winreg(tree, []), "P") == r"C:\Corel\Draw.exe"
    assert corel._server_path(_fake_winreg({}, []), "P") is None
