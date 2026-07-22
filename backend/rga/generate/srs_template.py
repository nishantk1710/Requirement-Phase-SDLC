"""The IEEE-830 (Karl Wiegers) SRS template — structure, conventions, and the
mapping from our requirement model into it.

This is the *format* the Design team expects (from `Reference SRS format.docx`); it is
NOT the example content. The G1 generator (Phase P7) consumes this to render the SRS.

Conventions (from the template):
  * functional      -> Section 4 (System Features), grouped by `feature`, tagged REQ-n
  * non-functional  -> Section 5.1-5.4 by ISO/IEC 25010 category, tagged NFR-n
  * business rule   -> Section 5.5 Business Rules, tagged BR-n
  * constraint      -> Section 2.5 Design and Implementation Constraints (listed)
  * assumption      -> Section 2.7 Assumptions and Dependencies (listed)
  * Appendix B (Analysis Models) -> our SEED MODELS (produced in Design; placeholder here)
  * Appendix C (TBD list)        -> our OPEN-QUESTIONS / conflicts log
  * "shall" = mandatory, "should" = desirable; priority High/Medium/Low per feature.
Formal SRS ids (REQ-n / NFR-n / BR-n) are assigned at generation time; internal working
ids (REQ-0xx) stay stable for traceability, and the RTM maps between them.
"""

from __future__ import annotations

import re

# --- fill modes ---------------------------------------------------------------
NARRATIVE = "narrative"        # hybrid: LLM drafts what it can; rest -> [TBD] placeholder
REQUIREMENTS = "requirements"  # rendered deterministically from the approved repo
TABLE = "table"                # generated table (revision history, glossary, ...)
PLACEHOLDER = "placeholder"    # section that is simply empty for this release -> "None identified"
DESIGN = "design"              # legitimately a later-phase (Design) artifact -> "Deferred..." (or LLM prose if drafted)
APPENDIX = "appendix"
TECH_STACK = "tech_stack"      # §7 Technology Stack — adopted from inputs, or two proposed options

# --- ordered section structure ----------------------------------------------
# (number, title, fill_mode)
SRS_STRUCTURE: list[tuple[str, str, str]] = [
    ("", "Title Page", TABLE),
    ("", "Revision History", TABLE),
    ("", "Table of Contents", TABLE),
    ("1", "Introduction", None),
    ("1.1", "Purpose", NARRATIVE),
    ("1.2", "Document Conventions", NARRATIVE),
    ("1.3", "Intended Audience and Reading Suggestions", NARRATIVE),
    ("1.4", "Product Scope", NARRATIVE),
    ("1.5", "References", PLACEHOLDER),
    ("2", "Overall Description", None),
    ("2.1", "Product Perspective", NARRATIVE),
    ("2.2", "Product Functions", NARRATIVE),
    ("2.3", "User Classes and Characteristics", NARRATIVE),
    ("2.4", "Operating Environment", NARRATIVE),
    ("2.5", "Design and Implementation Constraints", REQUIREMENTS),  # <- constraints
    ("2.6", "User Documentation", DESIGN),                           # produced in Design/delivery
    ("2.7", "Assumptions and Dependencies", REQUIREMENTS),           # <- assumptions
    ("3", "External Interface Requirements", None),
    ("3.1", "User Interfaces", NARRATIVE),          # design-system intro (UX facts we DO know)
    ("3.1.1", "Overall Visual Theme", DESIGN),      # visual-design tokens: Design-phase artifacts
    ("3.1.2", "Colour Tokens", DESIGN),
    ("3.1.3", "Typography Tokens", DESIGN),
    ("3.1.4", "Spacing, Radius, and Elevation Tokens", DESIGN),
    ("3.1.5", "Layout and Interaction Standards", DESIGN),
    ("3.2", "Hardware Interfaces", DESIGN),          # rarely known at RGA; deferred unless drafted
    ("3.3", "Software Interfaces", NARRATIVE),
    ("3.4", "Communications Interfaces", NARRATIVE),
    ("4", "System Features", REQUIREMENTS),                          # <- functional, by feature
    ("5", "Other Nonfunctional Requirements", None),
    ("5.1", "Performance Requirements", REQUIREMENTS),
    ("5.2", "Safety Requirements", REQUIREMENTS),
    ("5.3", "Security Requirements", REQUIREMENTS),
    ("5.4", "Software Quality Attributes", REQUIREMENTS),            # <- other NFRs
    ("5.5", "Business Rules", REQUIREMENTS),                         # <- business
    ("6", "Other Requirements", PLACEHOLDER),
    ("7", "Technology Stack", TECH_STACK),                           # <- adopted-from-inputs / 2 options
    ("A", "Appendix A: Glossary", TABLE),
    ("B", "Appendix B: Analysis Models", APPENDIX),                 # <- seed models placeholder
    ("C", "Appendix C: To Be Determined List", APPENDIX),           # <- open-questions
]

# --- requirement type -> where it goes + id prefix ---------------------------
TYPE_TO_SECTION: dict[str, dict] = {
    "functional": {"section": "4", "id_prefix": "REQ"},
    "non_functional": {"section": "5.1-5.4", "id_prefix": "NFR"},
    "business": {"section": "5.5", "id_prefix": "BR"},
    "constraint": {"section": "2.5", "id_prefix": None},
    "assumption": {"section": "2.7", "id_prefix": None},
}

# ISO/IEC 25010 category -> Section 5 subsection
NFR_SUBSECTIONS: dict[str, str] = {
    "performance": "5.1 Performance Requirements",
    "safety": "5.2 Safety Requirements",
    "security": "5.3 Security Requirements",
    "usability": "5.4 Software Quality Attributes",
    "reliability": "5.4 Software Quality Attributes",
    "maintainability": "5.4 Software Quality Attributes",
    "portability": "5.4 Software Quality Attributes",
    "compatibility": "5.4 Software Quality Attributes",
}

# Our artifacts that fill the appendices
APPENDIX_SOURCES: dict[str, str] = {
    "B": "seed models (use-case / DFD / ERD) — produced in Design; placeholder here",
    "C": "open-questions / conflicts log (TBD items)",
}

# Fixed per-feature sub-structure of EVERY Section 4 feature (4.x.1 / .2 / .3).
FEATURE_SUBSECTIONS: list[str] = [
    "Description and Priority",
    "Stimulus/Response Sequences",
    "Functional Requirements",
]

# Tables in the template and their column headers (the delivery format to reproduce).
TABLE_SPECS: dict[str, list[str]] = {
    "Revision History": ["Name", "Date", "Reason For Changes", "Version"],
    "User Classes and Characteristics": ["User Class", "Characteristics"],
    "Software Interfaces": ["Interface", "Description"],
    "Appendix A: Glossary": ["Term", "Definition"],
    "Appendix C: To Be Determined List": ["ID", "Description"],
    # §3.1 design-system token tables (from the updated reference SRS)
    "Colour Tokens": ["Token", "Hex", "Usage"],
    "Typography Tokens": ["Token", "Size", "Weight", "Usage"],
    "Spacing, Radius, and Elevation Tokens": ["Token", "Value", "Usage"],
}

# Standard 1.2 Document Conventions text (our conventions, matching the template).
DOCUMENT_CONVENTIONS: str = (
    "Functional requirements are tagged REQ-n, non-functional requirements NFR-n, and "
    "business rules BR-n, each with a unique identifier used for downstream "
    "traceability. Each system feature carries a priority of High, Medium, or Low; a "
    "requirement inherits its feature's priority unless overridden. 'Shall' denotes a "
    "mandatory requirement; 'should' a desirable one. Items still under discussion are "
    "marked TBD and tracked in Appendix C."
)

# The COMPLETE fixed outline the generated SRS must contain (number, title), taken
# verbatim from `Reference SRS format.docx`. Section 4's features are dynamic and each
# expands into FEATURE_SUBSECTIONS. Every non-4 section MUST appear in the output —
# with a "[TBD - Design/BA input]" placeholder where no content is available yet.
REFERENCE_OUTLINE: list[tuple[str, str]] = [
    ("1", "Introduction"),
    ("1.1", "Purpose"),
    ("1.2", "Document Conventions"),
    ("1.3", "Intended Audience and Reading Suggestions"),
    ("1.4", "Product Scope"),
    ("1.5", "References"),
    ("2", "Overall Description"),
    ("2.1", "Product Perspective"),
    ("2.2", "Product Functions"),
    ("2.3", "User Classes and Characteristics"),
    ("2.4", "Operating Environment"),
    ("2.5", "Design and Implementation Constraints"),
    ("2.6", "User Documentation"),
    ("2.7", "Assumptions and Dependencies"),
    ("3", "External Interface Requirements"),
    ("3.1", "User Interfaces"),
    ("3.1.1", "Overall Visual Theme"),
    ("3.1.2", "Colour Tokens"),
    ("3.1.3", "Typography Tokens"),
    ("3.1.4", "Spacing, Radius, and Elevation Tokens"),
    ("3.1.5", "Layout and Interaction Standards"),
    ("3.2", "Hardware Interfaces"),
    ("3.3", "Software Interfaces"),
    ("3.4", "Communications Interfaces"),
    ("4", "System Features"),
    ("5", "Other Nonfunctional Requirements"),
    ("5.1", "Performance Requirements"),
    ("5.2", "Safety Requirements"),
    ("5.3", "Security Requirements"),
    ("5.4", "Software Quality Attributes"),
    ("5.5", "Business Rules"),
    ("6", "Other Requirements"),
    ("A", "Appendix A: Glossary"),
    ("B", "Appendix B: Analysis Models"),
    ("C", "Appendix C: To Be Determined List"),
]

_DQ = "~"  # sort sentinel so un-featured items sort last

# Canonical DOMAIN feature buckets (Part E consolidation). The extractor emits many near-duplicate
# feature labels ("Search & Browse" vs "Product Detail", "Policy Pages" vs "Cookie Consent"); we map
# each raw label to ONE domain bucket by keyword so §4 has clean domain features (like QuickBite's 8),
# not 17 with duplicates. GENERAL (keyword-driven), not project-specific — first match wins.
_FEATURE_BUCKETS: list[tuple[str, tuple[str, ...]]] = [
    ("Account & Authentication",
     ("account", "login", "log in", "sign in", "sign up", "signup", "register", "registration",
      "authenticat", "password", "profile", "session", "otp", "address book")),
    ("Catalogue & Browsing",
     ("browse", "search", "catalogue", "catalog", "category", "categories", "product detail",
      "product listing", "listing", "faceted", "facet", "pdp", "plp", "discovery", "variant", "wishlist")),
    ("Cart & Checkout",
     ("cart", "basket", "checkout", "check out", "order placement", "place order", "place an order")),
    ("Payments",
     ("payment", "card", "wallet", "cod", "cash on delivery", "refund", "gst", "tax", "invoic", "billing")),
    ("Orders & Fulfilment",
     ("order", "fulfil", "fulfill", "delivery", "deliver", "shipping", "shipment", "track",
      "return", "rma", "cancel", "stock", "inventory")),
    ("Promotions",
     ("promotion", "promo", "coupon", "discount", "voucher", "offer", "deal")),
    ("Notifications",
     ("notification", "notify", "email", "sms", "alert", "reminder", "in-app message")),
    ("Compliance & Policies",
     ("policy", "policies", "cookie", "consent", "privacy", "legal", "complian", "terms",
      "gdpr", "dpdp", "grievance", "data retention", "erasure")),
    ("Admin & Management",
     ("admin", "management", "dashboard", "report", "analytic", "role", "rbac", "staff",
      "merchandis", "cms", "content management", "audit")),
]


def canonical_feature(name: str | None) -> str:
    """Map a raw feature label to ONE canonical domain bucket (keyword match, first wins). Unknown
    labels are kept as-is (title-trimmed) so nothing is silently lost."""
    if not name or not str(name).strip():
        return "System Features"
    low = str(name).lower()
    for canon, kws in _FEATURE_BUCKETS:
        if any(k in low for k in kws):
            return canon
    return str(name).strip()


# --- functional requirement shape (Fix E) ------------------------------------
_OBLIGATION_RE = re.compile(r"\b(?:shall|must|will)\b", re.IGNORECASE)
_MUST_RE = re.compile(r"\bmust\b", re.IGNORECASE)


def is_wellformed_functional(statement: str) -> bool:
    """True if a functional requirement is well-formed IEEE-830 shape: it starts with a capitalised
    subject and states an obligation with an imperative modal (shall/must/will). This accepts BOTH a
    plain 'Subject shall …' AND an event-driven 'When/If …, … shall …' (a valid, often preferred
    shape), plus non-'The' subjects ('Every order shall …', 'Customers shall …'). The older
    '^The .+ shall ' check wrongly rejected those legitimate forms — this is the correct predicate."""
    s = (statement or "").strip()
    return bool(s) and s[:1].isupper() and bool(_OBLIGATION_RE.search(s))


def to_shall_voice(statement: str) -> str:
    """Render-time voice normalisation (meaning-preserving): present the obligation verb as 'shall'
    for a uniform SRS voice — 'must' -> 'shall' (capitalisation preserved). Conditionals, subjects
    and everything else are untouched, and the STORED requirement is never changed (the RTM keeps
    the approved wording) — this only affects how §4 reads. Analogous to the render-time citation
    strip (Part L)."""
    def _repl(m: re.Match) -> str:
        return "Shall" if m.group(0)[:1].isupper() else "shall"
    return _MUST_RE.sub(_repl, statement or "")


def _get(r, key, default=None):
    """Accept either a dict or an object with attributes."""
    return r.get(key, default) if isinstance(r, dict) else getattr(r, key, default)


def section_for(requirement) -> str:
    """Human-readable SRS location for a requirement."""
    t = _get(requirement, "rtype")
    if hasattr(t, "value"):
        t = t.value
    if t == "functional":
        return f"4. System Features > {canonical_feature(_get(requirement, 'feature'))}"
    if t == "non_functional":
        return NFR_SUBSECTIONS.get(_get(requirement, "nfr_category") or "", "5.4 Software Quality Attributes")
    if t == "business":
        return "5.5 Business Rules"
    if t == "constraint":
        return "2.5 Design and Implementation Constraints"
    if t == "assumption":
        return "2.7 Assumptions and Dependencies"
    return "6. Other Requirements"


def features_in(requirements) -> list[str]:
    """Ordered list of features (for Section 4 subsections)."""
    seen: list[str] = []
    for r in requirements:
        t = _get(r, "rtype")
        t = t.value if hasattr(t, "value") else t
        if t == "functional":
            f = canonical_feature(_get(r, "feature"))
            if f not in seen:
                seen.append(f)
    return seen


def assign_srs_ids(requirements) -> dict[str, str]:
    """Assign formal, template-facing ids: REQ-n (functional), NFR-n (non-functional),
    BR-n (business). Deterministic. Constraints/assumptions get no tagged id (they are
    listed in §2.5 / §2.7). Returns internal_id -> formal_id."""

    def rtype(r):
        t = _get(r, "rtype")
        return t.value if hasattr(t, "value") else t

    # REQ-n must be numbered in the SAME order Section 4 renders features (first-seen), so the SRS
    # never prints REQ-2 before REQ-1 and the RTM row order matches the SRS (D-F4).
    functional_reqs = [r for r in requirements if rtype(r) == "functional"]
    feat_rank = {f: i for i, f in enumerate(features_in(functional_reqs))}
    functional = sorted(
        functional_reqs,
        key=lambda r: (feat_rank.get(canonical_feature(_get(r, "feature")), 1_000_000), _get(r, "id")),
    )
    nonfunctional = sorted(
        (r for r in requirements if rtype(r) == "non_functional"),
        key=lambda r: (NFR_SUBSECTIONS.get(_get(r, "nfr_category") or "", "5.4 Software Quality Attributes"), _get(r, "id")),
    )
    business = sorted(
        (r for r in requirements if rtype(r) == "business"),
        key=lambda r: _get(r, "id"),
    )

    out: dict[str, str] = {}
    for i, r in enumerate(functional, 1):
        out[_get(r, "id")] = f"REQ-{i}"
    for i, r in enumerate(nonfunctional, 1):
        out[_get(r, "id")] = f"NFR-{i}"
    for i, r in enumerate(business, 1):
        out[_get(r, "id")] = f"BR-{i}"
    return out
