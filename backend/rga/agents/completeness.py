"""Completeness / gap analysis — detect requirements that are probably MISSING.

Extraction captures what the sources SAY; a good analyst also spots what they OMIT. This agent flags
likely gaps so they surface for a human instead of silently shipping an incomplete spec:

  1. Cross-cutting coverage (deterministic): every system is normally expected to say something about
     security, performance, reliability, usability/accessibility, error handling, authentication,
     audit/logging and data privacy. An aspect with NO requirement at all is flagged — explainable
     and domain-agnostic.
  2. Analyst review (LLM, optional): given what's captured, name concrete requirements that appear
     missing or thin. Suggestions only — clearly labelled, routed to open-questions, never asserted
     as requirements.

Gaps are SUGGESTIONS routed to Appendix C (kind "gap"); nothing is added to the SRS automatically.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..llm.base import LLMProvider
from ..models import Requirement

# (aspect name, hint, detector regex over statement + nfr_category)
_ASPECTS: list[tuple[str, str, str]] = [
    ("Security", "authn/z, encryption, threat mitigation",
     r"secur|encrypt|authenticat|authoris|authoriz|vulnerab|threat|xss|csrf|injection|rbac|access control|\bbots?\b"),
    ("Performance", "response time, throughput, load",
     r"performanc|latency|response time|throughput|\bscal\w*|\bload\w*|concurren|p9\d|\bseconds?\b|sub[-\s]?second"),
    ("Reliability / availability", "uptime, failover, backup, recovery",
     r"availab|uptime|failover|recover|backup|resilien|fault|degrad"),
    ("Usability / accessibility", "accessibility, responsiveness, ease of use",
     r"usab|accessib|wcag|screen reader|keyboard|responsive|mobile|intuitive"),
    ("Error handling", "failure, invalid input, retries, fallbacks",
     r"error|fail|invalid|exception|retr(y|ies)|fallback|timeout|edge case|graceful"),
    ("Authentication / access control", "login, sessions, roles, permissions",
     r"log ?in|sign[- ]?in|password|otp|session|\brole\b|permission|authenticat|authoris|authoriz|account"),
    ("Audit / logging / observability", "audit trail, logs, monitoring",
     r"audit|logging|\blog\b|trace|monitor|observ|metric"),
    ("Data privacy / retention", "consent, retention, erasure, PII",
     r"privac|gdpr|dpdp|consent|retention|erasure|personal data|\bpii\b"),
]


def coverage_gaps(reqs: list[Requirement]) -> list[dict]:
    """Deterministic cross-cutting coverage check. Flags aspects with zero requirements."""
    hay = " \n ".join(
        f"{r.statement} {r.nfr_category or ''}" for r in reqs
    ).lower()
    gaps: list[dict] = []
    for name, hint, pattern in _ASPECTS:
        if not re.search(pattern, hay, re.IGNORECASE):
            gaps.append({
                "type": "gap",
                "statement": f"No requirements address {name}.",
                "reason": f"commonly-expected area ({hint}) — confirm whether it is in scope",
                "location": "", "doc_id": "",
            })
    return gaps


# --- LLM analyst review ------------------------------------------------------
GAP_SYSTEM = (
    "You are a senior business analyst reviewing a requirements set for COMPLETENESS. Given the "
    "requirements already captured, name specific, commonly-expected requirements that appear to be "
    "MISSING or thin for this kind of system. Be concrete and grounded in what is present; do NOT "
    "restate existing requirements, and do NOT invent domain features the material does not imply. "
    "Prefer cross-cutting omissions (error paths, edge cases, permissions, limits, data lifecycle). "
    "Return at most 8 short, specific missing-requirement suggestions."
)


class GapList(BaseModel):
    gaps: list[str] = Field(default_factory=list)


def _context(reqs: list[Requirement], *, sample: int = 60) -> str:
    from collections import Counter

    feats = Counter(r.feature for r in reqs if r.feature)
    types = Counter(r.rtype.value for r in reqs)
    lines = [
        f"Total requirements: {len(reqs)}",
        "By type: " + ", ".join(f"{k}={v}" for k, v in types.items()),
        "Features: " + ", ".join(f for f, _ in feats.most_common(25)),
        "",
        "Sample of captured requirements:",
    ]
    lines += [f"- {r.statement}" for r in reqs[:sample]]
    return "\n".join(lines)


def llm_gaps(provider: LLMProvider, reqs: list[Requirement]) -> list[dict]:
    user = _context(reqs) + "\n\nList the missing / thin requirements you would expect for this system."
    try:
        result = provider.structured(GAP_SYSTEM, user, GapList, max_tokens=1500)
    except Exception:
        return []  # completeness is advisory; never fail the run over it
    out: list[dict] = []
    for g in result.gaps:
        text = (g or "").strip()
        if text:
            out.append({
                "type": "gap",
                "statement": text,
                "reason": "AI-suggested missing requirement — verify and add if in scope",
                "location": "", "doc_id": "",
            })
    return out


def analyze_completeness(
    provider: LLMProvider | None, reqs: list[Requirement], *, run_llm: bool = True
) -> list[dict]:
    """Return a list of gap open-questions (deterministic coverage + optional LLM analyst review)."""
    gaps = coverage_gaps(reqs)
    if run_llm and provider is not None and reqs:
        gaps.extend(llm_gaps(provider, reqs))
    # de-dupe by normalised statement
    seen: set[str] = set()
    unique: list[dict] = []
    for g in gaps:
        key = re.sub(r"\s+", " ", g["statement"].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(g)
    return unique
