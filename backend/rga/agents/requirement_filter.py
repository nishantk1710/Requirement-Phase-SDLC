"""Deterministic 'is this actually a requirement?' guard.

Real documents phrase a lot of NON-requirements with "must/should/shall": statements about
the document itself ("this document must be read in conjunction with…"), disclaimers,
scope notes, and preambles. Those are not obligations on the product/system/business, so
they should not become requirements. This is a conservative, explainable, zero-cost filter
that rejects only CLEAR non-requirements (document-meta subjects + a few unmistakable
disclaimer phrases). Anything borderline is left in — the LLM critic (A0) and the human
review gate handle the subtler cases. High precision, deliberately low aggression, so we
never drop a genuine requirement.
"""

from __future__ import annotations

import re

# The statement's subject is the DOCUMENT/artefact itself, not the product → not a requirement.
# Only strongly-document nouns are listed: ambiguous ones that are commonly real product subjects
# (table, figure, list, report, draft, paragraph, glossary, note) were REMOVED — "The report shall
# export to CSV" / "The table shall be sortable" are genuine requirements (B-M3).
_META_SUBJECT = re.compile(
    r"^\s*(this|the)\s+"
    r"(document|section|subsection|specification|spec|brd|srs|memo|addendum|appendix)\b",
    re.IGNORECASE,
)

# A concrete PRODUCT action. If a statement performs one of these, it is a real requirement even
# when its subject noun looks document-ish ("The section shall EXPORT the current view"). Kept to
# concrete actions only — NOT generic verbs like provide/be/have, which appear in document prose.
_PRODUCT_VERB = re.compile(
    r"\b(display|show|render|export\w*|import\w*|sort\w*|filter\w*|search\w*|calculat\w*|comput\w*|"
    r"stor\w*|sav\w*|persist\w*|validat\w*|authenticat\w*|authori[sz]\w*|encrypt\w*|send|email\w*|"
    r"notif\w*|generat\w*|creat\w*|updat\w*|delet\w*|process\w*|track\w*|redirect\w*|submit\w*|"
    r"checkout|ship\w*|refund\w*|reserv\w*|cancel\w*|upload\w*|download\w*|print\w*|schedul\w*|"
    r"publish\w*)\b",
    re.IGNORECASE,
)

# Unmistakable disclaimer / meta phrases (substring match). "high-level overview" and "placeholder
# text" were REMOVED — both appear inside real requirements (B-M3).
_META_PHRASES = (
    "must be read in conjunction",
    "should be read in conjunction",
    "30,000-foot", "30000-foot", "30,000 foot",
    "must not be treated as", "should not be treated as", "is not to be treated as",
    "for illustration only", "for illustrative purposes",
    "this is a working draft", "this is a draft",
    "beyond the scope of this document",
)


# --- document-metadata SHAPES that are never product requirements (Part E junk purge) ------------
# A raw backlog CSV export row leaked as a requirement, e.g. "HRZN-12,Story,As a shopper I want…".
_CSV_ROW = re.compile(r"^\s*[A-Za-z]{2,}[-\s]?\d+\s*,\s*\S", re.IGNORECASE)
# A document version / draft marker, e.g. "Version 0.3 — DRAFT" / "v0.3 DRAFT".
_VERSION_LINE = re.compile(r"^\s*(version\s+v?\d|v\d+\.\d)", re.IGNORECASE)
# A byline / attendee / minutes lead-in, e.g. "Present: …", "Prepared by …", "Attendees: …".
_META_LEAD = re.compile(
    r"^\s*(prepared by|authored by|written by|reviewed by|approved by|present|attendees?|attending|"
    r"in attendance|distribution|circulated to|minutes|agenda|apologies)\b[:\s]", re.IGNORECASE)
# A date, used only to catch bare dated bylines ("Sameer Qureshi — 13 Feb 2026") when NOTHING else
# in the line reads as an obligation or product action.
_DATE = re.compile(
    r"\b(\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}|\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE)
_OBLIGATION = re.compile(r"\b(shall|must|should|will|may|is required|are required|needs? to)\b", re.IGNORECASE)


def _junk_shape(s: str) -> str | None:
    """A document-metadata SHAPE (byline / version string / attendee list / raw CSV row) that must
    never become a requirement. Recall-safe: the date rule fires ONLY when the line carries no
    obligation and no product action, so a dated real requirement is never dropped."""
    if _CSV_ROW.match(s):
        return "raw backlog CSV export row"
    if _VERSION_LINE.match(s):
        return "document version / draft string"
    if _META_LEAD.match(s):
        return "document byline / attendee list / meeting-minutes line"
    if _DATE.search(s) and not _OBLIGATION.search(s) and not _PRODUCT_VERB.search(s):
        return "dated byline / metadata line (no obligation)"
    return None


def is_genuine_requirement(statement: str) -> tuple[bool, str]:
    """Return (True, "") for a plausible requirement, or (False, reason) for a clear
    non-requirement (document meta / disclaimer / byline / version / CSV row). High precision,
    recall-safe: a document-noun subject is only rejected when it carries NO concrete product action,
    and metadata SHAPES are only rejected when they carry no obligation."""
    s = (statement or "").strip()
    low = s.lower()
    for phrase in _META_PHRASES:
        if phrase in low:
            return (False, f"reads as document meta/disclaimer ('{phrase}')")
    junk = _junk_shape(s)
    if junk:
        return (False, f"document metadata, not a requirement ({junk})")
    if _META_SUBJECT.match(s) and not _PRODUCT_VERB.search(s):
        return (False, "statement is about the document/section itself, not a product obligation")
    return (True, "")
