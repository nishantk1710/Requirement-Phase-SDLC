"""Deterministic scope / status classifier.

Extraction can state a requirement firmly even when its own source text marks the item as
OUT OF SCOPE, UNDECIDED, DISPUTED, or DEFERRED — the hedge is dropped when the sentence is
cleaned into a "shall". This module scans the requirement's EVIDENCE (the verbatim source
quotes it is grounded in) plus its section location for explicit status markers, and returns a
status flag when one is present. A flagged item is not a firm obligation, so the pipeline routes
it to open-questions (tentative) instead of asserting it in the SRS body.

Deterministic and conservative: only unambiguous, multi-word status markers trigger, so an
incidental "may"/"might" in an ordinary sentence does not. It scans the specific evidence span
the requirement is built from, not the whole chunk, so a marker about a neighbouring clause does
not bleed in. Because a flagged item is surfaced (never deleted), a rare false flag is safe — it
moves a real requirement to Appendix C for a human, which is the safe direction.

NOTE: markers that live only in a *document heading* separated from the item (e.g. a Word
"Broadly out (for now)" heading whose bullets are flattened away by the .docx loader) are not
visible here — that case is handled by structure-preserving ingestion (a separate change) and by
the cross-chunk reconciliation pass.
"""

from __future__ import annotations

import re

# Ordered by strength: the first family that matches wins (most-decisive scope signal first).
_MARKER_FAMILIES: list[tuple[str, str, tuple[str, ...]]] = [
    ("out_of_scope", "source marks this out of scope", (
        r"out[\s-]of[\s-]scope",
        r"broadly out",
        r"\bnot in scope\b",
        r"\bnot in v1\b",
        r"won'?t (?:make|be in) v1",
    )),
    ("disputed", "source marks this disputed / not confirmed in scope", (
        r"\bdisputed\b",
        r"not confirmed in scope",
        r"don'?t assume (?:it'?s )?(?:in or out|either way)",
    )),
    ("undecided", "source leaves this undecided / decision pending", (
        r"decision pending",
        r"\bnot settled\b",
        r"\bundecided\b",
        r"to be decided",
        r"yet to be decided",
        r"pending sign[\s-]?off",
        r"needs .{0,20}?sign[\s-]?off",
        r"\bTBD\b",
        r"open ?/ ?to[\s-]?confirm",           # a specific status label (kept)
        r"may or may not be in scope",
        r"not sure it makes the cut",
        r"might be a stretch",
        r"maybe[\s-]?not[\s-]?v1",
        # Fix 2 — deliberation markers: unambiguous "we are still deciding" phrasings. These gate
        # hedged source text to an open-question at the SOURCE, before it is firmed into a "shall".
        # Kept HIGH-PRECISION on purpose: bare "maybe" / a trailing "?" are NOT matched — they fire
        # on ordinary product text and would both hurt recall and inflate Appendix C.
        r"leaning (?:towards?|to)\b",
        r"still (?:deciding|debating|discussing|being decided|to be decided)\b",
        r"(?:have|has|had)(?:n'?t| not) (?:yet )?(?:been )?decided\b",
        r"\bnot yet decided\b",
        r"should be decided\b",
        r"yet to be (?:decided|determined|confirmed|agreed|finalis)",
        # NOTE: bare "open item" removed — it matches ordinary product text
        # ("show each open item in the queue"); the labels above are unambiguous.
    )),
    ("deferred", "source defers this to a later phase", (
        r"\bdeferred\b",
        r"defer(?:red|s)?\s+(?:to|until)\b",
        r"(?:to|in|until|for)\s+(?:a\s+)?(?:likely\s+)?phase[\s-]?2\b",   # "deferred/for/in phase 2"
        r"phase[\s-]?2\s+(?:release|onwards?|feature|scope)",
        r"\blater phase\b",
        r"fast[\s-]follow",
        # NOTE: bare "phase 2" and "could have" removed — they match feature names ("phase 2
        # onboarding") and MoSCoW/idiom ("users could have multiple addresses"). Deferral now
        # requires explicit context; MoSCoW "could" is handled by prioritisation, not scope.
    )),
]

_COMPILED = [
    (flag, reason, tuple(re.compile(p, re.IGNORECASE) for p in pats))
    for flag, reason, pats in _MARKER_FAMILIES
]

# The status flags this module can return — the pipeline treats these open-question kinds as
# "non-firm" and the reconciliation pass uses the same set.
SCOPE_FLAGS = frozenset(flag for flag, _reason, _pats in _MARKER_FAMILIES)


def classify_scope_status(
    evidence_texts: list[str] | None, location: str = ""
) -> tuple[str | None, str | None]:
    """Return (flag, reason) if the evidence/section marks the item as non-firm, else (None, None).

    `flag` is one of: out_of_scope | disputed | undecided | deferred.
    """
    haystack = " \n ".join([*(evidence_texts or []), location or ""])
    for flag, reason, patterns in _COMPILED:
        for pat in patterns:
            m = pat.search(haystack)
            if m:
                return flag, f'{reason} ("{m.group(0).strip()}")'
    return None, None
