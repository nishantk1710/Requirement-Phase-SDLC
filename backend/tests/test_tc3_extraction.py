"""TC3.1-TC3.4 (hardened) — Extraction (A1) + Critic (A0), driven by the mock so it is
deterministic and needs no API key.

Goals:
  TC3.1  every stored requirement's raw_quote is the SOURCE's own bytes at [start:end]
         (a fabricated quote is dropped; a trivial substring is not accepted) — a real,
         non-circular byte-level traceability check.
  TC3.2  the critic rejects an ungrounded/invented requirement (-> open-questions),
         keeps a grounded one; inferred-without-span goes to open-questions (not dropped).
  TC3.3  a requirement supported by two spans is captured multi-span, byte-accurate.
  TC3.4  the bounded loop converges and respects the hard cap.
  Plus:  cross-chunk duplicates MERGE source_refs; open-questions are deduped.
"""

from __future__ import annotations

import json

from rga.agents.pipeline import extract_document
from rga.llm.mock import MockProvider
from rga.models import Chunk

CHUNK = Chunk(
    doc_id="d1",
    project_id="P",
    source_type="brd",
    index=0,
    location="3.1",
    text="The system shall allow login. Passwords must be at least 8 characters.",
    start=0,
    end=69,
)


def _rq(statement, quotes, rtype="functional", inferred=False):
    return {
        "statement": statement,
        "rtype": rtype,
        "quotes": quotes,
        "inferred": inferred,
        "rationale": "r",
        "confidence": 0.9,
    }


def _ext(reqs):
    return json.dumps({"requirements": reqs})


def _crit_batch(verdicts, missed=None):
    """Batch critic result: verdicts = list of {index, grounded, invented?, reason?}."""
    return json.dumps({"verdicts": verdicts, "possibly_missed": missed or []})


def _assert_byte_accurate(accepted, chunk=CHUNK):
    """The stored quote must be the SOURCE's own bytes at the stored offsets."""
    for r in accepted:
        assert r.source_refs, f"{r.id} has no source_refs"
        for sr in r.source_refs:
            assert sr.start is not None and sr.end is not None
            assert sr.raw_quote == chunk.text[sr.start : sr.end]
            assert sr.raw_quote in chunk.text  # true byte-level substring


# --- TC3.1 -------------------------------------------------------------------
def test_tc3_1_fabricated_dropped_kept_are_byte_accurate():
    responses = [
        _ext(
            [
                _rq("The system allows a user to log in.", ["The system shall allow login."]),
                _rq("The system supports two-factor auth.", ["The system shall support two-factor authentication."]),
            ]
        )
    ]
    accepted, _ = extract_document(MockProvider(responses=responses), [CHUNK], max_passes=1, run_critic=False)
    assert [r.statement for r in accepted] == ["The system allows a user to log in."]
    _assert_byte_accurate(accepted)


def test_tc3_1_trivial_quote_is_rejected():
    # "The" is present in the chunk but not substantive -> not grounding -> dropped
    accepted, _ = extract_document(
        MockProvider(responses=[_ext([_rq("The system does something.", ["The"])])]),
        [CHUNK],
        max_passes=1,
        run_critic=False,
    )
    assert accepted == []


# --- TC3.3 -------------------------------------------------------------------
def test_tc3_3_multi_span_byte_accurate():
    responses = [
        _ext(
            [
                _rq(
                    "Login requires an 8-plus character password.",
                    ["The system shall allow login.", "Passwords must be at least 8 characters."],
                )
            ]
        )
    ]
    accepted, _ = extract_document(MockProvider(responses=responses), [CHUNK], max_passes=1, run_critic=False)
    assert len(accepted) == 1 and len(accepted[0].source_refs) == 2
    _assert_byte_accurate(accepted)


# --- TC3.2 -------------------------------------------------------------------
def test_tc3_2_critic_rejects_ungrounded():
    responses = [
        _ext([_rq("The system emails the manager on every login.", ["The system shall allow login."])]),
        _crit_batch([{"index": 0, "grounded": False, "invented": True, "reason": "no support for emailing a manager"}]),
    ]
    accepted, open_q = extract_document(MockProvider(responses=responses), [CHUNK], max_passes=1, run_critic=True)
    assert accepted == []
    assert len(open_q) == 1 and open_q[0]["type"] == "critic_rejected"


def test_tc3_2_critic_keeps_grounded():
    responses = [
        _ext([_rq("The system allows a user to log in.", ["The system shall allow login."])]),
        _crit_batch([{"index": 0, "grounded": True}]),
    ]
    accepted, open_q = extract_document(MockProvider(responses=responses), [CHUNK], max_passes=1, run_critic=True)
    assert len(accepted) == 1 and open_q == []
    _assert_byte_accurate(accepted)


def test_tc3_2_critic_fails_closed_on_missing_verdict():
    """B-H1: a candidate the critic does NOT adjudicate must be routed to open-questions
    (unverified), never silently accepted."""
    responses = [
        _ext([
            _rq("A user can log in.", ["The system shall allow login."]),
            _rq("Passwords are at least 8 characters.", ["Passwords must be at least 8 characters."]),
        ]),
        _crit_batch([{"index": 0, "grounded": True}]),  # verdict for candidate 0 ONLY
    ]
    accepted, open_q = extract_document(MockProvider(responses=responses), [CHUNK], max_passes=1, run_critic=True)
    assert [r.statement for r in accepted] == ["A user can log in."]           # verified one kept
    assert any(o["type"] == "critic_rejected" and "unverified" in o["reason"] for o in open_q)


def test_tc3_2_inferred_without_span_goes_to_open_questions():
    responses = [_ext([_rq("The system tracks user sessions.", ["a span not in the source"], inferred=True)])]
    accepted, open_q = extract_document(MockProvider(responses=responses), [CHUNK], max_passes=1, run_critic=False)
    assert accepted == []
    assert len(open_q) == 1 and open_q[0]["type"] == "inferred"


# --- merge + dedup -----------------------------------------------------------
def test_dedup_merges_source_refs_across_chunks():
    chunk_b = Chunk(
        doc_id="d2",
        project_id="P",
        source_type="email",
        index=0,
        location="Email 2",
        text="Also, the system shall allow login. Thanks!",
        start=0,
        end=43,
    )
    responses = [
        _ext([_rq("The system allows a user to log in.", ["The system shall allow login."])]),
        _ext([_rq("The system allows a user to log in.", ["the system shall allow login."])]),  # diff case, other doc
    ]
    accepted, _ = extract_document(MockProvider(responses=responses), [CHUNK, chunk_b], max_passes=1, run_critic=False)
    assert len(accepted) == 1
    assert {sr.doc_id for sr in accepted[0].source_refs} == {"d1", "d2"}
    assert len(accepted[0].source_refs) == 2  # evidence merged, not discarded


def test_open_questions_deduped_across_passes():
    ext = _ext(
        [
            _rq("The system allows a user to log in.", ["The system shall allow login."]),
            _rq("The system tracks sessions.", ["a span not present"], inferred=True),
        ]
    )
    accepted, open_q = extract_document(MockProvider(responses=[ext, ext]), [CHUNK], max_passes=2, run_critic=False)
    assert len(accepted) == 1
    assert len(open_q) == 1 and open_q[0]["type"] == "inferred"  # not recorded twice


# --- TC3.4 -------------------------------------------------------------------
def test_tc3_4_loop_converges():
    same = _ext(
        [
            _rq("A requirement about the login flow.", ["The system shall allow login."]),
            _rq("A rule about password minimum length.", ["Passwords must be at least 8 characters."]),
        ]
    )
    prov = MockProvider(responses=[same, same])
    accepted, _ = extract_document(prov, [CHUNK], max_passes=5, run_critic=False)
    assert len(accepted) == 2 and prov.calls == 2  # stopped at convergence, not cap


def test_tc3_4_loop_respects_cap():
    # distinct statements AND distinct spans so near-dedup doesn't merge them
    r1 = _ext([_rq("Users may sign in to the platform.", ["The system shall allow login."])])
    r2 = _ext([_rq("Passwords have a minimum length.", ["Passwords must be at least 8 characters."])])
    r3 = _ext([_rq("A third requirement never fetched.", ["The system shall allow login."])])
    prov = MockProvider(responses=[r1, r2, r3])
    accepted, _ = extract_document(prov, [CHUNK], max_passes=2, run_critic=False)
    assert prov.calls == 2 and len(accepted) == 2  # bounded: r3 never fetched


def test_extraction_carries_feature_and_nfr_category():
    reqs = [
        {"statement": "Users can search products.", "rtype": "functional", "feature": "Search & Browse",
         "quotes": ["The system shall allow login."], "inferred": False, "rationale": "r", "confidence": 0.9},
        {"statement": "Pages load within 2 seconds.", "rtype": "non_functional", "nfr_category": "performance",
         "quotes": ["Passwords must be at least 8 characters."], "inferred": False, "rationale": "r", "confidence": 0.9},
    ]
    accepted, _ = extract_document(MockProvider(responses=[_ext(reqs)]), [CHUNK], max_passes=1, run_critic=False)
    by_type = {r.rtype.value: r for r in accepted}
    assert by_type["functional"].feature == "Search & Browse"
    assert by_type["functional"].nfr_category is None       # feature only on functional
    assert by_type["non_functional"].nfr_category == "performance"
    assert by_type["non_functional"].feature is None        # nfr_category only on non-functional
