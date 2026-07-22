"""Reconciliation set (audit fixes 2–6):
  * scope/status classifier (Fix 2) — evidence markers force an item to non-firm;
  * cross-chunk reconciliation (Fix 3) — a firm requirement duplicating a non-firm open item is withdrawn;
  * open-questions post-processing (Fix 4 + 5) — redundant ungrounded notes dropped, near-dups collapsed;
  * conflict-agent polish (Fix 6) — pairs the model itself judges non-conflicts are dropped.
"""

from __future__ import annotations

import json as _json

from rga.agents.pipeline import postprocess_open_questions
from rga.agents.reconcile import reconcile_scope
from rga.agents.scope_classifier import classify_scope_status
from rga.llm.mock import MockProvider
from rga.models import Requirement, RType, SourceRef


def _req(rid, statement, rtype=RType.functional, doc="d1", start=0):
    return Requirement(
        id=rid, project_id="P", statement=statement, rtype=rtype,
        source_refs=[SourceRef(doc_id=doc, source_type="brd", location="x",
                               raw_quote=statement, start=start, end=start + len(statement))],
    )


# --- Fix 2: scope/status classifier -----------------------------------------
def test_scope_classifier_flags_explicit_markers():
    assert classify_scope_status(["Reviews & ratings — DISPUTED, not confirmed in scope."])[0] == "disputed"
    assert classify_scope_status(["Compare view, maybe-not-v1, DECISION PENDING."])[0] == "undecided"
    # marker living in the section heading (location), not the sentence
    assert classify_scope_status(["Native mobile apps"], location="Broadly out (for now)")[0] == "out_of_scope"
    assert classify_scope_status(["Wishlist is a could-have for phase 2."])[0] == "deferred"


def test_scope_classifier_ignores_benign_modals():
    # an ordinary "may" in a normal sentence must NOT be flagged
    flag, reason = classify_scope_status(["Users may log into the system with their email address."])
    assert flag is None and reason is None


def test_scope_classifier_ignores_idioms_but_keeps_real_deferral():
    """B-M4/C-M2: common English / feature names must NOT read as a scope hedge, but explicit
    deferral context still must."""
    assert classify_scope_status(["Users could have multiple saved addresses on file."])[0] is None
    assert classify_scope_status(["The dashboard shall show each open item in the queue."])[0] is None
    assert classify_scope_status(["The system shall support phase 2 onboarding of vendors."])[0] is None
    # genuine deferral is still detected
    assert classify_scope_status(["This feature is deferred to phase 2."])[0] == "deferred"
    assert classify_scope_status(["Reorder is a could-have for phase 2."])[0] == "deferred"


def test_scope_classifier_out_of_scope_wins_over_weaker_markers():
    # most-decisive family is checked first
    assert classify_scope_status(["This is out of scope, and also still undecided."])[0] == "out_of_scope"


def test_scope_classifier_flags_deliberation_markers():
    """Fix 2: unambiguous "we are still deciding" phrasings gate to `undecided` at the source."""
    for ev in (
        "We are leaning towards enabling COD but have not decided yet.",
        "Whether to support wishlists is still being decided.",
        "The return window duration should be decided by Finance.",
        "The delivery SLA is yet to be confirmed.",
        "The team hasn't decided if guest checkout ships in v1.",
    ):
        assert classify_scope_status([ev])[0] == "undecided", ev


def test_scope_classifier_deliberation_markers_stay_high_precision():
    """Recall guard: ordinary product text that merely contains 'maybe', a question mark, or a
    benign 'confirm' must NOT be flagged (bare hedges were deliberately excluded)."""
    assert classify_scope_status(["The system shall let the user confirm the order by email."])[0] is None
    assert classify_scope_status(["The FAQ shall answer 'how do I return an item?'"])[0] is None
    assert classify_scope_status(["The cart may hold up to maybe 50 items."])[0] is None


# --- Fix 3: cross-chunk reconciliation --------------------------------------
def test_reconcile_withdraws_firm_requirement_that_duplicates_a_nonfirm_item():
    r1 = _req("EX-r1", "The system shall support product reviews and ratings.")
    r2 = _req("EX-r2", "The system shall send order confirmation emails.")
    open_q = [{
        "type": "disputed",
        "statement": "Product reviews and ratings are disputed and not confirmed in scope.",
        "reason": "source marks this disputed", "location": "HRZN-31", "doc_id": "backlog",
    }]
    prov = MockProvider(responses=[_json.dumps({"withdraw": [1]})])
    kept, oq, withdrawn = reconcile_scope(prov, [r1, r2], open_q, candidate_threshold=0.2)

    assert withdrawn == 1
    assert [r.id for r in kept] == ["EX-r2"]              # the distinct one survives
    assert oq[0]["reconciled_from"] == ["EX-r1"]          # provenance recorded
    assert oq[0]["merged_refs"]                            # evidence folded onto the open item, not lost


def test_reconcile_noop_without_provider():
    r1 = _req("EX-1", "The system shall support reviews.")
    open_q = [{"type": "disputed", "statement": "Reviews are disputed."}]
    kept, oq, withdrawn = reconcile_scope(None, [r1], open_q)
    assert withdrawn == 0 and [r.id for r in kept] == ["EX-1"]


def test_reconcile_keeps_requirement_when_llm_declines():
    r1 = _req("EX-1", "The system shall support product reviews and ratings.")
    open_q = [{"type": "disputed", "statement": "Product reviews and ratings are disputed and not confirmed in scope."}]
    prov = MockProvider(responses=[_json.dumps({"withdraw": []})])  # model finds no true duplicate
    kept, oq, withdrawn = reconcile_scope(prov, [r1], open_q, candidate_threshold=0.2)
    assert withdrawn == 0 and [r.id for r in kept] == ["EX-1"]


# --- Fix 5: drop redundant ungrounded notes already grounded elsewhere ------
def test_postprocess_drops_ungrounded_note_already_a_firm_requirement():
    reqs = [_req("EX-1", "The system shall provide a shopping cart.")]
    open_q = [
        {"type": "ungrounded", "statement": "The system shall provide a shopping cart."},
        {"type": "undecided", "statement": "Cash on delivery is undecided."},
    ]
    out = postprocess_open_questions(reqs, open_q)
    kinds = [o["type"] for o in out]
    assert kinds == ["undecided"]  # the redundant ungrounded restatement is gone


# --- Fix 4: collapse near-verbatim duplicate open-questions ------------------
def test_postprocess_collapses_near_verbatim_duplicates():
    open_q = [
        {"type": "undecided", "statement": "The return window (7 days vs 10 days) is an open item that may need to be defined as a business rule."},
        {"type": "undecided", "statement": "The return window (7 days vs 10 days) is an open/undecided item that may need to be defined as a business rule."},
    ]
    out = postprocess_open_questions([], open_q)
    assert len(out) == 1


def test_postprocess_merge_keeps_most_decisive_kind():
    open_q = [
        {"type": "ungrounded", "statement": "The system shall support product reviews."},
        {"type": "disputed", "statement": "The system shall support product reviews."},
    ]
    out = postprocess_open_questions([], open_q)
    assert len(out) == 1 and out[0]["type"] == "disputed"  # decisive kind wins the merge


def test_postprocess_preserves_distinct_open_questions():
    open_q = [
        {"type": "undecided", "statement": "Cash on delivery is undecided."},
        {"type": "disputed", "statement": "Product reviews are disputed."},
        {"type": "out_of_scope", "statement": "Native mobile apps are out of scope."},
    ]
    out = postprocess_open_questions([], open_q)
    assert len(out) == 3


# --- LLM semantic dedup of open-questions -----------------------------------
def test_semantic_dedupe_open_questions_collapses_restatements_keeps_decisive_kind():
    from rga.agents.dedup_semantic import semantic_dedupe_open_questions

    oq = [
        {"type": "undecided", "statement": "The return window is 7 vs 10 days and is undecided."},
        {"type": "non_requirement", "statement": "Return window duration (7 or 10 days) is an open item."},
        {"type": "disputed", "statement": "Users may reset their password by email link only."},
    ]
    prov = MockProvider(responses=[_json.dumps({"groups": [[1, 2]]})])  # items 1 & 2 = same topic
    kept, merged = semantic_dedupe_open_questions(prov, oq, candidate_threshold=0.2)
    assert merged == 1 and len(kept) == 2
    kinds = {k["type"] for k in kept}
    assert kinds == {"undecided", "disputed"}          # decisive kind (undecided) survives, not non_requirement


def test_semantic_dedupe_open_questions_noop_without_provider():
    from rga.agents.dedup_semantic import semantic_dedupe_open_questions

    oq = [{"type": "undecided", "statement": "a"}, {"type": "undecided", "statement": "b"}]
    kept, merged = semantic_dedupe_open_questions(None, oq)
    assert merged == 0 and len(kept) == 2


# --- tiered Appendix C rendering --------------------------------------------
def test_open_questions_tiered_and_ordered():
    from rga.generate.open_questions import compile_open_questions, open_questions_markdown

    raw = [
        {"type": "non_requirement", "statement": "Most traffic is mobile.", "reason": "context"},
        {"type": "ungrounded", "statement": "The system shall provide search.", "reason": "verify"},
        {"type": "undecided", "statement": "COD is undecided.", "reason": "decide"},
        {"type": "conflict", "statement": "A  ⟷  B", "reason": "contradiction"},
    ]
    items = compile_open_questions(raw)
    assert items[0]["kind"] == "conflict"                          # most-actionable leads the list
    assert {it["kind"] for it in items if it["tier"] == "A"} == {"conflict", "undecided"}
    assert next(it["tier"] for it in items if it["kind"] == "non_requirement") == "C"

    md = open_questions_markdown(items)
    assert "A · Decisions Required" in md
    assert "C · Informational" not in md                           # Tier C section removed on request
    assert md.count("| ID | Description |") == 2                    # only A and B tables render now


# --- Fix 6 / C-M6: conflict agent keys on the structured is_conflict verdict ------------------
def test_conflict_agent_drops_pairs_the_model_marks_not_a_conflict():
    from rga.agents.conflict import detect_conflicts

    a = _req("EX-a", "The checkout flow shall be completed in exactly five steps.")
    b = _req("EX-b", "The checkout flow shall be completed in no more than three steps.", doc="d2")
    c = _req("EX-c", "The checkout flow shall display a PDF invoice to the customer.", doc="d3")
    d = _req("EX-d", "The checkout flow shall generate a PDF invoice for the customer.", doc="d4")
    prov = MockProvider(responses=[_json.dumps({"pairs": [
        {"a": 1, "b": 2, "is_conflict": True, "reason": "five steps versus three steps — mutually exclusive"},
        {"a": 3, "b": 4, "is_conflict": False, "reason": "both produce a PDF invoice, redundant"},
    ]})])
    out = detect_conflicts(prov, [a, b, c, d], threshold=0.2)
    assert len(out) == 1                                   # is_conflict=False pair is dropped
    assert {out[0]["a_id"], out[0]["b_id"]} == {"EX-a", "EX-b"}


def test_conflict_agent_drops_modality_only_variants_and_one_lines_reason():
    """Fix 3: a pair the model wrongly calls a conflict but that is the SAME obligation at a
    different strength ('shall' vs 'may') is dropped deterministically; a genuine value clash is
    kept; and a multi-line 'scratch reasoning' reason is reduced to a single line."""
    from rga.agents.conflict import detect_conflicts

    a = _req("EX-a", "The cart shall persist its contents across sessions.")
    b = _req("EX-b", "The cart may persist its contents across sessions.", doc="d2")
    c = _req("EX-c", "The checkout flow shall be completed in exactly five steps.", doc="d3")
    d = _req("EX-d", "The checkout flow shall be completed in no more than three steps.", doc="d4")
    prov = MockProvider(responses=[_json.dumps({"pairs": [
        {"a": 1, "b": 2, "is_conflict": True, "reason": "shall vs may"},          # modality-only -> drop
        {"a": 3, "b": 4, "is_conflict": True,
         "reason": "Let me think.\nStep 1: five != three.\nTherefore they contradict."},  # real -> keep
    ]})])
    out = detect_conflicts(prov, [a, b, c, d], threshold=0.2)
    assert len(out) == 1                                        # modality-only variant dropped
    assert {out[0]["a_id"], out[0]["b_id"]} == {"EX-c", "EX-d"}
    assert "\n" not in out[0]["reason"] and out[0]["reason"] == "Let me think."  # one-lined
