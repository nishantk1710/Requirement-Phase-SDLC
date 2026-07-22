"""TC5.1-TC5.3 — Ambiguity agent (A3). Deterministic (rules) parts need no key; the LLM
explanation is exercised with the mock.

  TC5.1  the rules layer flags the CATALOGUED ambiguous requirements (recall) and does
         not over-flag clean ones.
  TC5.2  when flagged, the agent attaches a plain-language explanation AND an EARS rewrite.
  TC5.3  the rules layer is deterministic and explainable.
"""

from __future__ import annotations

import json

from rga.agents.ambiguity import flag_requirement
from rga.llm.mock import MockProvider
from rga.rules import quars

# The three catalogued ambiguous gold requirements (REQ-004 / REQ-017 / REQ-019).
AMBIGUOUS = [
    "The leave dashboard shall load quickly for all users.",
    "The approval flow shall be flexible enough to handle different departments.",
    "A new employee shall be able to apply for leave without training.",
]
CLEAN = [
    "The system shall route each submitted leave request to the employee's direct manager for approval.",
    "The system shall require multi-factor authentication for all manager and HR-admin logins.",
]


# --- TC5.1 -------------------------------------------------------------------
def test_tc5_1_ambiguity_recall_on_catalogued_set():
    prov = MockProvider()  # run_llm=False -> not called
    flagged = sum(
        1 for s in AMBIGUOUS if flag_requirement(prov, s, run_llm=False).ambiguity_flags
    )
    assert flagged == len(AMBIGUOUS)  # 3/3 -> recall 1.0 (goal >= 0.66)


def test_tc5_1_clean_requirements_not_overflagged():
    prov = MockProvider()
    for s in CLEAN:
        q = flag_requirement(prov, s, run_llm=False)
        assert [f for f in q.ambiguity_flags if f.startswith("weak-word")] == []
        assert q.testable is True
        assert q.score == 1.0


def test_tc5_1_non_ears_is_flagged():
    q = flag_requirement(MockProvider(), "Login should be fast.", run_llm=False)
    assert "not-EARS-conformant" in q.ambiguity_flags  # no 'shall'
    assert any(f.startswith("weak-word:fast") for f in q.ambiguity_flags)
    assert q.testable is False


# --- TC5.2 -------------------------------------------------------------------
def test_tc5_2_flagged_requirement_gets_explanation_and_rewrite():
    reply = json.dumps(
        {
            "explanation": "'quickly' is not measurable.",
            "rewrite": "The leave dashboard shall load within 2 seconds at the 95th percentile.",
            "testable": True,
        }
    )
    q = flag_requirement(MockProvider(responses=[reply]), AMBIGUOUS[0], run_llm=True)
    assert q.ambiguity_flags  # still flagged by rules
    assert q.ambiguity_explanation and "measurable" in q.ambiguity_explanation
    assert q.suggested_rewrite and "2 seconds" in q.suggested_rewrite
    assert q.testable is True  # LLM's assessment after rewrite


def test_tc5_2_clean_requirement_skips_llm():
    prov = MockProvider(responses=["should-not-be-used"])
    q = flag_requirement(prov, CLEAN[0], run_llm=True)
    assert q.ambiguity_flags == []
    assert prov.calls == 0  # no LLM call when nothing is flagged
    assert q.suggested_rewrite is None


# --- TC5.3 -------------------------------------------------------------------
def test_tc5_3_rules_are_deterministic():
    s = AMBIGUOUS[1]
    assert quars.find_weak_terms(s) == quars.find_weak_terms(s)
    assert quars.ears_conformance(s) == quars.ears_conformance(s)
    assert quars.flags_for(s) == quars.flags_for(s)
    # 'flexible' word + 'flexible enough' phrase both detected
    terms = quars.find_weak_terms(s)
    assert "flexible" in terms and "flexible enough" in terms


def test_tc5_3_quality_score_monotonic():
    assert quars.quality_score([], True) == 1.0
    assert quars.quality_score(["quick"], True) == 0.6
    assert quars.quality_score([], False) == 0.6
    assert quars.quality_score(["quick"], False) == 0.2


def test_tc5_ears_requires_a_response_clause():
    assert quars.ears_conformance("The system shall.") == (False, "ubiquitous")
    assert quars.ears_conformance("The system shall log every login event.")[0] is True
    assert quars.ears_conformance("Login should be fast.") == (False, None)  # no 'shall'


def test_tc5_ears_accepts_must_and_will_as_mandatory_modals():
    """L4: 'must' / 'will' are mandatory/declarative modals — not penalised as non-EARS."""
    assert quars.ears_conformance("The system must encrypt data at rest.")[0] is True
    assert quars.ears_conformance("The system will send an order confirmation email.")[0] is True
    assert quars.ears_conformance("Login might be nice.") == (False, None)  # still no mandatory modal


def test_tc5_lexicon_no_false_positives_on_clean_gold():
    from pathlib import Path

    from rga.eval.dataset import load_corpus

    corpus = load_corpus(Path(__file__).resolve().parent.parent / "datasets" / "elams")
    fp = [
        r["id"]
        for r in corpus.requirements
        if "ambiguous" not in r["difficulty"] and quars.find_weak_terms(r["statement"])
    ]
    assert fp == [], f"lexicon over-flags clean requirements: {fp}"
