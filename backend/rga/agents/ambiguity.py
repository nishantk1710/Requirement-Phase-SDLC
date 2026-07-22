"""A3 — Ambiguity agent (the hybrid pattern).

Deterministic first: the QuARS lexicon + EARS conformance produce explainable flags and
a quality score with NO LLM. Then, only when something is flagged, the LLM explains the
flag in plain language and proposes an EARS-style rewrite. Rules give high recall;
the LLM adds the explanation/rewrite humans actually use.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..llm.base import LLMProvider
from ..models import Quality
from ..rules import quars

AMBIGUITY_SYSTEM = (
    "You improve software requirement quality. Given a weak/ambiguous requirement and the "
    "detected issues, you explain plainly why it is not testable and rewrite it as a single, "
    "measurable requirement in EARS style (e.g. 'The system shall ... <measurable criterion>')."
)


class AmbiguityExplanation(BaseModel):
    explanation: str
    rewrite: str
    testable: bool


def flag_requirement(
    provider: LLMProvider, statement: str, *, run_llm: bool = True
) -> Quality:
    """Return a Quality assessment. The deterministic flags/score are always computed;
    the LLM explanation + rewrite are added only when there is something to fix."""
    weak = quars.find_weak_terms(statement)
    ears_ok, _pattern = quars.ears_conformance(statement)
    flags = quars.flags_for(statement)
    q = Quality(
        ambiguity_flags=flags,
        testable=(ears_ok and not weak),
        score=quars.quality_score(weak, ears_ok),
    )
    if flags and run_llm:
        user = (
            f"Requirement: {statement}\n"
            f"Detected issues: {flags}\n"
            "Explain why it is ambiguous or not testable, and rewrite it as one measurable "
            "EARS-style requirement."
        )
        exp = provider.structured(AMBIGUITY_SYSTEM, user, AmbiguityExplanation)
        q.ambiguity_explanation = exp.explanation
        q.suggested_rewrite = exp.rewrite
        q.testable = exp.testable
    return q
