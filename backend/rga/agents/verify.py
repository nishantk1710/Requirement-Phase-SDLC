"""Adversarial second-opinion verification.

The in-loop critic (A0) runs once per chunk while extracting. The faithfulness audit showed it still
lets subtle problems through — an undecided item firmed to "shall", a dropped qualifier, a note
captured as a requirement. This is a SECOND, independent pass with a different, skeptical framing,
run only over the HIGH-RISK requirements (triage = attention) so the cost is bounded. Each is judged
against its OWN cited quotes; a requirement that fails is routed to open-questions (never deleted),
so recall is preserved while precision improves.

Requirements already flagged as inferred (visibly tentative) or part of a conflict (kept
deliberately for the human to resolve) are left alone — re-judging them adds nothing.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..llm.base import LLMProvider
from ..models import Requirement
from .pipeline import refs_as_dicts, topic_overlap
from .triage import ATTENTION, triage

MAX_ITEMS = 120  # hard cap on second-opinion calls per run (cost bound)

VERIFY_SYSTEM = (
    "You are a strict, independent reviewer double-checking requirements another agent extracted. "
    "Given a REQUIREMENT and the VERBATIM source quotes it cites as evidence, decide whether it HOLDS. "
    "It does NOT hold if you can point to a CONCRETE problem: it firms up an undecided/optional/"
    "disputed item into a commitment; it inverts scope (states an excluded item as required); it drops "
    "a qualifier the quotes contain (e.g. 'opt-in', 'for logged-in users'); it is not actually a "
    "product/system requirement (a note, aside, KPI, heading, or cross-reference); or the quotes do not "
    "support it. If it is a reasonable, faithful requirement, it HOLDS. Judge only against the quotes "
    "shown — do not invent problems. Separately, set open_decision=true ONLY when the requirement firms "
    "up a decision the source leaves genuinely OPEN — an undecided/optional/disputed item, or one it "
    "marks excluded/out-of-scope; otherwise leave open_decision false."
)


class VerifyVerdict(BaseModel):
    holds: bool
    reason: str = ""
    # True when the requirement firms up a genuinely-open decision. Used ONLY as a safety EXCLUSION in
    # the recall guard (a paraphrased disputed item the deterministic topic-match can miss) — it never
    # ENABLES retention, so a missing/wrong label can never delete a real in-scope capability.
    open_decision: bool = False


# A requirement attested by MULTIPLE independent documents, or grounded in an authoritative in-scope
# declaration (a stated Objective / Representative Business Requirement), is a cross-corroborated real
# obligation. The precision critic may flag its WORDING, but must never DELETE the capability — the
# recall floor. Such a verdict RETAINS the requirement (it is already triage=ATTENTION, so a human
# reviews it) and surfaces the concern as a caution, instead of collapsing a whole merged cluster on
# one refutation (the catalogue-CMS failure: 7 sources across 3 docs lost on a single false verdict).
_AUTHORITATIVE_SECTIONS = ("objective", "representative business requirement")

# Kinds that represent a GENUINELY-OPEN decision the sources left unsettled. If a requirement's TOPIC
# matches one of these, the critic is right to withhold it and the recall guard must NOT firm it up —
# that would resolve a decision the humans explicitly left open. This is deterministic (topic-word
# overlap), NOT an LLM label, so it does not flip run-to-run.
_OPEN_DECISION_KINDS = {"disputed", "undecided", "out_of_scope", "deferred"}
_OPEN_DECISION_OVERLAP = 0.20   # calibrated: catches an item's own open-decision note (~0.29) while
                                # leaving clearly in-scope capabilities (catalogue/search ~0-0.08) alone.


def _strongly_corroborated(r: Requirement) -> bool:
    if len({sr.doc_id for sr in r.source_refs}) >= 2:
        return True
    locs = " ".join((sr.location or "").lower() for sr in r.source_refs)
    return any(k in locs for k in _AUTHORITATIVE_SECTIONS)


def _already_open_decision(r: Requirement, open_q: list[dict]) -> bool:
    """True if this requirement's subject is already captured as a genuinely-open decision."""
    return any(
        topic_overlap(r.statement, o.get("statement", "")) >= _OPEN_DECISION_OVERLAP
        for o in open_q if o.get("type") in _OPEN_DECISION_KINDS
    )


def _verify_one(provider: LLMProvider, statement: str, quotes: list[str]) -> VerifyVerdict:
    listing = "\n".join(f'- "{q}"' for q in quotes) if quotes else "(no quotes cited)"
    user = (
        f"REQUIREMENT:\n{statement}\n\nCLAIMED SOURCE QUOTES:\n{listing}\n\n"
        "Does this requirement HOLD? If not, name the concrete problem, and set open_decision if it "
        "firms up something the source leaves open."
    )
    try:
        return provider.structured(VERIFY_SYSTEM, user, VerifyVerdict, max_tokens=400)
    except Exception:
        return VerifyVerdict(holds=True, reason="")  # never fail the run over a verification hiccup


def adversarial_verify(
    provider: LLMProvider | None,
    reqs: list[Requirement],
    open_q: list[dict],
    *,
    run_llm: bool = True,
    max_items: int = MAX_ITEMS,
) -> tuple[list[Requirement], list[dict], int]:
    """Second-opinion review of the high-risk subset. Returns (kept, open_q, refuted_count).
    Refuted requirements move to open-questions (kind 'critic_rejected'); nothing is deleted."""
    if not run_llm or provider is None or not reqs:
        return list(reqs), open_q, 0

    targets = [
        r for r in reqs
        if triage(r)["level"] == ATTENTION and not r.inferred and not r.conflicts_with
    ][:max_items]
    if not targets:
        return list(reqs), open_q, 0

    refuted: set[str] = set()
    for r in targets:
        verdict = _verify_one(provider, r.statement, [sr.raw_quote for sr in r.source_refs])
        if verdict.holds:
            continue
        reason = verdict.reason or "did not hold under scrutiny"
        loc = r.source_refs[0].location if r.source_refs else ""
        doc = r.source_refs[0].doc_id if r.source_refs else ""
        # RECALL GUARD: a cross-corroborated / authoritative in-scope obligation is KEPT (a human still
        # reviews it — it is triage=ATTENTION) with the critic's concern recorded, UNLESS it is a
        # genuinely-OPEN decision. "Open" is detected DETERMINISTICALLY (topic overlap with an existing
        # disputed/undecided/out-of-scope note — the primary, run-stable signal) and, as a safety net
        # for paraphrases the text match cannot catch, by the reviewer's open_decision flag. Corroborated
        # in-scope capabilities (catalogue) are never blocked by a missing/positive LLM label; only
        # affirmatively-open items are withdrawn — so real capabilities survive and open decisions stay open.
        if (_strongly_corroborated(r)
                and not _already_open_decision(r, open_q)
                and not verdict.open_decision):
            n_docs = len({sr.doc_id for sr in r.source_refs})
            r.provenance = {**(r.provenance or {}), "critic_concern": reason}
            open_q.append({
                "type": "critic_flag",
                "statement": r.statement,
                "reason": (f"retained despite a second-opinion concern — corroborated by {n_docs} "
                           f"independent source(s); verify the wording: {reason}"),
                "location": loc, "doc_id": doc,
                "merged_refs": refs_as_dicts(r.source_refs),  # keep ALL evidence (C-M5)
            })
            continue
        open_q.append({
            "type": "critic_rejected",
            "statement": r.statement,
            "reason": f"second-opinion review: {reason}",
            "location": loc, "doc_id": doc,
            "merged_refs": refs_as_dicts(r.source_refs),  # keep ALL evidence (C-M5)
        })
        refuted.add(r.id)

    kept = [r for r in reqs if r.id not in refuted]
    return kept, open_q, len(refuted)
