"""TC7.1-TC7.3 — Generators (G1 SRS, G2 RTM, seed models) + the gate + narrative hybrid.

Goals:
  TC7.1  SRS follows the IEEE-830 (Wiegers) structure (every reference section present);
         §4 grouped by feature with REQ-n; §5 NFR-n/BR-n; §2.5 constraints; §2.7 assumptions;
         contains ONLY approved requirements; every requirement line carries its source id.
  TC7.2  RTM is a faithful, complete projection — every approved req ↔ source ↔ SRS section.
  TC7.3  seed models are valid Mermaid (use-case + ERD), labelled DRAFT, and bounded.
Plus: generation is refused unless the review gate is open; the narrative is a hybrid
      (LLM draft where possible, else [TBD]).
"""

from __future__ import annotations

import json

import pytest

from rga.agents.narrative import draft_narrative
from rga.generate.common import DEFERRED, NONE_ITEMS, TBD
from rga.generate.handoff import GateNotOpen, generate_handoff
from rga.generate.models import MAX_ENTITIES, MAX_USE_CASES, seed_models, seed_models_markdown
from rga.generate.rtm import RTM_COLUMNS, build_rtm, rtm_markdown, traceability_check
from rga.generate.srs import generate_srs
from rga.generate.srs_template import REFERENCE_OUTLINE, assign_srs_ids
from rga.llm.mock import MockProvider
from rga.models import Priority, Requirement, RType, SourceRef, Status

PID = "P-GEN"


def _r(rid, statement, rtype, *, status=Status.approved, feature=None, nfr=None, priority=None):
    return Requirement(
        id=rid,
        project_id=PID,
        statement=statement,
        rtype=RType(rtype),
        feature=feature,
        nfr_category=nfr,
        priority=Priority(priority) if priority else None,
        status=status,
        source_refs=[SourceRef(doc_id="brd", source_type="brd", location="3.1",
                               raw_quote=statement, start=0, end=len(statement))],
    )


@pytest.fixture
def reqs():
    return [
        _r("EX-f01", "The system shall allow an employee to submit a leave request.", "functional", feature="Leave Application", priority="must"),
        _r("EX-f02", "The system shall route each request to the employee's manager.", "functional", feature="Leave Application"),
        _r("EX-f03", "The system shall show a manager their pending approvals.", "functional", feature="Approvals"),
        _r("EX-n01", "The system shall respond to dashboard queries within 2 seconds.", "non_functional", nfr="performance"),
        _r("EX-n02", "The system shall require MFA for all admin logins.", "non_functional", nfr="security"),
        _r("EX-b01", "Employees may carry over up to 5 unused casual leave days.", "business"),
        _r("EX-c01", "The system shall run on the existing Azure tenant.", "constraint"),
        _r("EX-a01", "An HR directory service is assumed to be available.", "assumption"),
        _r("EX-x01", "This CANDIDATE requirement is not yet reviewed.", "functional", feature="Leave Application", status=Status.candidate),
        _r("EX-x02", "This REJECTED requirement was thrown out.", "functional", feature="Approvals", status=Status.rejected),
    ]


def _expected_heading(num: str, title: str) -> str:
    if num in {"A", "B", "C"}:
        return f"## {title}"
    dots = num.count(".")
    if dots == 0:
        return f"## {num}. {title}"
    if dots == 1:
        return f"### {num} {title}"
    return f"#### {num} {title}"


def _between(md: str, start: str, end: str) -> str:
    i = md.index(start)
    j = md.index(end, i + len(start))
    return md[i:j]


# --- TC7.1 -------------------------------------------------------------------
def test_tc7_1_srs_has_full_reference_structure(reqs):
    md = generate_srs(reqs, project_name="ELAMS")
    for num, title in REFERENCE_OUTLINE:
        assert _expected_heading(num, title) in md, f"missing section {num} {title}"
    # the explicitly-queried constraints section is present
    assert "### 2.5 Design and Implementation Constraints" in md


def test_tc7_1_features_grouped_with_req_ids(reqs):
    md = generate_srs(reqs, project_name="ELAMS")
    id_map = assign_srs_ids([r for r in reqs if r.status == Status.approved])
    sec4 = _between(md, "## 4. System Features", "## 5. Other Nonfunctional Requirements")
    assert "### 4.1 Leave Application" in sec4 and "### 4.2 Approvals" in sec4
    # the two Leave Application functionals appear with REQ-n ids under §4
    assert id_map["EX-f01"].startswith("REQ-") and id_map["EX-f01"] in sec4
    assert "#### 4.1.3 Functional Requirements" in sec4


def test_tc7_1_only_approved_no_inline_citations_but_rtm_traces(reqs):
    approved = [r for r in reqs if r.status == Status.approved]
    md = generate_srs(reqs, project_name="ELAMS")
    assert "CANDIDATE" not in md and "REJECTED" not in md  # unreviewed/rejected excluded
    # Part L: the SRS body carries NO inline citations / internal ids / source-trail markers …
    # (internal cross-refs like "§4.1.3" are legitimate; the banned marker is the "[src: …]" block)
    assert "[src:" not in md and "EX-" not in md and "§HRZN" not in md and "; backlog" not in md
    # … but provenance is not lost — every approved requirement traces in the RTM
    id_map = assign_srs_ids(approved)
    rtm = rtm_markdown(build_rtm(approved, id_map))
    for r in approved:
        assert r.id in rtm, f"{r.id} not traced in RTM"


def test_srs_deterministic_and_free_of_diagrams_and_citations(reqs):
    """Determinism (global) + Parts L & G: two runs are byte-identical; the SRS has no inline
    citations and no diagram syntax; Appendix B is a single deferral paragraph."""
    a = generate_srs(reqs, project_name="ELAMS", date="2026-01-01")
    b = generate_srs(reqs, project_name="ELAMS", date="2026-01-01")
    assert a == b                                          # identical inputs -> identical output
    for banned in ("[src:", "flowchart", "erDiagram", "-->", "||--o{"):
        assert banned not in a, banned
    appb = _between(a, "## Appendix B", "## Appendix C")
    assert "Design phase" in appb and "data-flow diagram" in appb   # deferral paragraph, named models


def test_tc7_1_document_conventions_is_deterministic_not_tbd(reqs):
    from rga.generate.srs_template import DOCUMENT_CONVENTIONS

    md = generate_srs(reqs, project_name="ELAMS")
    conv = _between(md, "### 1.2 Document Conventions", "### 1.3")
    assert "REQ-n" in conv and TBD not in conv  # our own conventions text, never TBD
    assert DOCUMENT_CONVENTIONS[:40] in md


def test_tc7_1_types_land_in_right_sections(reqs):
    md = generate_srs(reqs, project_name="ELAMS")
    assert "within 2 seconds" in _between(md, "### 5.1 Performance", "### 5.2 Safety")
    assert "MFA" in _between(md, "### 5.3 Security", "### 5.4 Software Quality")
    assert "carry over up to 5" in _between(md, "### 5.5 Business Rules", "## 6. Other Requirements")
    assert "existing Azure tenant" in _between(md, "### 2.5 Design and Implementation Constraints", "### 2.6")
    assert "HR directory service" in _between(md, "### 2.7 Assumptions and Dependencies", "## 3.")


# --- TC7.2 -------------------------------------------------------------------
def test_tc7_2_rtm_is_complete_projection(reqs):
    approved = [r for r in reqs if r.status == Status.approved]
    rows = build_rtm(reqs)
    assert len(rows) == len(approved)
    assert {row["internal_id"] for row in rows} == {r.id for r in approved}
    for row in rows:
        assert row["section"] and row["sources"] != "—" and row["evidence"] != "—"
    trace = traceability_check(reqs)
    assert trace["complete"] is True and trace["rows"] == len(approved)


def test_tc7_2_rtm_ids_agree_with_srs(reqs):
    id_map = assign_srs_ids([r for r in reqs if r.status == Status.approved])
    rows = {row["internal_id"]: row for row in build_rtm(reqs, id_map)}
    assert rows["EX-b01"]["srs_id"] == id_map["EX-b01"]  # BR-n
    assert rows["EX-c01"]["srs_id"] == "—"  # constraint: untagged
    table = rtm_markdown(build_rtm(reqs, id_map))
    assert table.startswith("| SRS ID |") and table.count("\n") >= len(rows)


# --- TC7.3 -------------------------------------------------------------------
def test_tc7_3_seed_models_valid_bounded_and_draft(reqs):
    m = seed_models(reqs)
    assert m["use_case"].startswith("flowchart") and "DRAFT" in m["use_case"]
    assert m["erd"].startswith("erDiagram") and "DRAFT" in m["erd"]
    # bounded
    assert m["use_case"].count("UC") <= MAX_USE_CASES * 3 + 5
    assert m["erd"].count("||--o{") <= MAX_ENTITIES
    # entity-block braces balanced (ignore relationship lines, whose crow's-foot
    # cardinality `||--o{` legitimately contains a brace that is not a block delimiter)
    blocks = "\n".join(ln for ln in m["erd"].splitlines() if "--" not in ln)
    assert blocks.count("{") == blocks.count("}")
    md = seed_models_markdown(m)
    assert md.count("```mermaid") == 2 and "draft" in md.lower()


def test_tc7_3_seed_models_bounded_on_many_features():
    many = [
        _r(f"EX-f{i:02d}", f"The system shall support capability {i}.", "functional", feature=f"Feature {i}")
        for i in range(40)
    ]
    m = seed_models(many)
    assert m["erd"].count("||--o{") <= MAX_ENTITIES  # capped, no runaway


# --- gate --------------------------------------------------------------------
def test_generate_refused_until_gate_open(reqs):
    with pytest.raises(GateNotOpen):
        generate_handoff(reqs, project_name="ELAMS")  # a candidate is still pending


def test_generate_handoff_when_gate_open(reqs):
    triaged = [r for r in reqs if r.status != Status.candidate]  # drop the pending one
    pack = generate_handoff(triaged, project_name="ELAMS", date="2026-07-09")
    assert pack["manifest"]["traceability_complete"] is True
    assert pack["manifest"]["approved"] == sum(1 for r in triaged if r.status == Status.approved)
    assert "# Software Requirements Specification" in pack["srs_markdown"]
    assert pack["rtm_markdown"].startswith("| SRS ID |")
    assert all(r.status == Status.approved for r in pack["approved"])


def test_generate_refused_when_an_approved_requirement_has_no_source(reqs):
    """E-M4: an untraceable approved requirement BLOCKS handoff (enforced, not just reported)."""
    from rga.generate.handoff import TraceabilityIncomplete

    triaged = [r for r in reqs if r.status != Status.candidate]
    victim = next(r for r in triaged if r.status == Status.approved)
    victim.source_refs = []                                    # break traceability
    with pytest.raises(TraceabilityIncomplete):
        generate_handoff(triaged, project_name="ELAMS")


# --- rendering fidelity (Phase 4) --------------------------------------------
def test_srs_and_rtm_survive_pipes_and_newlines_in_a_statement():
    """D-F1/F5: a statement containing a pipe or newline must not break the SRS bullet or RTM row."""
    r = _r("EX-z1", "The system shall show a yes|no toggle\nand persist the choice.",
           "functional", feature="Prefs")
    md = generate_srs([r], project_name="X")
    body = _between(md, "#### 4.1.3", "## 5.")
    assert "yes|no toggle and persist" in body        # newline collapsed to one clean line
    assert "[src:" not in body and "EX-z1" not in body   # Part L: no inline citation in the SRS body
    rtm = rtm_markdown(build_rtm([r]))
    row = next(ln for ln in rtm.splitlines() if "EX-z1" in ln)
    assert row.count("|") - row.count("\\|") == len(RTM_COLUMNS) + 1        # pipe escaped, not a column


def test_req_ids_follow_srs_reading_order():
    """D-F4: REQ-n is numbered in the order Section 4 renders features (first-seen), not alphabetically."""
    reqs = [
        _r("EX-a1", "The system shall accept a return request.", "functional", feature="Returns"),
        _r("EX-b1", "The system shall support checkout.", "functional", feature="Checkout"),
    ]
    id_map = assign_srs_ids(reqs)
    assert id_map["EX-a1"] == "REQ-1" and id_map["EX-b1"] == "REQ-2"        # Returns rendered first


def test_feature_priority_shown_as_high_medium_low():
    """D-F8: §4.x.1 shows High/Medium/Low (matching §1.2); the highest MoSCoW wins."""
    reqs = [
        _r("EX-p1", "The system shall process payment.", "functional", feature="Checkout", priority="must"),
        _r("EX-p2", "The system shall show a spinner.", "functional", feature="Checkout", priority="could"),
    ]
    desc = _between(generate_srs(reqs, project_name="X"), "#### 4.1.1", "#### 4.1.2")
    assert "Priority: High." in desc and "Must" not in desc


# --- narrative hybrid --------------------------------------------------------
def test_narrative_hybrid_fills_some_and_tbd_the_rest(reqs):
    reply = json.dumps({
        "purpose": "ELAMS automates employee leave and attendance management.",
        "product_scope": "In scope: leave application, approvals, carry-over rules.",
        # everything else omitted -> should become TBD in the SRS
    })
    narrative = draft_narrative(MockProvider(responses=[reply]), "ELAMS", reqs)
    assert narrative["1.1"].startswith("ELAMS automates")
    assert "1.4" in narrative and "3.1" not in narrative
    md = generate_srs(reqs, project_name="ELAMS", narrative=narrative)
    assert "ELAMS automates" in _between(md, "### 1.1 Purpose", "### 1.2")
    # An undrafted PROSE section (§3.1 User Interfaces) still degrades to TBD ...
    assert TBD in _between(md, "### 3.1 User Interfaces", "### 3.1.1")
    # ... but the visual-design subsections are a Design-phase artifact, not a gap:
    theme = _between(md, "#### 3.1.1 Overall Visual Theme", "#### 3.1.2")
    assert DEFERRED in theme and TBD not in theme


# --- TBD reduction (the four fixes) -----------------------------------------
def test_title_page_and_toc_outline_in_markdown(reqs):
    """The SRS markdown carries the centred title-page block (project name, version, prepared-by RGA,
    date, full Wiegers disclaimer) and a Table-of-Contents outline the docx export turns into a live
    field — both marker-wrapped so they never leak the '-->' arrow the diagram guard forbids."""
    md = generate_srs(reqs, project_name="ELAMS", date="2025-03-03")
    assert "[[TITLEPAGE]]" in md and "[[/TITLEPAGE]]" in md
    assert "# Software Requirements Specification" in md
    assert "for\nELAMS\n" in md                              # 'for' then the project name, own lines
    assert "Version 1.0 approved" in md
    assert "Prepared by RGA (Agentic Requirement Gathering & Analysis)" in md
    assert "Permission is granted to use, modify, and distribute this document." in md
    assert "[[TOC]]" in md and "[[/TOC]]" in md
    assert "**1. Introduction**" in md                       # ToC outline lists the main headings
    assert "-->" not in md                                    # markers must not trip the diagram guard


def test_tc7_1_stimulus_response_is_generated_not_tbd(reqs):
    """Fix 1: §4.x.2 Stimulus/Response Sequences is synthesised, never a bare TBD."""
    md = generate_srs(reqs, project_name="ELAMS")
    sr = _between(md, "#### 4.1.2 Stimulus/Response Sequences", "#### 4.1.3")
    assert "Stimulus" in sr and "Response" in sr and TBD not in sr


# --- Fix 4: §4.x.2 renders DISCRETE stimulus/response sequences, not one packed paragraph -------
def test_tc7_stimulus_response_default_is_discrete(reqs):
    """The requirement-grounded default emits >=2 DISCRETE sequences, each with its own explicit
    **Stimulus** and **Response** (matching the reference SRS shape)."""
    md = generate_srs(reqs, project_name="ELAMS")
    sr = _between(md, "#### 4.1.2 Stimulus/Response Sequences", "#### 4.1.3")
    assert sr.count("**Stimulus —**") >= 2 and sr.count("**Response —**") >= 2


def test_tc7_narrative_renders_discrete_sequences(reqs):
    """draft_narrative turns the LLM's structured `sequences` into a numbered discrete block keyed
    by the exact feature name — one **Stimulus**/**Response** per sequence, no packed paragraph."""
    reply = json.dumps({
        "feature_flows": [
            {"feature": "Leave", "sequences": [
                {"stimulus": "An employee submits a leave application.",
                 "response": "The system validates the balance and records the request as pending."},
                {"stimulus": "The employee submits with insufficient balance.",
                 "response": "The system rejects the request with a clear message and no state change."},
            ]},
        ],
    })
    narrative = draft_narrative(MockProvider(responses=[reply]), "ELAMS", reqs)
    block = narrative["feature_flow::Leave"]
    assert block.count("**Stimulus —**") == 2 and block.count("**Response —**") == 2
    assert block.startswith("1. **Stimulus —** An employee submits a leave application.")


def test_tc7_srs_prefers_drafted_discrete_sequences(reqs):
    """When a drafted flow exists for the feature §4.1 renders, the SRS uses it verbatim."""
    from rga.generate.srs_template import features_in

    feat = features_in(reqs)[0]
    flow = "1. **Stimulus —** Actor does X.\n   **Response —** System does Y."
    md = generate_srs(reqs, project_name="ELAMS", narrative={f"feature_flow::{feat}": flow})
    sr = _between(md, "#### 4.1.2 Stimulus/Response Sequences", "#### 4.1.3")
    assert "Actor does X." in sr and "System does Y." in sr


def test_tc7_1_references_glossary_and_empty_sections(reqs):
    """Fixes 3 & 4: §1.5 References auto-built from the requirements' sources; Appendix A
    Glossary auto-expands acronyms found in the corpus; empty requirement sections read
    'None identified' (not TBD); a later-phase section (§2.6) reads 'Deferred' (not TBD)."""
    md = generate_srs(reqs, project_name="ELAMS")
    refs = _between(md, "### 1.5 References", "## 2. Overall Description")
    assert "Business Requirements Document (BRD)" in refs and "`brd`" in refs and TBD not in refs
    gloss = _between(md, "## Appendix A: Glossary", "## Appendix B")
    assert "MFA" in gloss and "Multi-Factor Authentication" in gloss and TBD not in gloss
    safety = _between(md, "### 5.2 Safety Requirements", "### 5.3")     # no safety NFR in corpus
    assert NONE_ITEMS in safety and TBD not in safety
    docs = _between(md, "### 2.6 User Documentation", "### 2.7")        # Design/delivery artifact
    assert DEFERRED in docs and TBD not in docs


def test_tc7_1_no_tbd_when_narrative_complete(reqs):
    """With every prose section drafted, the SRS carries ZERO '[TBD ...]' placeholders:
    design sections say 'Deferred', empty sections 'None identified', refs/glossary auto-filled."""
    narrative = {s: f"Drafted prose for section {s}." for s in
                 ["1.1", "1.3", "1.4", "2.1", "2.2", "2.3", "2.4", "3.1", "3.3", "3.4"]}
    md = generate_srs(reqs, project_name="ELAMS", narrative=narrative)
    assert TBD not in md


def test_narrative_degrades_gracefully_on_truncated_output(reqs):
    # the model returns invalid/truncated JSON on every attempt (the real bug) -> must NOT
    # raise; draft_narrative returns {} and the SRS renders those sections as TBD.
    prov = MockProvider(responses=["{", "{", "{"])
    narrative = draft_narrative(prov, "ELAMS", reqs, run_llm=True)
    assert narrative == {}
    md = generate_srs(reqs, project_name="ELAMS", narrative=narrative)
    assert "# Software Requirements Specification" in md  # generation still succeeds


def test_narrative_skipped_when_disabled(reqs):
    prov = MockProvider(responses=["unused"])
    assert draft_narrative(prov, "ELAMS", reqs, run_llm=False) == {}
    assert prov.calls == 0


# --- strict parser-conformance (Design team's srs_parser) --------------------
def test_srs_parser_conformance_tables_and_entities(reqs):
    """The Design team parses the SRS with a strict deterministic parser: §2.3 and §3.3 MUST be
    tables (with the exact header the parser keys off), Appendix B MUST name the principal entities,
    and requirements MUST carry REQ-/NFR-/BR- tags. These are now guaranteed from data every run,
    independent of the LLM narrative — so we assert them with NO narrative supplied."""
    md = generate_srs(reqs, project_name="ELAMS")  # no narrative -> deterministic fallback path

    uc = _between(md, "### 2.3 User Classes and Characteristics", "### 2.4")
    assert "| User Class | Description |" in uc and TBD not in uc      # real table, never prose/TBD

    si = _between(md, "### 3.3 Software Interfaces", "### 3.4")
    assert "| Name | Description |" in si and TBD not in si            # first col "Name" -> parser skips header

    appb = _between(md, "## Appendix B", "## Appendix C")
    assert "principal entities (" in appb                              # parser extracts entities from this

    assert "REQ-1:" in md and "NFR-1:" in md and "BR-1:" in md          # tagged for the parser
