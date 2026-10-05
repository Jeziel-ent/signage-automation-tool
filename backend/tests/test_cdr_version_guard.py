"""A .cdr saved by a newer CorelDRAW than the running one gets a clear error instead of 'Failed to open document'."""
import zipfile

import pytest

from app import corel_util


def make_cdr(path, core):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/metadata.xml", f"<x><cdr:CoreVersion>{core}</cdr:CoreVersion></x>")
    return path


class App:
    def __init__(self, major):
        self.VersionMajor = major


def test_reads_the_saving_version(tmp_path):
    assert corel_util.cdr_core_version(make_cdr(tmp_path / "a.cdr", 2510)) == pytest.approx(25.1)
    assert corel_util.cdr_core_version(make_cdr(tmp_path / "b.cdr", 2100)) == pytest.approx(21.0)


def test_unreadable_files_have_no_version(tmp_path):
    (tmp_path / "old.cdr").write_bytes(b"RIFFCDRB")        # a pre-X4 file is not a zip
    assert corel_util.cdr_core_version(tmp_path / "old.cdr") is None
    assert corel_util.cdr_core_version(tmp_path / "missing.cdr") is None


def test_newer_file_is_refused_with_both_versions(tmp_path):
    newer = make_cdr(tmp_path / "n.cdr", 2510)
    with pytest.raises(RuntimeError, match=r"version 25\.1.*version 21"):
        corel_util.check_file_not_newer(newer, App(21))


def test_same_or_older_file_passes(tmp_path):
    corel_util.check_file_not_newer(make_cdr(tmp_path / "s.cdr", 2100), App(21))
    corel_util.check_file_not_newer(make_cdr(tmp_path / "o.cdr", 1700), App(21))
    corel_util.check_file_not_newer(tmp_path / "unknown.cdr", App(21))          # unreadable -> let CorelDRAW decide
