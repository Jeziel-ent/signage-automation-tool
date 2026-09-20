"""Step 1: verify that a generated board's TEXT actually says what was
requested (shop name / phone / GST), by reading the shapes back from the
already-produced dump of the generated .cdr (dump_objects.py reads them via
live COM - see corel_worker.py's `entry["ours_dump"] = com_dump(ours_cdr)`;
this module is pure Python and never touches COM itself, it only reads that
dump's JSON).

Why this exists: every metric elsewhere in this codebase (visual similarity,
geometric diff, cluster match) measures *layout*, not *content* - a board
generated for the wrong shop, with someone else's name/phone/GST, can still
score a near-perfect visual/geometric match against a DIFFERENT board of the
same size (confirmed: `sensitivity_test.py`'s "wrong shop's board" case
scored 0.990 combined visual score, PASS). This module closes that gap:
CONTENT_OK/CONTENT_FAIL/NOT_CHECKED per field, never a vacuous pass when
nothing was actually requested for that field.

What counts as "expected" (this matters, see CLAUDE.md "Content check"):
- shop_name: whatever was actually passed as `shop["name"]` at generation
  time - i.e. this checks that CorelEngine faithfully wrote the value it was
  asked to write, NOT that our chosen name matches the designer's own
  (possibly differently-scripted/transliterated) rendering of that shop's
  name - that is a separate, already-documented gap (no shop_name_local is
  requested by validate_all.py) and would make this check fail on almost
  every real board for a reason that has nothing to do with content
  correctness.
- phone / gst: the designer's own real values for that exact shop, read
  from their real file (there is no other source of truth - shop filenames
  never encode phone/GST) - these are what validate_all.py must pass as
  `shop["phone"]`/`shop["gst"]` at generation time for this check to be
  meaningful; if it doesn't (e.g. the real file hasn't been dumped yet), the
  field is correctly NOT_CHECKED, not silently assumed correct.
"""
from __future__ import annotations

from .layout import _GST_RE, _PHONE_RE, Obj, find_contact_ids

CONTENT_OK = "CONTENT_OK"
CONTENT_FAIL = "CONTENT_FAIL"
NOT_CHECKED = "NOT_CHECKED"


def _norm(s: str | None) -> str | None:
    """Whitespace-normalized, case-folded - so a name that CorelEngine wrapped
    with a manual `\\r` line break (see `CorelEngine._fit_text`) still compares
    equal to the unwrapped string it was asked to write; wrapping is a
    legitimate fit accommodation, not a content change.
    """
    if s is None:
        return None
    return " ".join(str(s).replace("\r", " ").replace("\n", " ").split()).strip().lower()


def _objs_from_shapes(shapes: list[dict]) -> list[Obj]:
    return [
        Obj(str(i), s.get("name", "") or "", s.get("type", "shape"), s["x"], s["y"], s["w"], s["h"], s.get("text"))
        for i, s in enumerate(shapes)
    ]


def _find_shopname_text(objs: list[Obj], expected_name: str) -> str | None:
    """Does any text shape read, verbatim (whitespace/case normalized), the
    requested shop name? Returns that shape's raw text, or None.

    This is deliberately an exact-match existence check, not a "which shape
    is the shopname" lookup: after generation there is no content-independent
    way to identify that shape (see this module's docstring - `_shopname_ids`
    is computed from the untouched MASTER's old text, which is no longer
    present once replaced). Existence-of-exact-match is also exactly what
    "OK" needs to mean, so locating and validating collapse into one check.
    Normalizing whitespace (not punctuation) means a manual `\\r` wrap
    `CorelEngine._fit_text` may have inserted still compares equal to the
    unwrapped string it was asked to write. On a real mismatch `found` comes
    back None - this check does not guess which different shape was
    "supposed to be" the shopname; it only confirms whether the requested
    text exists anywhere among the text shapes.
    """
    hint = _norm(expected_name)
    if not hint:
        return None
    for o in objs:
        if o.kind != "text" or not o.text:
            continue
        if _norm(o.text) == hint:
            return o.text
    return None


def extract_contact_values(shapes: list[dict]) -> dict:
    """Ground-truth phone/GST read from a dumped file's own contact text
    (`layout.find_contact_ids`'s label match, then the value each regex
    captures) - used to pull the designer's real values out of a real file's
    dump, to pass as the *requested* values for that shop's generation.
    """
    objs = _objs_from_shapes(shapes)
    ids = find_contact_ids(objs)
    phone = gst = None
    for o in objs:
        if o.id not in ids or not o.text:
            continue
        if phone is None:
            m = _PHONE_RE.search(o.text)
            if m:
                phone = m.group(2).strip()
        if gst is None:
            m = _GST_RE.search(o.text)
            if m:
                gst = m.group(2).strip()
    return {"phone": phone, "gst": gst}


def _field(field: str, expected: str | None, found: str | None) -> dict:
    if expected is None:
        return {"field": field, "status": NOT_CHECKED, "expected": None, "found": found}
    ok = _norm(expected) == _norm(found)
    return {"field": field, "status": CONTENT_OK if ok else CONTENT_FAIL, "expected": expected, "found": found}


def check_content(shapes: list[dict], expected_name: str | None = None,
                  expected_phone: str | None = None, expected_gst: str | None = None) -> dict:
    """Read `shapes` (a dumped file's flattened shape list) back and compare
    against whatever was actually requested. Any field left as None (nothing
    requested) is NOT_CHECKED - never a vacuous OK. `overall` is CONTENT_FAIL
    if any field failed, else CONTENT_OK if at least one field was checked
    and passed, else NOT_CHECKED (nothing was requested at all).
    """
    objs = _objs_from_shapes(shapes)

    name_found = _find_shopname_text(objs, expected_name) if expected_name is not None else None

    phone_found = gst_found = None
    if expected_phone is not None or expected_gst is not None:
        contact_ids = find_contact_ids(objs)
        for o in objs:
            if o.id not in contact_ids or not o.text:
                continue
            if expected_phone is not None and phone_found is None:
                m = _PHONE_RE.search(o.text)
                if m:
                    phone_found = m.group(2).strip()
            if expected_gst is not None and gst_found is None:
                m = _GST_RE.search(o.text)
                if m:
                    gst_found = m.group(2).strip()

    fields = {
        "shop_name": _field("shop_name", expected_name, name_found),
        "phone": _field("phone", expected_phone, phone_found),
        "gst": _field("gst", expected_gst, gst_found),
    }
    statuses = {f["status"] for f in fields.values()}
    if CONTENT_FAIL in statuses:
        overall = CONTENT_FAIL
    elif CONTENT_OK in statuses:
        overall = CONTENT_OK
    else:
        overall = NOT_CHECKED
    return {**fields, "overall": overall}
