"""Risk-based review triage — grades each requirement by how much human attention it needs,
so a reviewer can batch-accept the routine ones and focus on the rest."""

from __future__ import annotations

from rga.agents.triage import ATTENTION, REVIEW, ROUTINE, needs_attention, triage, triage_summary
from rga.models import Priority, Quality, Requirement, RType, SourceRef


def _req(**kw):
    base = dict(
        id="EX-1", project_id="P", statement="The system shall place an order.",
        rtype=RType.functional, confidence=0.9, priority=Priority.must,
        source_refs=[SourceRef(doc_id="d", source_type="brd", location="3.1", raw_quote="x", start=0, end=1)],
    )
    base.update(kw)
    return Requirement(**base)


def test_clean_high_confidence_is_routine():
    r = _req(confidence=0.95)
    assert triage(r)["level"] == ROUTINE
    assert needs_attention(r) is False


def test_inferred_is_attention():
    assert triage(_req(inferred=True))["level"] == ATTENTION


def test_conflict_is_attention():
    r = _req(conflicts_with=["EX-2"])
    assert triage(r)["level"] == ATTENTION and needs_attention(r) is True


def test_ambiguity_flag_raises_attention():
    t = triage(_req(quality=Quality(ambiguity_flags=["vague term: fast"])))
    assert t["level"] in (REVIEW, ATTENTION)
    assert any("ambiguity" in reason for reason in t["reasons"])


def test_moderate_confidence_is_review():
    assert triage(_req(confidence=0.6))["level"] == REVIEW


def test_coarse_traceability_adds_signal():
    r = _req(source_refs=[SourceRef(doc_id="d", source_type="brd", location="(top)", raw_quote="x", start=0, end=1)])
    assert triage(r)["level"] == REVIEW  # precise everything else, but whole-doc location -> a glance


def test_triage_summary_counts_by_level():
    reqs = [_req(confidence=0.95), _req(inferred=True), _req(confidence=0.6)]
    s = triage_summary(reqs)
    assert s[ROUTINE] == 1 and s[ATTENTION] == 1 and s[REVIEW] == 1
