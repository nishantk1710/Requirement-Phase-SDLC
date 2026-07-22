"""Semantic de-duplication pass — merges cross-document paraphrases without losing evidence,
and does NOT merge genuinely distinct requirements."""

from __future__ import annotations

from rga.agents.pipeline import _statement_similarity, dedupe_requirements
from rga.models import Requirement, RType, SourceRef


def _req(rid, statement, rtype=RType.constraint, doc="d1", start=0):
    return Requirement(
        id=rid, project_id="P", statement=statement, rtype=rtype,
        source_refs=[SourceRef(doc_id=doc, source_type="brd", location="x",
                               raw_quote=statement, start=start, end=start + len(statement))],
    )


def test_postprocess_drops_ungrounded_scope_list_echo_but_keeps_distinct():
    """FIX 1: a generic 'In scope' umbrella whose feature is already captured (grounded) by an
    approved requirement is dropped from Appendix C; a genuinely-distinct ungrounded item is kept."""
    from rga.agents.pipeline import postprocess_open_questions

    def firm(rid, s):
        return Requirement(id=rid, project_id="P", statement=s, rtype=RType.functional,
                           source_refs=[SourceRef(doc_id="d", source_type="brd", location="x",
                                                  raw_quote=s, start=0, end=len(s))])
    reqs = [
        firm("R1", "The system shall support user login with an email address and a password."),
        firm("R2", "The system shall support password reset via a time-limited reset link."),
        firm("R3", "The system shall provide product search results with autocomplete."),
    ]
    open_q = [
        {"type": "ungrounded", "statement": "The system shall support user login."},           # covered -> drop
        {"type": "ungrounded", "statement": "The system shall support password reset."},        # covered -> drop
        {"type": "ungrounded", "statement": "The system shall provide a native mobile app."},   # distinct -> keep
    ]
    kept = [o["statement"] for o in postprocess_open_questions(reqs, open_q)]
    assert not any("user login" in s for s in kept)       # redundant echo removed
    assert not any("password reset" in s for s in kept)   # redundant echo removed
    assert any("mobile app" in s for s in kept)           # distinct ungrounded item preserved


def test_paraphrase_pair_merges_and_keeps_evidence():
    # the exact pair seen in the UI, from two different documents
    a = _req("EX-a", "Raw card details shall never be stored on ShopSphere servers.", doc="brd")
    b = _req("EX-b", "The system shall never store raw card details on ShopSphere servers.", doc="jira")
    kept, merged = dedupe_requirements([a, b])
    assert merged == 1 and len(kept) == 1
    # both source documents are preserved on the survivor
    assert {s.doc_id for s in kept[0].source_refs} == {"brd", "jira"}
    assert "EX-b" in kept[0].duplicate_of


def test_near_dup_merge_keeps_higher_authority_statement():
    """FIX(a): when a formal-spec (brd) statement and a backlog (form) near-duplicate merge, the
    formal statement REPRESENTS the pair — even though the backlog was seen first — and the displaced
    backlog wording is preserved in the audit trail. Stops a backlog user story that narrows the
    obligation from becoming the canonical (the catalogue-CMS collapse)."""
    backlog = Requirement(
        id="B-1", project_id="P", rtype=RType.functional,
        statement="Merchandisers create, edit, publish and unpublish product variants in the catalogue CMS.",
        source_refs=[SourceRef(doc_id="backlog", source_type="form", location="HG-50",
                               raw_quote="q", start=0, end=1)])
    brd = Requirement(
        id="A-1", project_id="P", rtype=RType.functional,
        statement="The system shall let merchandisers create, edit, publish and unpublish products, variants and prices.",
        source_refs=[SourceRef(doc_id="brd", source_type="brd", location="6", raw_quote="q", start=0, end=1)])
    kept, merged = dedupe_requirements([backlog, brd], threshold=0.4)   # backlog seen FIRST
    assert merged == 1 and len(kept) == 1
    assert "products, variants and prices" in kept[0].statement.lower()   # brd phrasing won (authority)
    assert any("catalogue cms" in s.lower() for s in kept[0].provenance.get("absorbed_statements", []))


def test_exact_normalized_duplicate_merges():
    a = _req("EX-1", "The system shall allow guest checkout.", RType.functional)
    b = _req("EX-2", "The system shall allow guest checkout.", RType.functional, doc="d2")
    kept, merged = dedupe_requirements([a, b])
    assert merged == 1 and len(kept) == 1


def test_distinct_requirements_do_not_merge():
    reqs = [
        _req("EX-1", "The system shall allow employees to apply for leave.", RType.functional),
        _req("EX-2", "The system shall calculate GST on every order.", RType.functional),
        _req("EX-3", "The platform shall be available 99.9% of the time.", RType.non_functional),
    ]
    kept, merged = dedupe_requirements(reqs)
    assert merged == 0 and len(kept) == 3


# --- polarity guard: same words, opposite meaning must NEVER merge -----------
def test_polarity_conflict_detects_direction_and_negation():
    from rga.agents.pipeline import _polarity_conflict

    assert _polarity_conflict("sort by price from low to high", "sort by price from high to low")
    assert _polarity_conflict("the system shall store card data", "the system shall not store card data")
    assert _polarity_conflict("include out-of-stock items", "exclude out-of-stock items")
    # genuine same-polarity paraphrases are NOT a conflict
    assert not _polarity_conflict("the system shall allow guest checkout",
                                  "guest checkout shall be allowed by the system")
    assert not _polarity_conflict("shall never store raw card details",
                                  "raw card details shall never be stored")


def test_directional_opposites_never_merge():
    """Identical word set, opposite direction — merging would silently drop one direction."""
    a = _req("EX-1", "The system shall sort products by price from low to high.", RType.functional)
    b = _req("EX-2", "The system shall sort products by price from high to low.", RType.functional, doc="d2")
    kept, merged = dedupe_requirements([a, b])
    assert merged == 0 and len(kept) == 2


def test_negation_opposites_never_merge():
    a = _req("EX-1", "The system shall store raw card details.", RType.functional)
    b = _req("EX-2", "The system shall not store raw card details.", RType.functional, doc="d2")
    kept, merged = dedupe_requirements([a, b])
    assert merged == 0 and len(kept) == 2


def test_normalize_rejects_meaning_flipping_rewrite():
    """A 'mechanical' rewrite that drops a negation must NOT be adopted (it inverts meaning)."""
    from rga.agents.normalize import auto_normalize
    from rga.models import Quality

    r = _req("EX-1", "The system shall not store raw card details.", RType.functional)
    r.quality = Quality(suggested_rewrite="The system shall store raw card details.")
    auto_normalize(r)
    assert "not store" in r.statement                      # negation preserved
    assert r.quality.suggested_rewrite is not None          # rewrite rejected, still flagged


def test_similarity_is_symmetric_and_bounded():
    s = _statement_similarity("A shall do X", "X shall do A by the system")
    assert 0.0 <= s <= 1.0
    assert _statement_similarity("same text", "same text") == 1.0


# --- A4 semantic de-duplication (LLM arbitration, mocked) --------------------
def test_semantic_dedupe_merges_by_meaning_and_keeps_evidence():
    import json as _json

    from rga.agents.dedup_semantic import semantic_dedupe
    from rga.llm.mock import MockProvider

    # three functional reqs: 1 & 2 are the same obligation worded differently; 3 is distinct.
    a = _req("EX-a", "The system shall persist a shopper's cart across sessions.", RType.functional, doc="brd")
    b = _req("EX-b", "A registered customer's basket must be retained between visits and devices.", RType.functional, doc="backlog")
    c = _req("EX-c", "The system shall allow guest checkout without an account.", RType.functional, doc="emails")
    # mock returns the LLM grouping: items 1 and 2 are the same (1-based within the candidate window)
    prov = MockProvider(responses=[_json.dumps({"groups": [[1, 2]]})])

    kept, merged = semantic_dedupe(prov, [a, b, c], run_llm=True, candidate_threshold=0.2)
    assert merged == 1 and len(kept) == 2
    survivor = next(r for r in kept if r.id in ("EX-a", "EX-b"))
    assert {s.doc_id for s in survivor.source_refs} == {"brd", "backlog"}  # evidence merged
    assert "EX-c" in {r.id for r in kept}  # the distinct one survives untouched


def test_semantic_dedupe_noop_without_provider():
    from rga.agents.dedup_semantic import semantic_dedupe

    reqs = [_req("EX-1", "s one", RType.functional), _req("EX-2", "s two", RType.functional)]
    kept, merged = semantic_dedupe(None, reqs, run_llm=True)
    assert merged == 0 and len(kept) == 2


def test_semantic_dedupe_merges_across_types():
    """The same obligation captured under two different types (assumption vs constraint) must merge."""
    import json as _json

    from rga.agents.dedup_semantic import semantic_dedupe
    from rga.llm.mock import MockProvider

    a = _req("EX-a", "A data-retention rule for customer data must be defined and agreed with Legal.", RType.assumption)
    b = _req("EX-b", "A data-retention rule must be defined and agreed upon with the Legal team.", RType.constraint, doc="d2")
    prov = MockProvider(responses=[_json.dumps({"groups": [[1, 2]]})])
    kept, merged = semantic_dedupe(prov, [a, b], candidate_threshold=0.3)
    assert merged == 1 and len(kept) == 1  # cross-type duplicate collapsed
    assert kept[0].duplicate_of  # the absorbed id is recorded


# --- conflict detection ------------------------------------------------------
def test_conflict_detection_flags_contradiction_and_keeps_both():
    import json as _json

    from rga.agents.conflict import detect_conflicts
    from rga.llm.mock import MockProvider

    a = _req("EX-a", "The checkout flow shall have exactly five steps.", RType.functional)
    b = _req("EX-b", "The checkout flow shall have no more than three steps.", RType.functional, doc="d2")
    c = _req("EX-c", "The system shall let a user reset their password by email.", RType.functional, doc="d3")
    prov = MockProvider(responses=[_json.dumps({"pairs": [{"a": 1, "b": 2, "reason": "5 vs 3 steps"}]})])
    out = detect_conflicts(prov, [a, b, c], threshold=0.2)
    assert len(out) == 1
    assert {out[0]["a_id"], out[0]["b_id"]} == {"EX-a", "EX-b"}
    # conflict detection never removes requirements — it only reports
    assert {a.id, b.id, c.id} == {"EX-a", "EX-b", "EX-c"}


def test_conflict_detection_noop_without_provider():
    from rga.agents.conflict import detect_conflicts

    reqs = [_req("EX-1", "s one", RType.functional), _req("EX-2", "s two", RType.functional)]
    assert detect_conflicts(None, reqs) == []


def test_conflict_kept_even_when_reason_mentions_non_conflict_words():
    """C-M6: the verdict is `is_conflict`, not prose — a real conflict whose reason happens to
    contain 'not mutually exclusive' must NOT be dropped by a substring backstop."""
    import json as _json

    from rga.agents.conflict import detect_conflicts
    from rga.llm.mock import MockProvider

    a = _req("EX-a", "The system shall erase all personal data on a customer erasure request.", RType.functional)
    b = _req("EX-b", "The system shall retain personal data in invoice records despite an erasure request.", RType.functional, doc="d2")
    prov = MockProvider(responses=[_json.dumps({"pairs": [
        {"a": 1, "b": 2, "is_conflict": True,
         "reason": "Not merely 'not mutually exclusive' — erasure vs mandated retention collide."},
    ]})])
    out = detect_conflicts(prov, [a, b], threshold=0.2)
    assert len(out) == 1 and {out[0]["a_id"], out[0]["b_id"]} == {"EX-a", "EX-b"}


def test_conflict_dropped_when_model_sets_is_conflict_false():
    import json as _json

    from rga.agents.conflict import detect_conflicts
    from rga.llm.mock import MockProvider

    a = _req("EX-a", "The system shall persist the cart across sessions.", RType.functional)
    b = _req("EX-b", "The system shall persist the cart across sessions and devices for logged-in users.", RType.functional, doc="d2")
    prov = MockProvider(responses=[_json.dumps({"pairs": [
        {"a": 1, "b": 2, "is_conflict": False, "reason": "compatible refinement, not a contradiction"},
    ]})])
    assert detect_conflicts(prov, [a, b], threshold=0.2) == []
