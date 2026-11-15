"""A Corel Intelligence board is reported as still converting (step "learning") until its learned edits are in, then as done."""
from __future__ import annotations

import time

from tests.test_editor_api import _converted_shop, client  # noqa: F401  (client is a fixture)

import app.main as m


def test_a_done_board_in_its_learning_phase_reports_converting_then_done(client):
    _, shop_id = _converted_shop(client)
    assert client.get(f"/api/v2/shops/{shop_id}/status").json()["status"] == "done"
    m._learning_start(shop_id)
    try:
        st = client.get(f"/api/v2/shops/{shop_id}/status").json()
        assert (st["status"], st["step"]) == ("converting", "learning")
        assert st["progress_pct"] == 97
        batch = client.get(f"/api/v2/shop-statuses?ids={shop_id}").json()[shop_id]
        assert batch["status"] == "converting" and batch["step"] == "learning"
    finally:
        m._learning_end(shop_id)
    assert client.get(f"/api/v2/shops/{shop_id}/status").json()["status"] == "done"


def test_a_learning_phase_nobody_finished_expires(client, monkeypatch):
    _, shop_id = _converted_shop(client)
    m._learning[shop_id] = time.time() - m._LEARNING_TTL_S - 1          # an agent that never answered
    assert client.get(f"/api/v2/shops/{shop_id}/status").json()["status"] == "done"
    m._learning_end(shop_id)


def test_the_phase_ends_when_the_board_has_nothing_to_replay(client):
    """_apply_learned_nested with no learned scene changes must not leave the board 'converting' forever."""
    _, shop_id = _converted_shop(client)
    m._learning_start(shop_id)
    m._apply_learned_nested(shop_id)                                      # use_intelligence is off: nothing found
    assert not m._is_learning(shop_id)
    assert client.get(f"/api/v2/shops/{shop_id}/status").json()["status"] == "done"
