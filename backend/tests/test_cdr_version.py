"""Saving .cdr files in the CorelDRAW 2019 (v21) format: option wiring against a fake COM document, and reading the format
back out of a saved file."""
from __future__ import annotations

import zipfile

import pytest

from app import corel_util


class FakeOptions:
    Version = None
    Overwrite = None


class FakeApp:
    def __init__(self, major=27, fail_options=False):
        self.VersionMajor = major
        self.fail_options = fail_options

    def CreateStructSaveAsOptions(self):
        if self.fail_options:
            raise RuntimeError("no such factory")
        return FakeOptions()


class FakeDoc:
    def __init__(self, app):
        self.Application = app
        self.saved = None

    def SaveAs(self, path, options):
        self.saved = (path, options)


def _cdr(path, form=b"CDRM", version=2100):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/x-vnd.corel.draw.document+zip")
        z.writestr("content/root.dat", b"RIFF\xa8\x07\x00\x00" + form + b"fver\x10\x00\x00\x00")
        z.writestr("META-INF/metadata.xml",
                   f"<r><cdr:CoreVersion>{version}</cdr:CoreVersion><cdr:AppVersion>2700</cdr:AppVersion></r>".encode())
    return path


def test_default_target_is_corel_2019_and_is_passed_to_saveas(monkeypatch):
    monkeypatch.delenv("SIGNAGE_CDR_VERSION", raising=False)
    doc = FakeDoc(FakeApp(major=27))
    assert corel_util.save_cdr(doc, "out.cdr") == 21
    path, opts = doc.saved
    assert path == "out.cdr"
    assert opts.Version == 21
    assert opts.Overwrite is True  # an options object, never None


def test_env_override_and_zero_means_current_version(monkeypatch):
    monkeypatch.setenv("SIGNAGE_CDR_VERSION", "18")
    doc = FakeDoc(FakeApp())
    assert corel_util.save_cdr(doc, "a.cdr") == 18
    assert doc.saved[1].Version == 18
    monkeypatch.setenv("SIGNAGE_CDR_VERSION", "0")
    assert corel_util.save_cdr(doc, "b.cdr") == 0
    assert doc.saved[1].Version == 0
    monkeypatch.setenv("SIGNAGE_CDR_VERSION", "garbage")
    assert corel_util.cdr_target_version() == corel_util.DEFAULT_CDR_VERSION


def test_a_corel_older_than_the_target_saves_as_itself(monkeypatch):
    monkeypatch.delenv("SIGNAGE_CDR_VERSION", raising=False)
    doc = FakeDoc(FakeApp(major=19))                     # X9 cannot write the v21 format
    assert corel_util.save_cdr(doc, "x.cdr") == 0
    assert doc.saved[1].Version == 0
    doc = FakeDoc(FakeApp(major=21))                     # 2019 itself: v21 is its own format
    assert corel_util.save_cdr(doc, "y.cdr") == 21


def test_a_failing_options_factory_fails_loudly_instead_of_writing_the_newest_format(monkeypatch):
    monkeypatch.delenv("SIGNAGE_CDR_VERSION", raising=False)
    doc = FakeDoc(FakeApp(fail_options=True))
    with pytest.raises(RuntimeError):
        corel_util.save_cdr(doc, "x.cdr")
    assert doc.saved is None                              # no silent plain SaveAs


def test_file_format_is_read_back_from_the_saved_zip(tmp_path):
    assert corel_util.cdr_file_format(_cdr(tmp_path / "v21.cdr")) == {"form": "CDRM", "version": 2100}
    assert corel_util.cdr_file_format(_cdr(tmp_path / "v27.cdr", form=b"CDRU", version=2700)) == {"form": "CDRU", "version": 2700}
    # AppVersion (the app that wrote it) must not be mistaken for the format version
    assert corel_util.cdr_file_format(_cdr(tmp_path / "x.cdr", version=2100))["version"] == 2100
    # a bare <Version> tag (what a fresh document carried in the live probe) is understood as well
    p = tmp_path / "bare.cdr"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("META-INF/metadata.xml", b"<r><a:Version>2100</a:Version><a:AppVersion>2700</a:AppVersion></r>")
    assert corel_util.cdr_file_format(p)["version"] == 2100


def test_unreadable_files_give_none_not_an_exception(tmp_path):
    p = tmp_path / "old.cdr"
    p.write_bytes(b"not a zip")
    assert corel_util.cdr_file_format(p) == {"form": None, "version": None}
    assert corel_util.cdr_file_format(tmp_path / "missing.cdr") == {"form": None, "version": None}


def test_check_warns_when_the_file_is_not_the_requested_format(tmp_path, monkeypatch):
    monkeypatch.delenv("SIGNAGE_CDR_VERSION", raising=False)
    w: list[str] = []
    ok = corel_util.check_cdr_format(_cdr(tmp_path / "ok.cdr"), w)
    assert w == []
    assert ok == {"form": "CDRM", "version": 2100, "requested": 21}
    corel_util.check_cdr_format(_cdr(tmp_path / "new.cdr", form=b"CDRU", version=2700), w)
    assert len(w) == 1
    assert "2700" in w[0]
    assert "2019" in w[0]
    w.clear()
    (tmp_path / "junk.cdr").write_bytes(b"x")
    corel_util.check_cdr_format(tmp_path / "junk.cdr", w)
    assert "unverified" in w[0]
    w.clear()
    monkeypatch.setenv("SIGNAGE_CDR_VERSION", "0")        # no down-save requested: nothing to warn about
    corel_util.check_cdr_format(_cdr(tmp_path / "cur.cdr", form=b"CDRU", version=2700), w)
    assert w == []


def test_a_per_save_version_overrides_the_configured_default(tmp_path, monkeypatch):
    """The export dialog's CDR version choice: v27 native (0) and X7 (17) for one save, without touching the env default."""
    monkeypatch.delenv("SIGNAGE_CDR_VERSION", raising=False)
    doc = FakeDoc(FakeApp(major=27))
    assert corel_util.save_cdr(doc, "x7.cdr", 17) == 17
    assert doc.saved[1].Version == 17
    assert corel_util.save_cdr(doc, "native.cdr", 0) == 0
    assert doc.saved[1].Version == 0
    assert corel_util.save_cdr(doc, "default.cdr", None) == 21
    w: list[str] = []
    assert corel_util.check_cdr_format(_cdr(tmp_path / "x7.cdr", version=1700), w, 17)["requested"] == 17
    assert w == []
    corel_util.check_cdr_format(_cdr(tmp_path / "bad.cdr", version=2100), w, 17)
    assert len(w) == 1
    assert "X7" in w[0]
    w.clear()
    corel_util.check_cdr_format(_cdr(tmp_path / "cur.cdr", form=b"CDRU", version=2700), w, 0)
    assert w == []
