"""Part H — Appendix C reconciliation: suppress open items already covered by an approved
requirement, drop pointer/absorbed demotions, and NEVER touch the approved set (recall guard)."""

from __future__ import annotations

from rga.generate.open_questions import reconcile_open_questions
from rga.models import Requirement, RType, SourceRef


def _req(rid: str, s: str) -> Requirement:
    return Requirement(id=rid, project_id="P", statement=s, rtype=RType.functional,
                       source_refs=[SourceRef(doc_id="d", source_type="brd", location="1",
                                              raw_quote=s, start=0, end=1)])


def test_suppresses_covered_and_pointer_keeps_decisions_and_gaps():
    approved = [_req("R1", "The system shall let a customer reset their password via an email link.")]
    open_q = [
        {"type": "ungrounded", "statement": "The system shall let a customer reset their password via an email link."},   # covered -> drop
        {"type": "gap", "statement": "The system shall provide a native mobile application for iOS and Android."},        # not covered -> keep
        {"type": "disputed", "statement": "Guest checkout is currently disputed and open, pending sign-off."},            # decision -> keep
        {"type": "non_requirement", "statement": "comply with NFR-SEC-05",
         "reason": "vacuous internal cross-reference (pointer) — folded into the referenced requirement"},                # pointer -> drop
    ]
    kept = reconcile_open_questions(open_q, approved)
    stmts = [o["statement"] for o in kept]
    assert not any("reset their password" in s for s in stmts)   # covered ungrounded suppressed
    assert any("native mobile application" in s for s in stmts)  # genuinely-missing gap kept
    assert any("Guest checkout" in s for s in stmts)             # open decision always kept
    assert all(o.get("type") != "non_requirement" for o in kept)  # pointer/absorbed dropped


def test_critic_rejected_suppressed_only_when_covered_by_an_approved_req():
    """Part H (Fix C): a critic_rejected candidate that near-restates an APPROVED requirement is a
    duplicate the critic rejected while the clean twin survived — redundant, so drop it. A
    critic_rejected item with NO approved twin (a genuine unverifiable claim) is always kept."""
    approved = [_req("R1", "The system shall pre-populate the checkout delivery address field with "
                            "the customer's default saved address.")]
    open_q = [
        # near-restates the approved requirement -> covered -> suppress
        {"type": "critic_rejected",
         "statement": "The system shall pre-populate the checkout delivery address field with the "
                      "customer's default address."},
        # a genuine unverifiable claim with no approved twin -> keep
        {"type": "critic_rejected",
         "statement": "The audit log shall be tamper-evident and cryptographically signed."},
    ]
    kept = reconcile_open_questions(open_q, approved)
    stmts = [o["statement"] for o in kept]
    assert not any("pre-populate the checkout delivery address" in s for s in stmts)  # covered -> dropped
    assert any("tamper-evident" in s for s in stmts)                                  # uncovered -> kept


def test_recall_guard_reconcile_never_shrinks_the_approved_set():
    approved = [_req("R1", "The system shall do X."), _req("R2", "The system shall do Y.")]
    before = [r.id for r in approved]
    reconcile_open_questions([{"type": "ungrounded", "statement": "The system shall do X."}], approved)
    assert [r.id for r in approved] == before        # approved set untouched — reconciliation only shrinks Appendix C
