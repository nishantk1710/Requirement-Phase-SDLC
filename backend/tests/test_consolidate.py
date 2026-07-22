"""Consolidation — converge to a canonical set, lossless. Pointers demoted, same-obligation merged
(deterministic + LLM), genuinely-distinct kept, and canonical+absorbed+demoted == input always."""

from __future__ import annotations

import json as _json

from rga.agents.consolidate import _is_pointer, consolidate
from rga.llm.mock import MockProvider
from rga.models import Requirement, RType, SourceRef


def _req(rid, stmt, doc="brd", start=0, source_type="brd"):
    return Requirement(id=rid, project_id="P", statement=stmt, rtype=RType.functional,
                       source_refs=[SourceRef(doc_id=doc, source_type=source_type, location="x",
                                              raw_quote=stmt, start=start, end=start + len(stmt))])


def test_is_pointer_only_demotes_vacuous_cross_references():
    # vacuous: the ENTIRE content is a cross-reference -> demote
    assert _is_pointer("The system shall comply with NFR-SEC-05.")
    assert _is_pointer("Security shall be as defined in NFR-SEC-01.")
    # substantive requirement that merely CITES an NFR/policy -> KEEP (recall-safe; C-H2)
    assert not _is_pointer(
        "The system shall encrypt cardholder data at rest using AES-256 in accordance with NFR-SEC-05."
    )
    assert not _is_pointer("Login shall be rate-limited in accordance with NFR-SEC-05.")
    assert not _is_pointer("The system shall allow guest checkout.")


def test_pointer_demoted_and_loss_zero():
    a = _req("EX-1", "The system shall allow guest checkout.")
    b = _req("EX-2", "The system shall comply with NFR-SEC-05.")  # vacuous pointer
    canonical, rep = consolidate([a, b])
    assert rep["loss"] == 0 and rep["demoted_pointers"] == 1
    assert [r.id for r in canonical] == ["EX-1"]
    assert rep["demoted"][0]["type"] == "non_requirement"


def test_substantive_requirement_citing_nfr_is_not_demoted():
    """C-H2 regression: a real obligation that happens to cite an NFR must reach the SRS body."""
    a = _req("EX-1", "The system shall encrypt cardholder data at rest in accordance with NFR-SEC-05.")
    b = _req("EX-2", "The system shall handle refunds in accordance with the Consumer Rights Act.")
    canonical, rep = consolidate([a, b])
    assert rep["demoted_pointers"] == 0 and rep["loss"] == 0
    assert {r.id for r in canonical} == {"EX-1", "EX-2"}


def test_genuinely_distinct_not_merged():
    a = _req("EX-1", "The system shall sort products by price low to high.")
    b = _req("EX-2", "The system shall sort products by price high to low.")
    canonical, rep = consolidate([a, b])  # no LLM: distinct content, must stay separate
    assert len(canonical) == 2 and rep["absorbed"] == 0 and rep["loss"] == 0


def test_llm_merge_biased_absorbs_paraphrase_and_keeps_evidence():
    a = _req("EX-1", "The system shall handle synonym matching for search terms.")
    b = _req("EX-2", "Search shall return results for equivalent synonyms of a query.", doc="jira")
    prov = MockProvider(responses=[_json.dumps({"groups": [[1, 2]]})])
    canonical, rep = consolidate([a, b], provider=prov, run_llm=True, candidate_threshold=0.2)
    assert rep["absorbed"] == 1 and len(canonical) == 1 and rep["loss"] == 0
    assert {s.doc_id for s in canonical[0].source_refs} == {"brd", "jira"}  # evidence preserved
    assert canonical[0].duplicate_of  # absorbed id recorded (auditable)


def test_merge_keeps_absorbed_statement_text_for_audit():
    """C-M4: a merge retains the absorbed wording (in provenance) so a reviewer can audit/split it."""
    a = _req("EX-1", "The system shall handle synonym matching for search terms.")
    b = _req("EX-2", "Search shall return results for equivalent synonyms of a query.", doc="jira")
    prov = MockProvider(responses=[_json.dumps({"groups": [[1, 2]]})])
    canonical, rep = consolidate([a, b], provider=prov, run_llm=True, candidate_threshold=0.2)
    assert len(canonical) == 1
    absorbed = canonical[0].provenance.get("absorbed_statements", [])
    assert absorbed and any("synonym" in s.lower() for s in absorbed)


def test_canonical_prefers_formal_spec_over_backlog_phrasing():
    """A merge keeps the FORMAL-SPEC phrasing as canonical, not a longer backlog user-story phrasing
    that narrows the obligation (which then fails second-opinion review — the catalogue-CMS collapse).
    The backlog wording is retained in the audit trail, so nothing is lost."""
    brd = _req("F-1", "The system shall let merchandisers create, edit and publish products, variants and prices.")
    backlog = _req(
        "F-2",
        "As a merchandiser I want to create, edit and publish product variants in the catalogue CMS "
        "without engineering, so that I can move quickly.",
        doc="backlog", source_type="form")  # LONGER, but narrows to 'variants' + from a backlog
    prov = MockProvider(responses=[_json.dumps({"groups": [[1, 2]]})])
    canonical, rep = consolidate([brd, backlog], provider=prov, run_llm=True, candidate_threshold=0.2)
    assert len(canonical) == 1 and rep["absorbed"] == 1 and rep["loss"] == 0
    assert "products, variants and prices" in canonical[0].statement.lower()   # formal-spec survived
    absorbed = canonical[0].provenance.get("absorbed_statements", [])
    assert any("catalogue cms" in s.lower() for s in absorbed)                 # backlog wording kept


def test_demoted_pointer_keeps_full_provenance():
    """C-M5: a demoted pointer keeps ALL its source refs (quote + offsets), not just the first."""
    a = _req("EX-1", "The system shall comply with NFR-SEC-05.")
    a.source_refs.append(SourceRef(doc_id="jira", source_type="jira", location="H-1",
                                   raw_quote="comply with NFR-SEC-05", start=0, end=22))
    _, rep = consolidate([a])
    assert rep["demoted_pointers"] == 1
    mr = rep["demoted"][0]["merged_refs"]
    assert len(mr) == 2 and {m["doc_id"] for m in mr} == {"brd", "jira"}
    assert all(m["raw_quote"] for m in mr)


def test_loss_invariant_holds_on_mixed_input():
    reqs = [
        _req("EX-1", "The system shall allow guest checkout."),
        _req("EX-2", "RBAC shall be enforced in accordance with NFR-SEC-07."),  # pointer
        _req("EX-3", "The system shall generate a GST invoice for each order."),
    ]
    canonical, rep = consolidate(reqs)
    assert rep["loss"] == 0
    assert len(canonical) + rep["absorbed"] + rep["demoted_pointers"] == len(reqs)
