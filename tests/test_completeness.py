"""Completeness / gap analysis — flags commonly-expected requirements that appear to be missing.
Deterministic cross-cutting coverage + optional LLM analyst review (suggestions only)."""

from __future__ import annotations

import json as _json

from rga.agents.completeness import analyze_completeness, coverage_gaps
from rga.llm.mock import MockProvider
from rga.models import Requirement, RType, SourceRef


def _req(statement, rtype=RType.functional, nfr_category=None, i=0):
    return Requirement(
        id=f"EX-{i}", project_id="P", statement=statement, rtype=rtype, nfr_category=nfr_category,
        source_refs=[SourceRef(doc_id="d", source_type="brd", location="x",
                               raw_quote=statement, start=0, end=len(statement))],
    )


def test_coverage_gaps_flags_missing_cross_cutting_aspects():
    reqs = [_req("The system shall let a customer place an order.")]  # covers none of the aspects
    names = " ".join(g["statement"] for g in coverage_gaps(reqs))
    assert "Security" in names and "Performance" in names and "Error handling" in names


def test_coverage_gaps_recognizes_present_aspects():
    reqs = [
        _req("Card data shall be encrypted; access is role-based.", RType.non_functional, "security", 1),
        _req("Each API shall meet its performance response-time budget under load.", RType.non_functional, "performance", 2),
    ]
    names = " ".join(g["statement"] for g in coverage_gaps(reqs))
    assert "Security" not in names and "Performance" not in names  # detected as covered → not flagged


def test_analyze_completeness_includes_llm_suggestions():
    reqs = [_req("The system shall let a customer place an order.")]
    prov = MockProvider(responses=[_json.dumps({"gaps": ["No requirement covers order-cancellation edge cases."]})])
    out = analyze_completeness(prov, reqs)
    assert all(g["type"] == "gap" for g in out)
    assert any("cancellation" in g["statement"] for g in out)


def test_analyze_completeness_deterministic_without_provider():
    out = analyze_completeness(None, [_req("The system shall let a customer place an order.")])
    assert out and all(g["type"] == "gap" for g in out)  # coverage check still runs, no LLM
