"""SRS template incorporation — the IEEE-830 (Wiegers) format is wired in correctly.

Goals:
  * every requirement type maps to a section; every NFR category maps to a §5 subsection;
  * functional requirements carry a `feature` (for §4 grouping);
  * formal SRS ids (REQ-n / NFR-n / BR-n) assign deterministically and contiguously;
  * the template structure contains the core IEEE-830 sections + appendices.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rga.eval.dataset import load_corpus
from rga.generate.srs_template import (
    APPENDIX_SOURCES,
    FEATURE_SUBSECTIONS,
    NFR_SUBSECTIONS,
    REFERENCE_OUTLINE,
    SRS_STRUCTURE,
    TABLE_SPECS,
    TYPE_TO_SECTION,
    assign_srs_ids,
    features_in,
    section_for,
)

CORPUS_DIR = Path(__file__).resolve().parent.parent / "datasets" / "elams"


@pytest.fixture(scope="module")
def reqs():
    return load_corpus(CORPUS_DIR).requirements


def test_every_rtype_maps_to_a_section(reqs):
    used = {r["rtype"] for r in reqs}
    assert used <= set(TYPE_TO_SECTION), used - set(TYPE_TO_SECTION)


def test_every_nfr_category_maps_to_a_subsection(reqs):
    cats = {r["nfr_category"] for r in reqs if r.get("nfr_category")}
    missing = cats - set(NFR_SUBSECTIONS)
    assert not missing, f"unmapped NFR categories: {missing}"


def test_functional_requirements_have_a_feature(reqs):
    missing = [r["id"] for r in reqs if r["rtype"] == "functional" and not r.get("feature")]
    assert not missing, f"functional reqs missing a feature: {missing}"


def test_non_functional_grouping_is_sane(reqs):
    feats = features_in(reqs)
    assert 1 <= len(feats) <= 8, feats
    # every functional requirement's section_for points into Section 4
    for r in reqs:
        if r["rtype"] == "functional":
            assert section_for(r).startswith("4."), r["id"]


def test_srs_ids_assign_deterministically_and_contiguously(reqs):
    a = assign_srs_ids(reqs)
    b = assign_srs_ids(reqs)
    assert a == b, "assignment is not deterministic"

    def prefix_ids(prefix):
        return sorted(v for v in a.values() if v.startswith(prefix + "-"))

    for prefix, rtype in [("REQ", "functional"), ("NFR", "non_functional"), ("BR", "business")]:
        n = sum(1 for r in reqs if r["rtype"] == rtype)
        got = prefix_ids(prefix)
        assert len(got) == n, f"{prefix}: {len(got)} != {n}"
        assert set(got) == {f"{prefix}-{i}" for i in range(1, n + 1)}, f"{prefix} not contiguous"

    # constraints & assumptions get no tagged id
    tagged = set(a)
    untagged = {r["id"] for r in reqs if r["rtype"] in ("constraint", "assumption")}
    assert tagged.isdisjoint(untagged)


def test_template_has_core_sections_and_appendices():
    nums = {num for num, _title, _mode in SRS_STRUCTURE}
    for core in ("1", "2", "3", "4", "5", "6", "A", "B", "C"):
        assert core in nums, f"missing section {core}"
    # the requirement-bearing sections are marked as REQUIREMENTS fills
    fills = {num: mode for num, _title, mode in SRS_STRUCTURE}
    for req_section in ("4", "5.5", "2.5", "2.7"):
        assert fills[req_section] == "requirements", req_section
    assert set(APPENDIX_SOURCES) == {"B", "C"}


def test_reference_outline_is_fully_present():
    """EVERY section/subsection in the Reference SRS format must exist in the template
    — including 2.5 Design and Implementation Constraints. Fails loudly on any gap."""
    have = {(num, title) for num, title, _mode in SRS_STRUCTURE}
    missing = [item for item in REFERENCE_OUTLINE if item not in have]
    assert not missing, f"SRS template is missing required sections: {missing}"
    # explicit guard on the section that was queried
    assert ("2.5", "Design and Implementation Constraints") in have


def test_canonical_feature_consolidates_domain_groups():
    """Part E: many raw feature labels collapse to <=10 clean domain buckets, no duplicates; a
    domain label with no e-commerce keyword is kept as-is (generalizes to any project)."""
    from rga.generate.srs_template import canonical_feature

    raw = ["Returns", "Admin", "Cart & Checkout", "Search & Browse", "Payments", "Notifications",
           "Product Detail", "Orders & Fulfilment", "Policy Pages", "Account", "Promotions",
           "Cookie Consent"]
    buckets = {canonical_feature(x) for x in raw}
    assert len(buckets) <= 10                                   # 12 raw -> <= 10 canonical
    assert canonical_feature("Product Detail") == canonical_feature("Search & Browse")
    assert canonical_feature("Cookie Consent") == canonical_feature("Policy Pages")
    assert canonical_feature("Returns") == canonical_feature("Orders & Fulfilment")
    assert canonical_feature("Leave Application") == "Leave Application"   # unknown label kept
    assert canonical_feature(None) == "System Features"


def test_wellformed_functional_accepts_conditionals_and_alt_subjects():
    """Fix E: the correct functional-shape predicate accepts event-driven conditionals and non-'The'
    subjects (all valid IEEE-830), not only literal '^The .+ shall '."""
    from rga.generate.srs_template import is_wellformed_functional

    for s in [
        "The system shall log the user in.",
        "When an order is cancelled, the system shall release the reserved stock.",
        "If a payment fails, the customer's cart shall be preserved.",
        "Every order shall produce a downloadable GST tax invoice.",
        "Customers shall be able to view order status from their order page.",
        "A refund must generate a credit note.",                       # 'must' is a valid modal
        "Stock reservations shall be released automatically after a timeout.",
    ]:
        assert is_wellformed_functional(s), s
    assert not is_wellformed_functional("system logs the user in")     # no modal, lowercase
    assert not is_wellformed_functional("attendees: alice, bob")       # not an obligation
    assert not is_wellformed_functional("")


def test_to_shall_voice_normalises_must_but_preserves_meaning():
    """Fix E: render-time voice normalisation makes '§4' read in a uniform 'shall' voice without
    touching conditionals/subjects or the stored requirement."""
    from rga.generate.srs_template import to_shall_voice

    assert to_shall_voice("A refund must generate a credit note.") == "A refund shall generate a credit note."
    assert to_shall_voice("The system must not store raw card data.") == "The system shall not store raw card data."
    # already-'shall' text and conditionals are unchanged
    assert to_shall_voice("When X happens, the system shall respond.") == "When X happens, the system shall respond."
    # 'must' inside a larger word (e.g. 'mustard') is NOT touched (word boundary)
    assert to_shall_voice("The catalogue shall list mustard products.") == "The catalogue shall list mustard products."


def test_feature_substructure_and_tables_specified():
    assert FEATURE_SUBSECTIONS == [
        "Description and Priority",
        "Stimulus/Response Sequences",
        "Functional Requirements",
    ]
    for tbl in (
        "Revision History",
        "User Classes and Characteristics",
        "Software Interfaces",
        "Appendix A: Glossary",
        "Appendix C: To Be Determined List",
    ):
        assert tbl in TABLE_SPECS and TABLE_SPECS[tbl], f"missing table spec: {tbl}"
