"""Conflict detection — flags pairs of requirements that directly contradict each other.

Real, messy inputs contain genuine contradictions (a 5-step checkout in one doc vs a 3-step in
another; "delete the data" vs "retain the data"). Extraction faithfully captures BOTH sides, so
the contradiction must be SURFACED for a human to resolve — never silently dropped or auto-picked.

Approach mirrors the semantic-dedup agent: group requirements into tight topic clusters by
similarity (cheap, deterministic), then ask the LLM to name only the genuinely mutually-exclusive
pairs within each cluster. Output is a list of conflict pairs → routed to the open-questions /
Appendix C list. Nothing is removed.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..llm.base import LLMProvider
from ..models import Requirement
from .dedup_semantic import _components

CONFLICT_THRESHOLD = 0.45  # tight topic clusters (conflicts are usually about the same thing)
MAX_GROUP = 40

# Fix 3 — modality/wording-variant guard. Two statements that are the SAME obligation stated at a
# different STRENGTH ("the cart shall persist" vs "the cart may persist") are not a contradiction —
# consolidation keeps the stronger one; the conflict detector must never fire on such a pair. We
# strip the modal verbs and compare: only a near-identical remainder (and not a true polarity flip)
# is treated as a variant. A genuine value clash ("5 steps" vs "3 steps", "delete" vs "retain")
# survives, because its remainder is NOT near-identical / IS a polarity conflict.
_MODAL_RE = re.compile(r"\b(?:shall|should|must|may|will|can|could|would|might|to)\b", re.IGNORECASE)


def _modality_or_wording_variant(a: str, b: str) -> bool:
    from .pipeline import _polarity_conflict, _statement_similarity

    if _polarity_conflict(a, b):
        return False  # an opposite (store vs NOT store, low vs high) is a real conflict candidate
    stripped_a = _MODAL_RE.sub(" ", a)
    stripped_b = _MODAL_RE.sub(" ", b)
    return _statement_similarity(stripped_a, stripped_b) >= 0.9


def _one_line(reason: str) -> str:
    """Keep only a short, single-line reason — never let the model's multi-line scratch reasoning
    become an Appendix-C description (the decision is carried by `is_conflict`, not the prose)."""
    first = (reason or "").strip().splitlines()
    return (first[0].strip()[:200]) if first else ""

CONFLICT_SYSTEM = (
    "You find CONTRADICTIONS between software requirements. Two requirements conflict only when "
    "they cannot both hold at the same time — e.g. one mandates a 5-step checkout while another "
    "mandates 3 steps; one says data must be deleted while another says it must be retained; one "
    "includes a capability while another excludes it. Merely sharing a topic, overlapping, or being "
    "redundant is NOT a conflict. For EACH pair you output, set `is_conflict` to true only if they "
    "are genuinely mutually exclusive, or false if on reflection they can coexist. The `reason` is a "
    "short explanation and may use any wording — the decision is carried by `is_conflict`, not the "
    "prose."
)


class ConflictPair(BaseModel):
    a: int  # 1-based item number
    b: int
    is_conflict: bool = True  # the model's explicit verdict (decision lives here, not in `reason`)
    reason: str = ""


class ConflictResult(BaseModel):
    pairs: list[ConflictPair] = Field(default_factory=list)


def _llm_pairs(provider: LLMProvider, statements: list[str]) -> list[tuple[int, int, str]]:
    listing = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(statements))
    user = (
        "Requirements:\n" + listing + "\n\nReturn the pairs of item numbers that DIRECTLY "
        "CONTRADICT each other (cannot both be satisfied), each with a one-line reason. "
        "If none conflict, return an empty list."
    )
    res = provider.structured(CONFLICT_SYSTEM, user, ConflictResult)
    n = len(statements)
    out: list[tuple[int, int, str]] = []
    for p in res.pairs:
        if not p.is_conflict:  # the model's explicit verdict that this pair is NOT a real conflict
            continue
        if isinstance(p.a, int) and isinstance(p.b, int) and 1 <= p.a <= n and 1 <= p.b <= n and p.a != p.b:
            if _modality_or_wording_variant(statements[p.a - 1], statements[p.b - 1]):
                continue  # same obligation at a different strength — not a contradiction (Fix 3)
            out.append((p.a - 1, p.b - 1, _one_line(p.reason)))
    return out


def detect_conflicts(
    provider: LLMProvider | None, reqs: list[Requirement], *, run_llm: bool = True,
    threshold: float = CONFLICT_THRESHOLD,
) -> list[dict]:
    """Return a list of contradiction records: {a_id, b_id, a, b, reason}. Empty if none / no LLM."""
    if not run_llm or provider is None or len(reqs) < 2:
        return []
    conflicts: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for comp in _components(list(range(len(reqs))), reqs, threshold):
        if len(comp) < 2:
            continue
        for start in range(0, len(comp), MAX_GROUP):
            window = comp[start:start + MAX_GROUP]
            if len(window) < 2:
                continue
            for a, b, reason in _llm_pairs(provider, [reqs[i].statement for i in window]):
                ia, ib = window[a], window[b]
                key = tuple(sorted((reqs[ia].id, reqs[ib].id)))
                if key in seen:
                    continue
                seen.add(key)
                conflicts.append({
                    "a_id": reqs[ia].id, "b_id": reqs[ib].id,
                    "a": reqs[ia].statement, "b": reqs[ib].statement, "reason": reason,
                })
    return conflicts
