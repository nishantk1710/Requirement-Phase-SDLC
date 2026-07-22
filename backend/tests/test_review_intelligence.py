"""Review-intelligence layer: owner routing (#7), mechanical normalization (#4), and
decision clustering with propose/options/owner/affected (#3, #6)."""

from __future__ import annotations

from rga.agents.decisions import build_decisions
from rga.agents.normalize import auto_normalize
from rga.agents.owner import owner_of
from rga.models import Quality, Requirement, RType, SourceRef


def _req(rid, stmt, feature=None, conflicts=None, flags=None, rewrite=None, rtype=RType.functional):
    return Requirement(
        id=rid, project_id="P", statement=stmt, rtype=rtype, feature=feature,
        conflicts_with=conflicts or [],
        quality=Quality(ambiguity_flags=flags or [], suggested_rewrite=rewrite),
        source_refs=[SourceRef(doc_id="d", source_type="brd", location="x", raw_quote=stmt, start=0, end=len(stmt))],
    )


# --- #7 owner routing --------------------------------------------------------
def test_owner_routing_by_domain_and_type():
    assert owner_of("The system shall process card payments via the PSP.") == "Finance"
    assert owner_of("When a return is approved, arrange reverse pickup.") == "CX / Operations"
    assert owner_of("The system shall capture marketing consent under GDPR.") == "Legal"
    assert owner_of("TTFB shall be under 600ms.", rtype="non_functional") == "Engineering"
    assert owner_of("Reviews and wishlist are out of scope for v1.") == "Product / Sponsor"
    assert owner_of("The homepage shall show a hero banner.") == "Product Owner"


def test_owner_routing_does_not_over_route_generic_terms_to_legal():
    """L2: bare 'terms'/'policy' must not send everything to Legal."""
    assert owner_of("The system shall autocomplete search terms as the user types.") != "Legal"
    assert owner_of("The privacy policy link shall appear in the footer.") == "Legal"  # legal context


# --- #4 mechanical normalization --------------------------------------------
def test_cosmetic_whitespace_and_period_fixed():
    r = _req("EX-1", "The system  shall   allow checkout")
    auto_normalize(r)
    assert r.statement == "The system shall allow checkout."


def test_mechanical_flag_cleared_substantive_kept():
    r = _req("EX-1", "The system shall be fast.", flags=["passive voice", "vague term: fast"])
    auto_normalize(r)
    assert r.quality.ambiguity_flags == ["vague term: fast"]  # mechanical dropped, substantive kept


def test_ears_rewrite_adopted_only_when_meaning_preserved():
    keep = _req("EX-1", "The system shall validate input.", rewrite="The system shall validate the input.")
    auto_normalize(keep)
    assert keep.statement == "The system shall validate the input." and keep.quality.suggested_rewrite is None
    # modal change (may -> shall) is a meaning change: do NOT adopt
    hold = _req("EX-2", "The system may log events.", rewrite="The system shall log events.")
    auto_normalize(hold)
    assert hold.statement == "The system may log events." and hold.quality.suggested_rewrite == "The system shall log events."


# --- #3 / #6 decisions -------------------------------------------------------
def test_conflict_becomes_one_decision_with_recommendation():
    a = _req("EX-a", "Checkout shall have exactly five steps.", conflicts=["EX-b"])
    b = _req("EX-b", "Checkout shall have no more than three steps.", conflicts=["EX-a"])
    conf = [d for d in build_decisions([a, b], []) if d["kind"] == "conflict"]
    assert len(conf) == 1
    d = conf[0]
    assert set(d["affected"]) == {"EX-a", "EX-b"} and d["recommended"] and d["options"] and d["id"]


def test_scope_items_cluster_into_one_decision_and_propagate():
    reqs = [
        _req("EX-1", "The system shall display product reviews on the PDP."),
        _req("EX-2", "The system shall show star ratings for products."),
        _req("EX-3", "The system shall allow guest checkout."),
    ]
    open_q = [
        {"type": "disputed", "statement": "Product reviews and ratings are disputed and not confirmed in scope."},
        {"type": "undecided", "statement": "Whether reviews are included in the first release is undecided."},
    ]
    scope = [d for d in build_decisions(reqs, open_q) if d["kind"] in ("disputed", "undecided")]
    assert len(scope) == 1  # two review items -> ONE decision
    d = scope[0]
    assert "EX-1" in d["affected"] and "EX-2" in d["affected"] and "EX-3" not in d["affected"]
    assert d["owner"] == "Product / Sponsor" and d["recommended"] and d["reason"]


def test_generic_shared_word_does_not_over_cluster_or_over_propagate():
    """C-M7: two distinct scope calls sharing only a generic noun ('customer') must NOT merge into
    one decision, and each resolution must affect only its own topic's requirement."""
    reqs = [
        _req("EX-1", "The system shall let a customer write product reviews."),
        _req("EX-2", "The system shall enroll a customer in a loyalty programme."),
    ]
    open_q = [
        {"type": "disputed", "statement": "Customer reviews are disputed and not confirmed in scope."},
        {"type": "deferred", "statement": "Customer loyalty programme is deferred to phase 2."},
    ]
    scope = [d for d in build_decisions(reqs, open_q) if d["kind"] in ("disputed", "deferred", "undecided")]
    assert len(scope) == 2  # two DISTINCT decisions, not collapsed via "customer"
    by_kind = {d["kind"]: d for d in scope}
    assert by_kind["disputed"]["affected"] == ["EX-1"]   # reviews topic only
    assert by_kind["deferred"]["affected"] == ["EX-2"]   # loyalty topic only


def test_common_word_does_not_over_propagate_affected_set():
    """FIX 0b: a scope decision must not sweep in every requirement that shares a CORPUS-COMMON word
    ('delivery'). Only requirements sharing a SPECIFIC (rare) term are affected."""
    reqs = [_req(f"D{i}", f"The system shall handle delivery step number {i} for customer orders.", feature="Delivery")
            for i in range(15)]
    reqs.append(_req("X1", "The system shall integrate with a live carrier tracking API for delivery.", feature="Delivery"))
    open_q = [{"type": "out_of_scope", "statement": "Live carrier tracking API integration is out of scope."}]
    scope = [d for d in build_decisions(reqs, open_q) if d["kind"] == "out_of_scope"]
    assert len(scope) == 1
    aff = scope[0]["affected"]
    assert "X1" in aff and "D0" not in aff and len(aff) <= 3   # 'delivery' common -> not swept in


def test_recommendations_are_concrete_verdicts_not_decide_statements():
    """P5.1: recall-first VERDICTS, not 'decide…/confirm whether…' punts."""
    from rga.agents.decisions import _recommend
    assert _recommend("disputed")[0].lower().startswith("include in v1")     # uncertain -> include
    assert _recommend("undecided")[0].lower().startswith("include in v1")
    assert _recommend("gap")[0].lower().startswith("add requirement")        # gap -> add
    assert "exclude from v1" in _recommend("out_of_scope")[0].lower()        # explicit signal kept
    assert "phase 2" in _recommend("deferred")[0].lower()
    for kind in ("disputed", "undecided", "gap"):                            # no lingering "decide" punt
        assert "decide" not in _recommend(kind)[0].lower()


def test_possible_miss_and_gap_produce_decisions():
    open_q = [
        {"type": "possible_miss", "statement": "Faceted navigation on the PLP is not captured."},
        {"type": "gap", "statement": "No requirements address Security."},
    ]
    decs = build_decisions([], open_q)
    kinds = {d["kind"] for d in decs}
    assert "possible_miss" in kinds and "gap" in kinds
    assert all(d["id"] and d["owner"] and d["options"] and d["recommended"] for d in decs)
