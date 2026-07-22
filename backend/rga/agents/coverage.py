"""Coverage accounting — the recall floor ("nothing real left off the table").

Every source chunk must be ACCOUNTED FOR. A chunk is accounted for if it:
  (a) contributed evidence to a requirement, OR
  (b) was captured as an open-question / exclusion (same doc + section), OR
  (c) is a legitimately non-requirement chunk — a heading, greeting, speaker cue, or too-short/meta
      fragment that could not carry a requirement.

A chunk that is none of these — substantive text that produced nothing — is a POSSIBLE MISS and is
surfaced (kind 'possible_miss', tier A) so a genuine requirement can never be silently dropped. This
turns "did we miss anything?" from a hope into a checked invariant, catching misses DURING the run
instead of as rework later.
"""

from __future__ import annotations

import re

# Section headings that never carry product requirements (stakeholders, front-matter, glossary…).
_NONREQ_SECTION = re.compile(
    r"(stakeholder|who cares|revision history|table of contents|how to use|glossary|"
    r"document control|version history|sign[- ]?off|distribution list|contact list)"
    r"|\b(attendees|present|agenda)\b",  # meeting front-matter (word-bounded: not 'representation')
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Za-z0-9']+")
# The primary "could this plausibly be a requirement?" signal: an obligation or capability cue.
# A chunk with NONE of these — a roster ("Present: Karan (Product), …"), a version/attribution line
# ("Version 0.3 — DRAFT — 17 Feb"), a heading — was correctly NOT extracted, so it is an expected
# non-requirement, not a possible miss. A chunk that DOES carry a cue but produced no requirement is
# a genuine possible miss, regardless of length (so a short "System shall support SSO." still counts).
_OBLIGATION = re.compile(
    r"\b(shall|must|should|will|would|may|can|need|needs|needed|require[sd]?|support[sed]*|"
    r"enable[sd]?|allow[sed]*|provide[sd]?|ensure[sd]?|want|wants|wish|wishes|able to|ability to|"
    r"has to|have to|expected to|responsible for|so that)\b",
    re.IGNORECASE,
)


def _is_nonrequirement_chunk(text: str, source_type: str, location: str = "") -> bool:
    """A chunk that cannot carry a product requirement — so extraction producing nothing from it is
    expected, not a miss. Recall-safe: any chunk with an obligation/capability cue is treated as a
    plausible requirement (never dismissed by length or speaker formatting)."""
    t = (text or "").strip()
    if not t or t.startswith("#"):
        return True  # heading / empty
    if _NONREQ_SECTION.search(location or ""):
        return True  # front-matter / stakeholder / roster section — not a requirement
    if not _OBLIGATION.search(t):
        return True  # no obligation/capability cue anywhere — roster, version line, attribution, note
    return False


def coverage_report(chunks: list, reqs: list, open_q: list[dict] | None = None) -> dict:
    """Return a coverage report. `chunks`/`reqs` are model objects; `open_q` is the raw list.

    Keys: total, covered_by_req, covered_by_openq, expected_nonrequirement, possible_misses (list),
    accounted, accounted_pct, req_coverage_pct.
    """
    open_q = open_q or []
    # evidence spans per doc from requirements
    spans_by_doc: dict[str, list[tuple[int, int]]] = {}
    for r in reqs:
        for sr in getattr(r, "source_refs", []) or []:
            if sr.start is None or sr.end is None:
                continue
            spans_by_doc.setdefault(sr.doc_id, []).append((sr.start, sr.end))
    # (doc_id, location) pairs that produced an open-question
    openq_locs = {(o.get("doc_id", ""), o.get("location", "")) for o in open_q}

    covered_req = covered_openq = expected_nonreq = 0
    possible_misses: list[dict] = []
    for c in chunks:
        spans = spans_by_doc.get(c.doc_id, [])
        if any(c.start <= s and e <= c.end for (s, e) in spans):
            covered_req += 1
        elif (c.doc_id, c.location) in openq_locs:
            covered_openq += 1
        elif _is_nonrequirement_chunk(c.text, c.source_type, c.location):
            expected_nonreq += 1
        else:
            possible_misses.append({
                "doc_id": c.doc_id, "location": c.location,
                "preview": " ".join(_WORD.findall(c.text))[:160],
                "type": "possible_miss",
                "statement": c.text.strip()[:200],
                "reason": "substantive source text that produced no requirement — verify nothing was missed",
            })
    total = len(chunks)
    accounted = covered_req + covered_openq + expected_nonreq
    return {
        "total": total,
        "covered_by_req": covered_req,
        "covered_by_openq": covered_openq,
        "expected_nonrequirement": expected_nonreq,
        "possible_misses": possible_misses,
        "accounted": accounted,
        "accounted_pct": round(100 * accounted / total, 1) if total else 100.0,
        "req_coverage_pct": round(100 * covered_req / total, 1) if total else 0.0,
    }
