"""Coverage accounting — the recall floor. Every chunk is covered-by-req, captured-as-open-Q, or a
legitimate non-requirement; substantive orphans surface as possible misses (never silently dropped)."""

from __future__ import annotations

from rga.agents.coverage import coverage_report
from rga.models import Chunk, Requirement, RType, SourceRef


def _chunk(text, start, end, doc="brd", loc="3.1", st="brd"):
    return Chunk(doc_id=doc, project_id="P", source_type=st, index=0, location=loc, text=text, start=start, end=end)


def _req(rid, doc, start, end, stmt="The system shall allow the customer to place an order."):
    return Requirement(id=rid, project_id="P", statement=stmt, rtype=RType.functional,
                       source_refs=[SourceRef(doc_id=doc, source_type="brd", location="3.1",
                                              raw_quote="q", start=start, end=end)])


def test_chunk_covered_by_requirement():
    ch = _chunk("The system shall allow the customer to place an order online today.", 0, 66)
    rep = coverage_report([ch], [_req("EX-1", "brd", 10, 25)], [])
    assert rep["covered_by_req"] == 1 and rep["accounted_pct"] == 100.0


def test_chunk_covered_by_open_question():
    ch = _chunk("A product comparison feature is undecided and pending a decision by the team.",
                0, 70, doc="backlog", loc="HRZN-14")
    oq = [{"type": "undecided", "statement": "compare undecided", "doc_id": "backlog", "location": "HRZN-14"}]
    rep = coverage_report([ch], [], oq)
    assert rep["covered_by_openq"] == 1 and rep["accounted_pct"] == 100.0


def test_heading_short_and_stakeholder_sections_are_nonrequirement():
    heading = _chunk("# Scope", 0, 7)
    short = _chunk("Too short here", 0, 14)
    stakeholder = _chunk("Meera Iyer Head of E-commerce Sponsor with final say on scope decisions",
                         0, 70, loc="5. Who cares about this (stakeholders)")
    rep = coverage_report([heading, short, stakeholder], [], [])
    assert rep["expected_nonrequirement"] == 3 and rep["possible_misses"] == []


def test_substantive_orphan_is_a_possible_miss():
    ch = _chunk("The shopper wants to combine multiple filters on the product listing page at once.",
                0, 82, doc="backlog", loc="HRZN-10")
    rep = coverage_report([ch], [], [])
    assert len(rep["possible_misses"]) == 1
    assert rep["possible_misses"][0]["type"] == "possible_miss"


def test_metadata_without_obligation_cue_is_not_a_possible_miss():
    """C-M1: rosters / version lines / attributions carry no obligation cue -> expected non-req,
    NOT noise in the decision queue."""
    roster = _chunk("Present: Karan Malhotra (Product), Ananya Bose (Growth), Dev Prasad (Eng).",
                    0, 74, loc="Present")
    version = _chunk("Version 0.3 - DRAFT - 17 February 2026", 0, 38, loc="(top)")
    attribution = _chunk("Sameer Qureshi (Ops & Warehouse), 13 Feb 2026. Returns input from Neha (CX).",
                         0, 76, loc="(top)")
    rep = coverage_report([roster, version, attribution], [], [])
    assert rep["expected_nonrequirement"] == 3 and rep["possible_misses"] == []


def test_short_requirement_with_a_modal_still_surfaces():
    """C-M1: a short but obligation-bearing orphan must NOT be hidden by a length floor."""
    ch = _chunk("System shall support SSO.", 0, 25, loc="7.2")  # 4 words, but a real obligation
    rep = coverage_report([ch], [], [])
    assert len(rep["possible_misses"]) == 1


def test_completeness_gap_detector_uses_word_boundaries():
    """L1: substring false-matches ('both'~bot, 'download'~load, 'escalate'~scal, 'secondary'~second)
    must NOT mark Security/Performance covered and suppress a real gap."""
    from rga.agents.completeness import coverage_gaps

    reqs = [
        _req("R1", "brd", 0, 1, stmt="The system shall display both price and rating."),
        _req("R2", "brd", 0, 1, stmt="Users shall download and escalate a secondary invoice."),
    ]
    gaps = {g["statement"] for g in coverage_gaps(reqs)}
    assert "No requirements address Security." in gaps       # 'both' no longer satisfies Security
    assert "No requirements address Performance." in gaps    # download/escalate/secondary don't satisfy Performance
