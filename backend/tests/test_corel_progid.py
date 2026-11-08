"""Which CorelDRAW gets launched: ProgID discovery from the registry and the fallback to the next installed version.
Pure logic with a fake registry/COM - the real machine's registrations were checked live (see CLAUDE.md "CorelDRAW version")."""
from __future__ import annotations

import pytest

from app import corel_util as cu

GENERIC_CLSID = "{F35B0002-6F4C-417C-94C7-B06D2400E9C0}"
V27 = '"c:/Program Files/Corel/CorelDRAW Graphics Suite/27/Programs64/CorelDRW.exe" /Automation'
V21 = '"c:/Program Files/Corel/CorelDRAW Graphics Suite 2019/Programs64/CorelDRW.exe" /Automation'
# this dev machine, as read from the registry: 2025 (.27, also what the generic ProgID points at) and 2019 (.21)
REAL = [
    {"progid": "CorelDRAW.Application", "clsid": GENERIC_CLSID, "server": V27},
    {"progid": "CorelDRAW.Application.21", "clsid": "{1F880002-01A1-470B-A07E-FF9136793812}", "server": V21},
    {"progid": "CorelDRAW.Application.27", "clsid": GENERIC_CLSID, "server": V27},
]


def all_exist(path):
    return True


def test_server_exe_parses_quoted_and_unquoted_values():
    assert cu._server_exe('"c:/a b/CorelDRW.exe" /Automation') == "c:/a b/CorelDRW.exe"
    assert cu._server_exe("c:/x/CorelDRW.exe /Automation") == "c:/x/CorelDRW.exe"
    assert cu._server_exe(None) is None


def test_generic_first_then_newest_version_and_the_generics_own_server_is_not_tried_twice():
    assert cu.rank_progids(REAL, all_exist) == ["CorelDRAW.Application", "CorelDRAW.Application.21"]


def test_versions_sort_numerically_newest_first_with_no_hardcoded_list():
    entries = [{"progid": f"CorelDRAW.Application.{v}", "clsid": f"{{{v}}}", "server": f"c:/{v}/CorelDRW.exe"} for v in (9, 21, 30, 27)]
    assert cu.rank_progids(entries, all_exist) == [f"CorelDRAW.Application.{v}" for v in (30, 27, 21, 9)]


def test_a_registration_whose_executable_was_uninstalled_is_dropped():
    assert cu.rank_progids(REAL, lambda p: "2019" not in p) == ["CorelDRAW.Application"]
    # a stale generic registration (CurVer still naming a removed version): the numbered installs are what is left
    stale = [dict(REAL[0], server='"c:/removed/CorelDRW.exe"'), REAL[1]]
    assert cu.rank_progids(stale, lambda p: "removed" not in p) == ["CorelDRAW.Application.21"]


def test_unrelated_classes_are_ignored():
    entries = REAL + [{"progid": "CorelDRAW.Graphic", "clsid": "{G}", "server": "c:/x.exe"},
                      {"progid": "CorelDRAW.Application.beta", "clsid": "{B}", "server": "c:/y.exe"}]
    assert cu.rank_progids(entries, all_exist) == ["CorelDRAW.Application", "CorelDRAW.Application.21"]


def test_pin_and_unreadable_registry(monkeypatch):
    monkeypatch.setenv("SIGNAGE_COREL_PROGID", "CorelDRAW.Application.21")
    assert cu.progid_candidates() == ["CorelDRAW.Application.21"]
    monkeypatch.delenv("SIGNAGE_COREL_PROGID")

    def unreadable():
        raise OSError("access denied")

    monkeypatch.setattr(cu, "_registry_corel_entries", unreadable)
    assert cu.progid_candidates() == ["CorelDRAW.Application"]  # the old behaviour


class FakeApp:
    Version = "21.3"
    Visible = False


def _com_error(hresult):
    return Exception(hresult, "Class not registered", None, None)  # shaped like pywintypes.com_error: args[0] = HRESULT


def test_dispatch_falls_through_a_dead_progid_immediately_and_records_what_it_connected_to(monkeypatch):
    monkeypatch.setattr(cu, "progid_candidates", lambda: ["CorelDRAW.Application", "CorelDRAW.Application.21"])
    tried, slept = [], []
    pids = iter([set(), {501}])

    def fake_dispatch(timeout_s=None, progid=None):
        tried.append(progid)
        if progid == "CorelDRAW.Application":
            raise _com_error(-2147221164)       # REGDB_E_CLASSNOTREG
        return FakeApp()

    monkeypatch.setattr(cu, "_dispatch_with_timeout", fake_dispatch)
    monkeypatch.setattr(cu, "corel_pids", lambda: next(pids, {501}))
    monkeypatch.setattr(cu, "_suppress_prompts", lambda app: None)
    monkeypatch.setattr(cu, "_track_launched", lambda pid: None)
    monkeypatch.setattr(cu.time, "sleep", lambda s: slept.append(s))
    app, launched, pid = cu.dispatch_corel()
    assert tried == ["CorelDRAW.Application", "CorelDRAW.Application.21"]
    assert not slept                              # no 6 s retry wait for a server that cannot exist
    assert cu.connected == {"progid": "CorelDRAW.Application.21", "version": "21.3"}


def test_dispatch_gives_up_with_a_clear_error_when_no_version_can_start(monkeypatch):
    monkeypatch.setattr(cu, "progid_candidates", lambda: ["CorelDRAW.Application", "CorelDRAW.Application.21"])

    def never(timeout_s=None, progid=None):
        raise _com_error(-2147221005)           # CO_E_CLASSSTRING

    monkeypatch.setattr(cu, "_dispatch_with_timeout", never)
    monkeypatch.setattr(cu, "corel_pids", lambda: set())
    with pytest.raises(RuntimeError, match="no usable CorelDRAW installation"):
        cu.dispatch_corel()


# ------------------------------------------------------------------ choosing the install per file (only versions that can open it)

def test_only_installs_that_can_open_the_file_are_kept():
    kept = cu.openable_entries(REAL, 25)
    assert [e["progid"] for e in kept] == ["CorelDRAW.Application.27"]                 # the generic one and 2019 are out
    assert [e["progid"] for e in cu.openable_entries(REAL, 21)] == ["CorelDRAW.Application.21", "CorelDRAW.Application.27"]
    assert cu.openable_entries(REAL, 0) == REAL and cu.openable_entries(REAL, -3) == REAL


def test_when_nothing_is_new_enough_the_entries_are_left_alone_so_the_error_names_both_versions():
    old_only = [REAL[1]]
    assert cu.openable_entries(old_only, 25) == old_only


def test_the_agent_sets_the_minimum_through_the_environment(monkeypatch):
    monkeypatch.delenv("SIGNAGE_COREL_PROGID", raising=False)
    monkeypatch.setattr(cu, "_registry_corel_entries", lambda: REAL)
    real_rank = cu.rank_progids
    monkeypatch.setattr(cu, "rank_progids", lambda entries: real_rank(entries, all_exist))      # every server exe "exists"
    monkeypatch.delenv("SIGNAGE_COREL_MIN_MAJOR", raising=False)
    assert cu.progid_candidates() == ["CorelDRAW.Application", "CorelDRAW.Application.21"]      # no minimum: as before
    monkeypatch.setenv("SIGNAGE_COREL_MIN_MAJOR", "25")
    assert cu.progid_candidates() == ["CorelDRAW.Application.27"]                              # 2019 is never tried for a 2024 file
    monkeypatch.setenv("SIGNAGE_COREL_MIN_MAJOR", "garbage")
    assert "CorelDRAW.Application.21" in cu.progid_candidates()                                # a bad value means no filter
    monkeypatch.setenv("SIGNAGE_COREL_MIN_MAJOR", "25")
    monkeypatch.setenv("SIGNAGE_COREL_PROGID", "CorelDRAW.Application.21")                     # a pin still wins (diagnosis override)
    assert cu.progid_candidates() == ["CorelDRAW.Application.21"]
