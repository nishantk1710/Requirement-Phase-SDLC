"""A0 — Critic / Verifier. Adversarially checks that an extracted requirement follows
ONLY from its cited quotes / the source chunk. This is the main anti-hallucination guard;
it also surfaces requirements the extractor may have missed (fed back into the loop)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..llm.base import LLMProvider
from .schemas import CriticVerdict


class ItemVerdict(BaseModel):
    index: int
    grounded: bool
    invented: bool = False
    is_requirement: bool = True  # False = document meta / heading / disclaimer / vague narrative
    reason: str = ""


class BatchCriticResult(BaseModel):
    verdicts: list[ItemVerdict] = Field(default_factory=list)
    possibly_missed: list[str] = Field(default_factory=list)

CRITIC_SYSTEM = (
    "You are an adversarial requirements verifier. Your job is to catch requirements that "
    "are NOT supported by the source, and to notice requirements that were missed. Be "
    "strict: if a requirement asserts anything beyond what the source states or clearly "
    "implies, mark it not grounded."
)


def verify(
    provider: LLMProvider, statement: str, quotes: list[str], chunk_text: str
) -> CriticVerdict:
    quote_block = "\n".join(f"  - {q}" for q in quotes) or "  (none)"
    user = (
        f"SOURCE TEXT:\n\"\"\"\n{chunk_text}\n\"\"\"\n\n"
        f"CANDIDATE REQUIREMENT:\n  {statement}\n\n"
        f"CITED QUOTES:\n{quote_block}\n\n"
        "Answer strictly:\n"
        "  - grounded: true only if the requirement follows from the cited quotes / source.\n"
        "  - invented: true if it asserts something the source does not support.\n"
        "  - possibly_missed: list any clear requirements in the SOURCE TEXT that a first "
        "pass might have missed (empty if none).\n"
        "  - reason: one short sentence."
    )
    return provider.structured(CRITIC_SYSTEM, user, CriticVerdict)


def verify_batch(
    provider: LLMProvider, candidates: list[tuple[str, list[str]]], chunk_text: str
) -> BatchCriticResult:
    """Verify ALL of a chunk's candidate requirements in ONE call (cheaper than per-item).
    Returns a verdict per index + any requirements the extractor may have missed."""
    listing = "\n".join(
        f"[{i}] {stmt}\n    cited: {quotes}" for i, (stmt, quotes) in enumerate(candidates)
    )
    user = (
        f'SOURCE TEXT:\n"""\n{chunk_text}\n"""\n\n'
        f"CANDIDATE REQUIREMENTS:\n{listing}\n\n"
        "For EACH candidate (by its index) decide:\n"
        "  - grounded: true only if it follows from the source. Mark it NOT grounded (invented=true) if "
        "it INVERTS scope (the source marks the item out of scope / excluded / deferred / 'broadly out' "
        "but the candidate states it as a 'must/shall support'), if it firms up something the source "
        "marks as undecided/optional/'maybe' into a mandatory obligation, if it DROPS a stated "
        "alternative (source says 'percentage or flat'; candidate keeps only one), or if it misreads an "
        "internal reference/ticket key (e.g. 'A3', 'HRZN-12') as a product or feature;\n"
        "  - invented: true if it asserts something the source does not support;\n"
        "  - is_requirement: true only if it is a GENUINE, testable obligation of the product, system, or "
        "business/process — false if it is document meta ('this document…'), a heading/title, a "
        "disclaimer, a scope note, a business goal / success metric / KPI / pain point, a casual aside or "
        "opinion, a form question, or a directive to 'capture requirements';\n"
        "  - reason: one short sentence.\n"
        "Also list in possibly_missed any clearly-stated requirement in the SOURCE TEXT "
        "that is NOT among the candidates."
    )
    return provider.structured(CRITIC_SYSTEM, user, BatchCriticResult)
