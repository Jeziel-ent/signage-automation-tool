"""POST /api/jobs (the original single-page flow): shop validation is explicit (not `assert`, which Python strips under -O)
and the master upload is written off the event loop. Uses the mock engine and a throwaway data directory."""
from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAGE_DATA", str(tmp_path))
    monkeypatch.setenv("SIGNAGE_ENGINE", "mock")
    import app.db as db_module
    import app.main as main_module
    importlib.reload(db_module)
    importlib.reload(main_module)
    return TestClient(main_module.app)


def _post(client, shops, filename="master.cdr"):
    return client.post(
        "/api/jobs",
        files={"master": (filename, b"not a real cdr", "application/octet-stream")},
        data={"brand": "dalmia", "shops": shops if isinstance(shops, str) else json.dumps(shops)},
    )


GOOD = {"name": "Sri Kumar Stores", "width": 120, "height": 48, "unit": "in"}


def test_valid_shops_create_a_job_and_save_the_master(client, tmp_path):
    r = _post(client, [GOOD])
    assert r.status_code == 200
    job_id = r.json()["id"]
    assert (tmp_path / "jobs" / job_id / "master.cdr").read_bytes() == b"not a real cdr"


@pytest.mark.parametrize(
    "shops",
    [
        [],  # an empty list
        {"name": "x"},  # not a list
        "not json at all",
        [{**GOOD, "name": "   "}],  # blank name
        [{**GOOD, "width": 0}],
        [{**GOOD, "height": -3}],
        [{**GOOD, "width": float("nan")}],  # NaN must be rejected, not slip through a comparison
        [{**GOOD, "unit": "yards"}],
        [{"name": "no size"}],  # missing keys
        [GOOD, {**GOOD, "unit": "parsec"}],  # one bad shop rejects the whole request
    ],
)
def test_invalid_shops_are_rejected_with_400(client, shops):
    if isinstance(shops, list) and any(isinstance(s, dict) and s.get("width") != s.get("width") for s in shops):
        shops = json.dumps(shops, allow_nan=True)  # json.dumps writes NaN, json.loads reads it back
    r = _post(client, shops)
    assert r.status_code == 400
    assert "Invalid shops" in r.json()["detail"]


def test_a_non_cdr_master_is_rejected(client):
    r = _post(client, [GOOD], filename="master.pdf")
    assert r.status_code == 400
    assert ".cdr" in r.json()["detail"]
