"""The deterministic non-requirement guard: reject document-meta/disclaimers, keep real
requirements. High precision (never drops a genuine requirement)."""

from __future__ import annotations

import json

from rga.agents.pipeline import extract_document
from rga.agents.requirement_filter import is_genuine_requirement
from rga.llm.mock import MockProvider
from rga.models import Chunk

NON_REQUIREMENTS = [
    "This document provides only a high-level (30,000-foot) overview and must not be treated as final.",
    "This document must be read in conjunction with the detailed functional requirements.",
    "The specification shall be reviewed quarterly by the authors.",  # about the spec, not product
    "This section describes the ordering flow.",
]
REAL_REQUIREMENTS = [
    "The system shall allow a user to reset their password via an email link.",
    "The platform must be available 99.9% of the time.",
    "Customers should be able to check out as a guest.",
    "The system shall generate a GST-compliant invoice for every order.",  # 'document' is the OBJECT, not subject
    # B-M3 regression: these were WRONGLY dropped before — the subject noun looks document-ish or
    # the text contains an "overview"/"placeholder" phrase, but each is a real product obligation.
    "The report generator shall export orders to CSV.",
    "The table shall display sortable, filterable columns.",
    "The system shall show a high-level overview dashboard on the home page.",
    "The system shall display placeholder text in empty search fields.",
    "The list shall be paginated at 25 items per page.",
]


def test_rejects_document_meta_and_disclaimers():
    for s in NON_REQUIREMENTS:
        ok, why = is_genuine_requirement(s)
        assert ok is False, f"should reject: {s}"
        assert why


def test_keeps_genuine_requirements():
    for s in REAL_REQUIREMENTS:
        ok, _ = is_genuine_requirement(s)
        assert ok is True, f"should keep: {s}"


def test_rejects_metadata_shapes_bylines_versions_csv_rows():
    """Part E: byline / version / attendee / raw-CSV-row shapes are document metadata, not reqs."""
    for junk in (
        "HRZN-12,Story,As a shopper I want to filter products,Search,Must",   # raw backlog CSV row
        "HRZN-3, Story, cart update, Cart, High",
        "Version 0.3 — DRAFT",
        "v0.3 DRAFT — internal circulation only",
        "Present: Sameer Qureshi, Tarun Mehta, Meera Nair",
        "Attendees: Growth, Finance, CX",
        "Prepared by CommerceForge — 13 Feb 2026",
        "Sameer Qureshi, Product Lead, 13 Feb 2026",                         # dated byline, no obligation
    ):
        ok, why = is_genuine_requirement(junk)
        assert ok is False, f"should reject junk: {junk!r}"
        assert why


def test_dated_real_requirement_is_kept():
    """Recall-safe: a date does NOT make a line junk when it carries a real obligation/action."""
    for good in (
        "The system shall retain audit logs until 2030-01-01.",
        "The system shall email the invoice within 1 business day of 2026-01-01.",
    ):
        ok, _ = is_genuine_requirement(good)
        assert ok is True, f"should keep: {good!r}"


def test_pipeline_drops_non_requirement_to_open_questions():
    chunk = Chunk(doc_id="d1", project_id="P", source_type="brd", index=0, location="1",
                  text="This document must be read in conjunction with the annex. "
                       "The system shall allow login.", start=0, end=90)
    responses = [json.dumps({"requirements": [
        {"statement": "This document must be read in conjunction with the annex.",
         "rtype": "constraint", "quotes": ["This document must be read in conjunction with the annex."],
         "inferred": False, "rationale": "r", "confidence": 0.9},
        {"statement": "The system shall allow login.", "rtype": "functional",
         "quotes": ["The system shall allow login."], "inferred": False, "rationale": "r", "confidence": 0.9},
    ]})]
    accepted, open_q = extract_document(MockProvider(responses=responses), [chunk], max_passes=1, run_critic=False)
    assert [r.statement for r in accepted] == ["The system shall allow login."]
    assert any(o["type"] == "non_requirement" for o in open_q)
