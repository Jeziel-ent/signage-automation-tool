"""Evidence-based master selection (Stage 2, Phase 2.2).

Today (`main._select_shop_master`) the master is chosen by ORIENTATION first (W:H >= 1.25 -> a landscape master, else a portrait one) and only then, among masters of
that orientation, by the example library / nearest shape. For the Hangyo brand that means every portrait board (3x6, 4x7, 3x5) is made from the one portrait master
(4 X 8), although the designers' own portrait boards follow the 6 X 3 composition (Stage 1: 17 boards were built from a different master than the one the
designer's board resembles, and those boards scored 0.685 against 0.839).

This module scores EVERY registered master of a brand (both orientations) for a target board from grounded signals and says why:

  evidence  how the designers' own boards of a similar shape were made - the example library records, per designer board, the master it was built from; the
            nearest boards (log aspect + height, same orientation family, same board type preferred) vote for their master (weight exp(-distance / TAU))
  aspect    log-aspect closeness of the master's page to the target
  size      closeness of the master's height to the target's (a 10 ft master for a 3 ft board is a stretch)
  brand     the master is one the brand's library knows
  orient    a soft penalty when the master has the other orientation - evidence can outweigh it, aspect alone cannot

A designer board never votes for its own selection: `exclude` (leave-one-out) removes boards from the evidence. Explicit master choices still override this
(the caller decides); the result is a ranked list with per-signal numbers and a reason string.
Off by default in production (`SIGNAGE_MASTER_SCORE=1` enables it) until the evaluation shows an improvement.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

TAU = 0.25                 # evidence distance scale (same units as example_layout's board distance)
K_NEAREST = 4              # how many nearest designer boards vote
W_EVIDENCE, W_ASPECT, W_SIZE, W_BRAND = 0.55, 0.25, 0.10, 0.10
CROSS_ORIENTATION_PENALTY = 0.15
H_WEIGHT = 0.35            # same height weight the example library uses for board distance


def enabled() -> bool:
    return os.environ.get("SIGNAGE_MASTER_SCORE", "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class MasterCandidate:
    file: str
    page_w: float | None = None        # mm
    page_h: float | None = None
    orientation: str | None = None     # "landscape" | "portrait"
    ref: object = None                 # whatever the caller needs back (a master row / id)


@dataclass
class Score:
    file: str
    total: float
    evidence: float
    aspect: float
    size: float
    brand: float
    orientation_penalty: float
    reasons: list = field(default_factory=list)
    ref: object = None


def _ln(a: float, b: float) -> float:
    return abs(math.log(a / b)) if a > 0 and b > 0 else 9.0


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def load_evidence(brand: str | None) -> dict:
    """{designer board file: {"masters": {master file: weight}, "status": ...}} from brand_data/<brand>/master_evidence.json (INFERRED mappings: see that file's
    note). Empty when the brand has none - the example library's own master per board is used then."""
    import json
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "brand_data" / (brand or "").strip().lower() / "master_evidence.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("boards", {}) if p.is_file() else {}
    except (OSError, ValueError):
        return {}


def score_masters(lib: dict | None, candidates: list[MasterCandidate], target_w: float, target_h: float, board_type: str | None = None,
                  exclude=None, evidence: dict | None = None) -> list[Score]:
    """Ranked scores (best first) of `candidates` for a target board. `lib` = the brand's example library (or None: then only aspect / size count)."""
    from . import example_layout as ex

    skip = {exclude} if isinstance(exclude, str) else set(exclude or ())
    fam, asp = ex.family(target_w, target_h), target_w / target_h
    want_type = ex.norm_type(board_type)
    votes: dict[str, float] = {}
    nearest: list = []
    lib_files: dict[str, str] = {}
    if lib:
        masters = {m["id"]: m for m in lib.get("masters", [])}
        lib_files = {_norm(m["file"]): m["file"] for m in masters.values()}
        ranked = []
        for b in lib.get("boards", []):
            if b["file"] in skip or ex.family(b["W"], b["H"]) != fam:
                continue
            m = masters.get(b["master"])
            if m is None:
                continue
            d = _ln(b["W"] / b["H"], asp) + H_WEIGHT * _ln(b["H"], target_h)
            mismatch = 1 if want_type and ex.board_kind(b["file"]) and ex.board_kind(b["file"]) != want_type else 0
            ranked.append((round(d, 3), mismatch, b, m))
        ranked.sort(key=lambda t: (t[0], t[1]))
        for d, mismatch, b, m in ranked[:K_NEAREST]:
            w = math.exp(-d / TAU) * (0.8 if mismatch else 1.0)
            ev_masters = ((evidence or {}).get(b["file"]) or {}).get("masters") or {m["file"]: 1.0}      # inferred mapping weights, else the library's own master
            for mf, mw in ev_masters.items():
                votes[_norm(mf)] = votes.get(_norm(mf), 0.0) + w * mw
            nearest.append({"board": b["file"], "master": "/".join(ev_masters), "distance": d, "weight": round(w, 3)})
    total_votes = sum(votes.values()) or 1.0
    out: list[Score] = []
    target_orient = "landscape" if asp >= 1.25 else "portrait"
    for c in candidates:
        ev = votes.get(_norm(c.file), 0.0) / total_votes if votes else 0.0
        if c.page_w and c.page_h:
            a = math.exp(-_ln(asp, c.page_w / c.page_h) / 0.7)
            s = math.exp(-_ln(target_h, c.page_h) / 1.5)
        else:
            a = s = 0.0
        brand = 1.0 if _norm(c.file) in lib_files else 0.0
        orient = c.orientation or ("landscape" if (c.page_w or 0) >= (c.page_h or 1) else "portrait")
        pen = 0.0 if orient == target_orient else CROSS_ORIENTATION_PENALTY
        total = W_EVIDENCE * ev + W_ASPECT * a + W_SIZE * s + W_BRAND * brand - pen
        reasons = []
        if votes:
            reasons.append(f"the {len(nearest)} nearest designer boards of this shape were made from: " + ", ".join(f"{k} ({v / total_votes:.0%})" for k, v in votes.items()))
        reasons.append(f"aspect {c.page_w / c.page_h:.2f}:1 vs {asp:.2f}:1" if c.page_w and c.page_h else "master size unknown")
        if pen:
            reasons.append(f"{orient} master for a {target_orient} board (-{pen:.2f})")
        out.append(Score(c.file, round(total, 4), round(ev, 4), round(a, 4), round(s, 4), brand, pen, reasons, c.ref))
    out.sort(key=lambda t: -t.total)
    return out


def choose(lib: dict | None, candidates: list[MasterCandidate], target_w: float, target_h: float, board_type: str | None = None, exclude=None,
           evidence: dict | None = None) -> tuple[Score | None, list[Score]]:
    """(best, ranked). None when there is nothing to choose between."""
    ranked = score_masters(lib, candidates, target_w, target_h, board_type, exclude, evidence)
    return (ranked[0] if ranked else None), ranked
