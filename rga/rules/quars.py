"""QuARS-style ambiguity detection + EARS conformance (deterministic).

QuARS = Quality Analyzer for Requirement Specifications: flag vague/weak wording via a
lexicon. EARS = Easy Approach to Requirements Syntax: check the sentence is a well-formed
"[optional preamble] the <system> shall <response>" requirement. High recall, explainable,
and reproducible — the LLM (A3) then explains each flag and proposes a rewrite.
"""

from __future__ import annotations

import re

# Weak single words (matched on word boundaries, case-insensitive).
WEAK_WORDS = [
    "quick", "quickly", "fast", "slow", "efficient", "efficiently", "flexible",
    "flexibly", "easy", "easily", "intuitive", "seamless", "seamlessly", "robust",
    "scalable", "approximately", "roughly", "several", "many", "minimal", "optimal",
    "optimize", "optimise", "reasonable", "reasonably", "adequate", "sufficient",
    "appropriate", "modern", "better", "improved", "friendly",
]

# Weak multi-word phrases (substring match, case-insensitive).
WEAK_PHRASES = [
    "user-friendly", "user friendly", "as appropriate", "as needed", "as required",
    "if possible", "where possible", "and so on", "and/or", "to be determined",
    "without training", "state of the art", "state-of-the-art", "etc.", "etc ",
    "flexible enough",
]

_WORD_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in WEAK_WORDS) + r")\b", re.IGNORECASE)


def find_weak_terms(text: str) -> list[str]:
    """Return the sorted, unique weak words/phrases present in `text`."""
    found: set[str] = {m.group(0).lower() for m in _WORD_RE.finditer(text)}
    low = text.lower()
    for phrase in WEAK_PHRASES:
        if phrase in low:
            found.add(phrase.strip())
    return sorted(found)


def ears_conformance(text: str) -> tuple[bool, str | None]:
    """Is the requirement a well-formed EARS statement? Returns (conformant, pattern).

    Requires a mandatory 'shall'. Recognises the EARS patterns by their preamble;
    a plain '<subject> shall <response>' is the ubiquitous pattern."""
    t = text.strip()
    # EARS is written with 'shall', but 'must'/'will' are equally mandatory/declarative in real
    # requirements and must not be penalised as non-conformant (L4).
    m = re.search(r"\b(?:shall|must|will)\b(.*)$", t, re.IGNORECASE | re.DOTALL)
    if not m:
        return (False, None)  # no mandatory obligation ('shall'/'must'/'will')
    if len(m.group(1).strip(" .\t\n").split()) < 2:
        return (False, "ubiquitous")  # 'shall' with no real response clause
    if re.match(r"^\s*when\b", t, re.IGNORECASE):
        return (True, "event-driven")
    if re.match(r"^\s*while\b", t, re.IGNORECASE):
        return (True, "state-driven")
    if re.match(r"^\s*where\b", t, re.IGNORECASE):
        return (True, "optional-feature")
    if re.match(r"^\s*if\b", t, re.IGNORECASE) and re.search(r"\bthen\b", t, re.IGNORECASE):
        return (True, "unwanted-behavior")
    return (True, "ubiquitous")


def quality_score(weak_terms: list[str], ears_ok: bool) -> float:
    """A simple 0-1 quality score: penalise weak wording and non-EARS structure."""
    score = 1.0
    if weak_terms:
        score -= 0.4
    if not ears_ok:
        score -= 0.4
    return round(max(0.0, min(1.0, score)), 2)


def flags_for(text: str) -> list[str]:
    """All deterministic quality flags for a requirement statement."""
    flags = [f"weak-word:{w}" for w in find_weak_terms(text)]
    ears_ok, _ = ears_conformance(text)
    if not ears_ok:
        flags.append("not-EARS-conformant")
    return flags
