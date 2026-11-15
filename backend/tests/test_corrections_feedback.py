"""Corel Intelligence feedback and automatic apply: a learned move the designer nudges is half right, one she puts back is wrong (counts double),
and the administrator's switch makes Convert use the learned changes it is sure about."""
from __future__ import annotations

from tests.test_editor_api import client  # noqa: F401  (client is a fixture)

from app import corrections, db

PW, PH = 1000.0, 400.0
BASE = {"page": {"width": PW, "height": PH}, "layers": [{"id": "L1", "children": []}]}


def _source():
    """A record that moved an object from cx 0.30 (the engine) to cx 0.50 (the lesson)."""
    return {"shop_id": "src", "record": {"changes": [{"id": "a", "action": "moved", "signature": {"kind": "shape"},
            "before": {"cx": 0.30, "cy": 0.5, "w": 0.2, "h": 0.25}, "after": {"cx": 0.50, "cy": 0.5, "w": 0.2, "h": 0.25}}], "nested": []}}


def _again(after_cx=None, action="moved"):
    """The designer's save on the learned board: the object sat at cx 0.50 (where the lesson put it) and she moved it to `after_cx`."""
    c = {"id": "z", "action": action, "signature": {"kind": "shape"}, "before": {"cx": 0.50, "cy": 0.5, "w": 0.2, "h": 0.25}}
    if after_cx is not None:
        c["after"] = {"cx": after_cx, "cy": 0.5, "w": 0.2, "h": 0.25}
    return c


def _verdict(change, extra_boxes=()):
    base = {"page": {"width": PW, "height": PH}, "layers": [{"id": "L1", "children": [
        {"id": f"n{i}", "kind": "shape", "x": (b["cx"] - b["w"] / 2) * PW, "y": (b["cy"] - b["h"] / 2) * PH, "w": b["w"] * PW, "h": b["h"] * PH}
        for i, b in enumerate(extra_boxes)]}]}
    _, verdicts = corrections.rebase_and_feedback([change], [_source()], base)
    return verdicts.get("src")


def test_an_object_left_where_the_lesson_put_it_is_kept():
    kept_box = {"cx": 0.50, "cy": 0.5, "w": 0.2, "h": 0.25}
    assert _verdict(_again(None, action="moved") | {"before": {"cx": 0.9, "cy": 0.1, "w": 0.1, "h": 0.1}, "after": {"cx": 0.9, "cy": 0.1, "w": 0.1, "h": 0.1}}, [kept_box]) == [1, 0]


def test_a_small_nudge_is_half_right():
    assert _verdict(_again(0.515)) == [0.5, 0.5]


def test_moving_it_somewhere_else_is_one_dispute():
    assert _verdict(_again(0.75)) == [0, 1.0]


def test_putting_it_back_where_the_engine_had_it_counts_double():
    assert _verdict(_again(0.30)) == [0, corrections.REVERT_WEIGHT]


def test_hiding_or_deleting_the_learned_object_is_one_dispute():
    assert _verdict(_again(None, action="deleted")) == [0, 1.0]


def test_trust_follows_fractions_and_a_reverted_lesson_is_paused_sooner():
    nudged = {"shop_id": "x", "record": {"feedback": {"boards": {"b1": [0.5, 0.5], "b2": [0.5, 0.5]}}}}
    assert corrections.feedback_of(nudged) == {"confirmed": 1.0, "disputed": 1.0}
    assert corrections.trust(nudged) == 0.5
    reverted = {"shop_id": "y", "record": {"feedback": {"boards": {"b1": [0, 2.0]}}}}
    assert corrections.trust(reverted) == 1 / 4
    assert corrections.quarantined(reverted)                    # one board that put it back is enough (2 disputes, trust 0.25)
    mild = {"shop_id": "z", "record": {"feedback": {"boards": {"b1": [0, 1]}}}}
    assert not corrections.quarantined(mild)                    # one ordinary dispute is not


def test_the_database_keeps_fractional_tallies_and_whole_ones_as_integers(client):
    db.save_correction({"shop_id": "src", "brand": "B", "master_file": "m", "page_w_mm": PW, "page_h_mm": PH, "board_type": None,
                        "changes": [], "nested": [], "text_edits": 0})
    db.set_correction_feedback("src", "b1", 0.5, 0.5)
    db.set_correction_feedback("src", "b2", 2, 0)
    boards = db.get_correction("src")["record"]["feedback"]["boards"]
    assert boards == {"b1": [0.5, 0.5], "b2": [2, 0]}
    assert isinstance(boards["b2"][0], int)


# ---------------------------------------------------------------- automatic apply

def test_automatic_apply_is_off_by_default_and_only_an_administrator_changes_it(client):
    r = client.get("/api/v2/intelligence/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["auto_apply"] is False
    assert body["auto_min_confidence"] > body["button_min_confidence"]
    on = client.put("/api/v2/intelligence/settings", json={"auto_apply": True})
    assert on.status_code == 200 and on.json()["auto_apply"] is True
    assert client.get("/api/v2/intelligence/settings").json()["auto_apply"] is True
    assert client.put("/api/v2/intelligence/settings", json={"auto_apply": "yes"}).status_code == 422
    off = client.put("/api/v2/intelligence/settings", json={"auto_apply": False})
    assert off.json()["auto_apply"] is False


def test_with_the_switch_on_a_plain_convert_gets_the_stricter_learning_and_with_it_off_nothing(client):
    import app.main as m
    row = {"use_intelligence": 0, "board_type": "GSB"}
    job = {"brand": "B"}
    used = {"job_id": "nope"}
    assert m._learned_records(row, job, used, 2000.0, 800.0) == []                  # off: only the button asks for learning
    db.set_setting(m.AUTO_KEY, "1")
    assert m._learned_records(row, job, used, 2000.0, 800.0) == []                  # on, but nothing stored for this master: still empty, no error
    assert m._auto_apply_on() is True
