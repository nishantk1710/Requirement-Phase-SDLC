"""Mechanical normalization — auto-fix cosmetic/structural quality flags BEFORE the human gate.

A requirement should never reach a human just because it had a passive-voice or not-EARS-conformant
flag — those are deterministic, meaning-preserving fixes. This pass:
  * tidies cosmetic issues (whitespace, terminal punctuation);
  * adopts the A3 EARS-style `suggested_rewrite` ONLY when it is meaning-preserving (high similarity
    AND the same modal verb, so priority/obligation is unchanged);
  * clears MECHANICAL ambiguity flags (passive voice, EARS structure, phrasing) so they no longer
    escalate the item;
  * keeps SUBSTANTIVE flags (genuinely vague terms like "fast", "as required") — those still surface.

So only items whose meaning is actually unclear reach the reviewer. Deterministic and conservative.
"""

from __future__ import annotations

import re

from .pipeline import _polarity_conflict, _statement_similarity

# Flags that are fixable without changing meaning -> cleared (do not escalate).
_MECHANICAL = re.compile(
    r"(passive voice|not[-\s]?ears|ears[-\s]?conform|structure|phrasing|tense|"
    r"terminal punctuation|missing period|formatting|capitalis|capitaliz)",
    re.IGNORECASE,
)
_MODALS = ("shall", "must", "should", "may", "will")


def _is_mechanical(flag: str) -> bool:
    return bool(_MECHANICAL.search(flag or ""))


def _modal(s: str) -> str:
    low = (s or "").lower()
    for m in _MODALS:
        if re.search(rf"\b{m}\b", low):
            return m
    return ""


def auto_normalize(req) -> dict:
    """Apply mechanical fixes to `req` in place. Returns a small report of what changed.
    After this, `req.quality.ambiguity_flags` contains only SUBSTANTIVE flags."""
    changed: dict = {}
    original = req.statement

    # 1) cosmetic: collapse whitespace, ensure terminal punctuation
    s = re.sub(r"\s+", " ", req.statement).strip()
    if s and s[-1] not in ".!?":
        s += "."
    if s != req.statement:
        req.statement = s
        changed["cosmetic"] = True

    q = getattr(req, "quality", None)
    if q is None:
        return changed

    # 2) adopt an EARS rewrite ONLY if it is meaning-preserving (close + same modal + same polarity,
    #    so a rewrite that drops a negation or flips a direction is never silently adopted)
    rewrite = (getattr(q, "suggested_rewrite", None) or "").strip()
    if (
        rewrite
        and _statement_similarity(rewrite, req.statement) >= 0.8
        and _modal(rewrite) == _modal(req.statement)
        and not _polarity_conflict(rewrite, req.statement)
    ):
        req.statement = rewrite
        q.suggested_rewrite = None
        changed["rewrite_adopted"] = True

    # 3) clear mechanical flags; keep substantive ones
    flags = list(getattr(q, "ambiguity_flags", []) or [])
    substantive = [f for f in flags if not _is_mechanical(f)]
    if len(substantive) != len(flags):
        q.ambiguity_flags = substantive
        changed["mechanical_flags_cleared"] = len(flags) - len(substantive)

    if req.statement != original:
        changed["statement_changed"] = True
    return changed
