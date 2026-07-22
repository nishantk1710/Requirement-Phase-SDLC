"""Risk-based review triage.

A reviewer should not read every requirement with equal attention. This scores each requirement by
how much human judgement it needs, so the review UI can surface the few that matter and safely
batch-accept the rest. Deterministic and explainable — it ORDERS and RECOMMENDS; the human decides.

Attention rises with: inferred/tentative status, low extractor confidence, ambiguity/clarity flags,
non-testable phrasing, conflict membership, coarse (whole-document) traceability, and 'could'
priority. A clear, testable, high-confidence, precisely-located requirement is 'routine'.
"""

from __future__ import annotations

_COARSE_LOCATIONS = {"", "(top)", "(para)", "(heading)", "(intro)"}

ATTENTION = "attention"   # a human must decide — never batch-accept
REVIEW = "review"         # worth a glance
ROUTINE = "routine"       # clear + grounded + high-confidence — safe to batch-accept

_ATTENTION_AT = 3         # score >= this -> attention
_REVIEW_AT = 1            # score >= this -> review


def triage(r) -> dict:
    """Return {'level', 'score', 'reasons'} for a requirement. Higher score = more human attention."""
    score = 0
    reasons: list[str] = []

    if getattr(r, "inferred", False):
        score += 3
        reasons.append("inferred / tentative — never auto-accept")

    conf = float(getattr(r, "confidence", 0.0) or 0.0)
    if 0 < conf < 0.5:
        score += 2
        reasons.append(f"low extractor confidence ({conf:.2f})")
    elif 0.5 <= conf < 0.75:
        score += 1
        reasons.append(f"moderate confidence ({conf:.2f})")

    if getattr(r, "conflicts_with", None):
        score += 3
        reasons.append("part of a flagged conflict")

    quality = getattr(r, "quality", None)
    flags = list(getattr(quality, "ambiguity_flags", []) or []) if quality is not None else []
    if flags:
        score += 2
        reasons.append("ambiguity: " + ", ".join(flags[:3]))
    if quality is not None and getattr(quality, "testable", None) is False:
        score += 2
        reasons.append("not testable as written")

    rt = getattr(getattr(r, "rtype", None), "value", str(getattr(r, "rtype", "")))
    if rt in ("constraint", "assumption"):
        score += 1
        reasons.append(f"{rt} — confirm with the owner")

    prio = getattr(getattr(r, "priority", None), "value", None)
    if prio == "could":
        score += 1
        reasons.append("low priority (could)")

    refs = list(getattr(r, "source_refs", []) or [])
    if refs and all((getattr(s, "location", "") or "") in _COARSE_LOCATIONS for s in refs):
        score += 1
        reasons.append("coarse traceability (no precise section)")

    level = ATTENTION if score >= _ATTENTION_AT else (REVIEW if score >= _REVIEW_AT else ROUTINE)
    return {"level": level, "score": score, "reasons": reasons}


def needs_attention(r) -> bool:
    """True when a requirement is NOT safe to batch-accept (triage level != routine)."""
    return triage(r)["level"] != ROUTINE


def triage_summary(reqs) -> dict:
    """Counts by level across a set of requirements — powers the review dashboard."""
    out = {ATTENTION: 0, REVIEW: 0, ROUTINE: 0}
    for r in reqs:
        out[triage(r)["level"]] += 1
    return out
