"""TC4.1-TC4.4 — the evaluation harness, deterministic (no API key).

  TC4.1  the scorer reproduces HUMAN-labelled verdicts on a small hand-scored set
         (captured / partial / missed) — the scorer itself is trustworthy.
  TC4.2  scoring the sealed split touches ONLY that split (no leakage).
  TC4.3  the human baseline records and loads.
  TC4.4  the data-driven go/no-go bar behaves correctly (incl. 'pending' when no human).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rga.agents.grounding import locate_span
from rga.eval.baseline import HumanBaseline, go_no_go, load_baseline, save_baseline
from rga.eval.dataset import Corpus, Doc
from rga.eval.scorer import score
from rga.models import Requirement, RType, SourceRef

TEXT = (
    "The system shall allow login. Passwords must be at least 8 characters. "
    "The system shall log out idle users. Admins can export reports."
)


def _gold(rid, statement, quote, rtype="functional", implicit=False, difficulty=("explicit",)):
    return {
        "id": rid,
        "statement": statement,
        "rtype": rtype,
        "nfr_category": None,
        "feature": None,
        "difficulty": list(difficulty),
        "implicit": implicit,
        "duplicate_of": None,
        "conflicts_with": [],
        "source": [{"doc_id": "d1", "location": "x", "quote": quote}],
    }


@pytest.fixture(scope="module")
def corpus():
    reqs = [
        _gold("G1", "Users can log in.", "The system shall allow login."),
        _gold("G2", "Passwords are 8+ chars.", "Passwords must be at least 8 characters.", rtype="business"),
        _gold("G3", "Idle users are logged out.", "The system shall log out idle users.", implicit=True, difficulty=("implicit",)),
        _gold("G4", "Admins can export reports.", "Admins can export reports."),  # will be MISSED
    ]
    docs = {"d1": Doc(doc_id="d1", source_type="brd", file="d1", text=TEXT)}
    return Corpus(domain="t", path=Path("."), docs=docs, requirements=reqs, dev=["G1", "G3", "G4"], test=["G2"])


def _ext(rid, start, end):
    return Requirement(
        id=rid,
        project_id="P",
        statement=TEXT[start:end],
        rtype=RType.functional,
        source_refs=[SourceRef(doc_id="d1", source_type="brd", location="x", raw_quote=TEXT[start:end], start=start, end=end)],
    )


# --- TC4.1 -------------------------------------------------------------------
def test_tc4_1_scorer_matches_hand_labels(corpus):
    g1 = locate_span("The system shall allow login.", TEXT)
    g2 = locate_span("Passwords must be at least 8 characters.", TEXT)
    g3 = locate_span("The system shall log out idle users.", TEXT)
    # extracted: fully cover G1 & G2; cover ~30% of G3 (partial); nothing for G4
    partial_end = g3[0] + max(1, int(0.3 * (g3[1] - g3[0])))
    extracted = [
        _ext("E1", g1[0], g1[1]),
        _ext("E2", g2[0], g2[1]),
        _ext("E3", g3[0], partial_end),
    ]
    result = score(extracted, corpus)
    v = result["verdicts"]
    # human-adjudicated expectations:
    assert v["G1"]["status"] == "captured"
    assert v["G2"]["status"] == "captured"
    assert v["G3"]["status"] == "partial"
    assert v["G4"]["status"] == "missed"
    # explicit set = G1,G2,G4 -> 2 captured / 3
    assert result["recall_explicit"] == pytest.approx(2 / 3, abs=0.01)
    assert result["recall_implicit"] == pytest.approx(0.5, abs=0.01)  # G3 partial
    assert result["precision_grounded"] == 1.0  # all 3 extracted map to gold


# --- TC4.2 -------------------------------------------------------------------
def test_tc4_2_sealed_split_has_no_leakage(corpus):
    result = score([], corpus, split_ids=set(corpus.test))
    assert result["n_target"] == 1
    assert set(result["verdicts"].keys()) == {"G2"}  # only the sealed test req scored
    assert "G1" not in result["verdicts"] and "G3" not in result["verdicts"]


# --- TC4.3 -------------------------------------------------------------------
def test_tc4_3_human_baseline_roundtrip(tmp_path):
    hb = HumanBaseline(recall=0.88, minutes=45, reviewer="BA-1", notes="manual pass")
    p = tmp_path / "baseline.json"
    save_baseline(p, hb)
    loaded = load_baseline(p)
    assert loaded is not None
    assert loaded.recall == 0.88 and loaded.minutes == 45 and loaded.reviewer == "BA-1"
    assert load_baseline(tmp_path / "nope.json") is None


# --- TC4.4 -------------------------------------------------------------------
def test_tc4_4_go_no_go_bar():
    # no/pending human baseline -> pending (never green-by-default)
    assert go_no_go(0.9, 10, None)["verdict"] == "pending_human_baseline"
    assert go_no_go(0.9, 10, HumanBaseline(recall=0.9, minutes=60, pending=True))["verdict"] == "pending_human_baseline"

    hb = HumanBaseline(recall=0.90, minutes=60)  # human found 90%, took 60 min
    # required = 0.9 * 0.90 = 0.81
    assert go_no_go(0.85, 20, hb)["verdict"] == "pass"       # >=0.81 and faster
    assert go_no_go(0.85, 90, hb)["verdict"] == "fail" or go_no_go(0.85, 90, hb)["time_ok"] is False
    assert go_no_go(0.65, 20, hb)["verdict"] == "marginal"   # >=0.75*0.81 but <0.81
    assert go_no_go(0.30, 20, hb)["verdict"] == "fail"       # well below
