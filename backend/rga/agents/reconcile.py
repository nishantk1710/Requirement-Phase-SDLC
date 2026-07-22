"""Cross-chunk scope reconciliation.

The same obligation often appears in several chunks. One mention may be hedged
("reviews — DISPUTED", "guest checkout — pending sign-off") and routed to open-questions, while a
plainer mention elsewhere becomes a firm requirement. Because chunks are extracted independently,
both survive — and the SRS then asserts a firm "shall" for something the sources left open.

This pass reconciles the two lists. For every firm requirement that expresses the SAME obligation
as an item already flagged non-firm (out-of-scope / disputed / undecided / deferred, or rejected by
the critic) in open-questions, the CAUTIOUS status wins: the firm requirement is withdrawn from the
SRS body and its evidence is folded onto the matching open-question. Nothing is dropped — the
obligation still appears in Appendix C for a human to resolve.

Candidate pairs are found deterministically by statement similarity (cheap; decides nothing); the
LLM then confirms which are truly the same obligation (never asserting firmer than the most-cautious
mention). No provider / run_llm=False -> no-op, so tests and the mock path are unaffected.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..llm.base import LLMProvider
from ..models import Requirement
from .pipeline import _statement_similarity
from .scope_classifier import SCOPE_FLAGS

# open-question kinds that represent a NON-firm status: a firm requirement duplicating one of
# these is over-committed. (The scope-classifier flags + the critic's own rejection.)
CAUTIONARY_KINDS = frozenset({*SCOPE_FLAGS, "critic_rejected"})
CANDIDATE_THRESHOLD = 0.5   # loose lexical gate -> the LLM decides same-obligation
MAX_GROUP = 40              # cap firm statements per LLM call (prompt bound)

RECONCILE_SYSTEM = (
    "You reconcile a software requirements specification. You are given FIRM requirements (each "
    "stated as an obligation) and, separately, items the sources left NON-FIRM (out of scope, "
    "disputed, undecided, or deferred). Identify which FIRM requirements assert, as a firm "
    "obligation, the SAME underlying thing that appears in the NON-FIRM list. Those firm "
    "requirements are over-committed and must be withdrawn to the non-firm list. Match by MEANING, "
    "not wording. If a firm requirement has no non-firm counterpart, do NOT list it. When unsure, "
    "do NOT list it."
)


class WithdrawResult(BaseModel):
    withdraw: list[int] = Field(default_factory=list)  # 1-based indices into the firm list shown


def _llm_withdraw(
    provider: LLMProvider, firm_statements: list[str], cautionary_lines: list[str]
) -> list[int]:
    """Ask the model which firm statements duplicate a non-firm item. Returns 0-based indices."""
    firm_listing = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(firm_statements))
    caution_listing = "\n".join(f"- {c}" for c in cautionary_lines)
    user = (
        "NON-FIRM items (out of scope / disputed / undecided / deferred):\n"
        + caution_listing
        + "\n\nFIRM requirements to check:\n"
        + firm_listing
        + "\n\nReturn the numbers of the FIRM requirements that assert the same obligation as one "
        "of the NON-FIRM items above."
    )
    res = provider.structured(RECONCILE_SYSTEM, user, WithdrawResult)
    n = len(firm_statements)
    return sorted({x - 1 for x in res.withdraw if isinstance(x, int) and 1 <= x <= n})


def reconcile_scope(
    provider: LLMProvider | None,
    reqs: list[Requirement],
    open_q: list[dict],
    *,
    run_llm: bool = True,
    candidate_threshold: float = CANDIDATE_THRESHOLD,
) -> tuple[list[Requirement], list[dict], int]:
    """Withdraw firm requirements that duplicate a non-firm open-question.

    Returns (kept_requirements, open_q, withdrawn_count). `open_q` is updated in place: each matched
    open-question gains `merged_refs` (the withdrawn requirement's evidence) and `reconciled_from`
    (its id), so no evidence is lost.
    """
    if not run_llm or provider is None or not reqs or not open_q:
        return list(reqs), open_q, 0

    cautionary = [
        o for o in open_q
        if o.get("type") in CAUTIONARY_KINDS and (o.get("statement") or "").strip()
    ]
    if not cautionary:
        return list(reqs), open_q, 0

    # deterministic candidate gate: only check firm reqs that lexically resemble some cautionary item
    candidates: list[int] = []
    best_match: dict[int, dict] = {}
    for i, r in enumerate(reqs):
        best, best_sim = None, 0.0
        for o in cautionary:
            sim = _statement_similarity(r.statement, o["statement"])
            if sim > best_sim:
                best, best_sim = o, sim
        if best is not None and best_sim >= candidate_threshold:
            candidates.append(i)
            best_match[i] = best
    if not candidates:
        return list(reqs), open_q, 0

    withdrawn: set[int] = set()
    for start in range(0, len(candidates), MAX_GROUP):
        window = candidates[start:start + MAX_GROUP]
        firm_statements = [reqs[i].statement for i in window]
        # show only the specific cautionary items these firm reqs resemble (focused, smaller prompt)
        caution_lines: list[str] = []
        for i in window:
            o = best_match[i]
            line = f'[{o["type"]}] {o["statement"]}'
            if line not in caution_lines:
                caution_lines.append(line)
        for local in _llm_withdraw(provider, firm_statements, caution_lines):
            withdrawn.add(window[local])

    # fold withdrawn requirements' evidence onto their matching open-question; drop from firm list
    for gi in withdrawn:
        o = best_match.get(gi)
        if o is None:
            continue
        r = reqs[gi]
        refs = o.setdefault("merged_refs", [])
        refs.extend(
            {"doc_id": sr.doc_id, "location": sr.location, "raw_quote": sr.raw_quote,
             "start": sr.start, "end": sr.end}
            for sr in r.source_refs
        )
        o.setdefault("reconciled_from", []).append(r.id)

    kept = [r for i, r in enumerate(reqs) if i not in withdrawn]
    return kept, open_q, len(withdrawn)
