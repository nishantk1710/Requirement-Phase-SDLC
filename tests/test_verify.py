"""Adversarial second-opinion verification — a strict, independent re-check of the high-risk
subset. Refuted requirements move to open-questions (never deleted); safe items are untouched."""

from __future__ import annotations

import json as _json

from rga.agents.verify import adversarial_verify
from rga.llm.mock import MockProvider
from rga.models import Priority, Quality, Requirement, RType, SourceRef


def _sref(loc="3.1", q="evidence"):
    return SourceRef(doc_id="d", source_type="brd", location=loc, raw_quote=q, start=0, end=len(q))


def _attention_req(rid="EX-1", statement="The system shall be fast."):
    # ambiguity flag (+2) + moderate confidence (+1) -> triage 'attention', not inferred, no conflict
    return Requirement(
        id=rid, project_id="P", statement=statement, rtype=RType.functional, confidence=0.6,
        quality=Quality(ambiguity_flags=["vague term: fast"]), source_refs=[_sref()],
    )


def test_refuted_high_risk_requirement_moves_to_open_questions():
    r = _attention_req()
    prov = MockProvider(responses=[_json.dumps({"holds": False, "reason": "vague, not testable"})])
    kept, oq, refuted = adversarial_verify(prov, [r], [])
    assert refuted == 1
    assert r.id not in {x.id for x in kept}          # withdrawn from firm set
    assert oq[-1]["type"] == "critic_rejected" and "second-opinion" in oq[-1]["reason"]
    assert oq[-1]["merged_refs"] and oq[-1]["merged_refs"][0]["raw_quote"] == "evidence"  # full provenance (C-M5)


def test_held_requirement_is_kept():
    r = _attention_req()
    prov = MockProvider(responses=[_json.dumps({"holds": True})])
    kept, oq, refuted = adversarial_verify(prov, [r], [])
    assert refuted == 0 and [x.id for x in kept] == ["EX-1"] and oq == []


def test_noop_without_provider():
    r = _attention_req()
    kept, oq, refuted = adversarial_verify(None, [r], [])
    assert refuted == 0 and len(kept) == 1


def _multi_src(rid="M-1", statement="The system shall let merchandisers manage products, variants and prices."):
    # triage=attention, but grounded in TWO independent documents -> cross-corroborated
    return Requirement(
        id=rid, project_id="P", statement=statement, rtype=RType.functional, confidence=0.6,
        quality=Quality(ambiguity_flags=["vague term: manage"]),
        source_refs=[
            SourceRef(doc_id="brd", source_type="brd", location="6. Representative Business Requirements",
                      raw_quote="create, edit, publish and unpublish products, variants and prices", start=0, end=60),
            SourceRef(doc_id="backlog", source_type="form", location="HG-50",
                      raw_quote="create/edit/publish products variants prices", start=0, end=44),
        ],
    )


def test_recall_guard_retains_cross_corroborated_requirement():
    """Recall floor: a requirement attested by >=2 independent documents whose topic is NOT an open
    decision is NOT deleted by the critic — it is retained (still triage=attention, so a human reviews
    it) and surfaced as a caution (critic_flag). Prevents one refutation collapsing a merged
    capability (the catalogue-CMS failure)."""
    r = _multi_src()
    prov = MockProvider(responses=[_json.dumps({"holds": False, "reason": "over-specifies variants"})])
    kept, oq, refuted = adversarial_verify(prov, [r], [])   # no open-decision notes present
    assert refuted == 0                                    # capability NOT removed
    assert r.id in {x.id for x in kept}
    assert oq[-1]["type"] == "critic_flag" and "corroborated by 2" in oq[-1]["reason"]
    assert r.provenance.get("critic_concern")              # concern recorded for the reviewer


def test_recall_guard_retains_authoritative_single_source():
    """A single-document requirement grounded in a stated Objective is an authoritative in-scope
    declaration — the critic flags its wording, it is not deleted."""
    r = Requirement(
        id="O-1", project_id="P", confidence=0.6, rtype=RType.functional,
        statement="The system shall let merchandising manage the catalogue without engineering.",
        quality=Quality(ambiguity_flags=["vague term: manage"]),
        source_refs=[SourceRef(doc_id="brd", source_type="brd", location="2. Objectives",
                               raw_quote="Let merchandising manage catalogue", start=0, end=34)],
    )
    prov = MockProvider(responses=[_json.dumps({"holds": False, "reason": "vague"})])
    kept, oq, refuted = adversarial_verify(prov, [r], [])
    assert refuted == 0 and r.id in {x.id for x in kept} and oq[-1]["type"] == "critic_flag"


def test_open_decision_refuted_even_when_corroborated():
    """The guard must NOT firm up open decisions: a cross-corroborated item whose TOPIC matches an
    existing disputed/undecided open-question is still withdrawn to critic_rejected — the critic is
    right, and it stays captured as that open decision. Deterministic (topic overlap), no LLM label."""
    r = _multi_src(statement="The system shall allow guest checkout without creating an account.")
    open_q = [{"type": "disputed",
               "statement": "Guest checkout without creating an account is disputed and open, pending sign-off."}]
    prov = MockProvider(responses=[_json.dumps({"holds": False, "reason": "BRD marks guest checkout OPEN"})])
    kept, oq, refuted = adversarial_verify(prov, [r], open_q)
    assert refuted == 1                                    # NOT protected — correctly withdrawn
    assert r.id not in {x.id for x in kept}
    assert oq[-1]["type"] == "critic_rejected"


def test_open_decision_refuted_by_reviewer_flag_when_text_match_misses():
    """Safety net: a paraphrased open item the deterministic topic-match CANNOT catch (different
    words + different source than the disputed note) is still withdrawn when the reviewer flags
    open_decision — corroboration must never firm up a decision the source leaves open."""
    r = _multi_src(statement="The system should let a shopper complete purchase without a registered profile.")
    open_q = [{"type": "disputed", "statement": "Guest checkout is disputed and open."}]  # low text overlap
    prov = MockProvider(responses=[_json.dumps(
        {"holds": False, "reason": "source leaves this open", "open_decision": True})])
    kept, oq, refuted = adversarial_verify(prov, [r], open_q)
    assert refuted == 1 and r.id not in {x.id for x in kept} and oq[-1]["type"] == "critic_rejected"


def test_only_high_risk_targeted_routine_inferred_conflict_untouched():
    routine = Requirement(
        id="R", project_id="P", statement="The system shall place an order.", rtype=RType.functional,
        confidence=0.95, priority=Priority.must, source_refs=[_sref()],
    )
    inferred = Requirement(
        id="I", project_id="P", statement="The system shall email a receipt.", rtype=RType.functional,
        confidence=0.6, inferred=True, source_refs=[_sref()],
    )
    conflicting = Requirement(
        id="C", project_id="P", statement="Checkout shall have five steps.", rtype=RType.functional,
        confidence=0.6, conflicts_with=["X"], source_refs=[_sref()],
    )
    # even though the mock would refute everything, none of these are targeted
    prov = MockProvider(responses=[_json.dumps({"holds": False})] * 5)
    kept, oq, refuted = adversarial_verify(prov, [routine, inferred, conflicting], [])
    assert refuted == 0 and len(kept) == 3 and oq == []
