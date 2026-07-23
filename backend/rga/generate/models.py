"""Seed analysis models → Appendix B (bounded, DRAFT for human confirmation).

Modelling belongs to the Design phase; Wave-1 only produces a *seed* skeleton so the
Design team has a starting point. These are generated DETERMINISTICALLY from the approved
requirements (features → use cases; features → candidate entities) and rendered as valid
Mermaid by construction. They are always labelled a draft and are bounded (capped counts)
so they can never run away. Refined models are a Design/Wave-2 activity.
"""

from __future__ import annotations

import re

from ..models import Requirement
from .common import approved_sorted
from .srs_template import features_in

MAX_USE_CASES = 20
MAX_ENTITIES = 15
DRAFT_NOTE = "%% DRAFT seed model — auto-generated from approved requirements; for human confirmation."

# Actor roles recognised deterministically in requirement text (fallback: "User").
# Domain-agnostic: covers commerce, clinic, HR, and generic apps so the seed model reflects
# the ACTUAL domain of the requirements rather than any one sample.
_ROLE_LEXICON = [
    ("Customer", ("customer", "shopper", "buyer", "consumer", "guest")),
    ("Administrator", ("administrator", "admin")),
    ("Manager", ("manager", "approver", "supervisor")),
    ("Operations Staff", ("operations", "ops team", "fulfilment", "fulfillment", "warehouse",
                          "clerk", "receptionist", "agent", "support")),
    ("Finance", ("finance", "accounting", "auditor", "payroll")),
    ("Merchandiser", ("merchandising", "merchandiser", "catalogue manager", "catalog manager")),
    ("Doctor", ("doctor", "physician", "clinician")),
    ("Patient", ("patient",)),
    ("Employee", ("employee",)),
    ("User", ("user", "member", "subscriber", "end user")),
]


def _pascal(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    return "".join(w.capitalize() for w in words) or "Entity"


def _safe_label(text: str) -> str:
    """Sanitise a (human-editable) feature name for a Mermaid `["..."]` node label — quotes,
    brackets/braces and newlines would break the diagram parse (D-F3)."""
    t = " ".join((text or "").split())
    for a, b in (('"', "'"), ("[", "("), ("]", ")"), ("{", "("), ("}", ")")):
        t = t.replace(a, b)
    return t or "Unnamed"


def _actors(requirements: list[Requirement]) -> list[str]:
    corpus = " ".join(r.statement.lower() for r in requirements)
    found = [role for role, keys in _ROLE_LEXICON if any(k in corpus for k in keys)]
    return found or ["User"]


# --- deterministic data for the parser-critical SRS structures ----------------
# §2.3 User Classes, §3.3 Software Interfaces, and the Appendix-B entity line are consumed by the
# Design team's parser as TABLES / a tagged line. They must be present EVERY run regardless of the
# LLM narrative, so we derive them deterministically from the approved requirements here (domain-
# agnostic keyword lexicons; first match wins; ordering is stable -> byte-identical output).

# One short, generic characterisation per recognised role (keyed by the _ROLE_LEXICON display name).
_ROLE_DESCRIPTIONS: dict[str, str] = {
    "Customer": "Registered or guest end user who browses the catalogue, manages a cart, and places and tracks orders.",
    "Administrator": "Privileged user who configures the platform and manages users, roles, content, and settings.",
    "Manager": "Oversees a functional area and approves or supervises the work within it.",
    "Operations Staff": "Handles day-to-day fulfilment, support, and operational tasks.",
    "Finance": "Manages billing, payment reconciliation, and financial reporting.",
    "Merchandiser": "Maintains the product catalogue, pricing, and promotions.",
    "Doctor": "Clinical user who delivers care and maintains patient records.",
    "Patient": "Receives care and manages their own health information and appointments.",
    "Employee": "Internal staff member who uses the system to carry out their role.",
    "User": "General authenticated user of the system.",
}

# External software interfaces commonly implied by the requirements (name, keywords, description).
_INTERFACE_LEXICON: list[tuple[str, tuple[str, ...], str]] = [
    ("Payment Provider",
     ("payment", "card", "gateway", "checkout payment", "refund", "billing"),
     "External payment provider integrated through a payment module; card data is handled by the provider."),
    ("Email / SMS Notifications",
     ("email", "sms", "notification", "notify", "otp", "reminder"),
     "Email/SMS gateway for transactional order and account notifications."),
    ("Search Service",
     ("search", "autocomplete", "full-text", "faceted"),
     "Search and indexing service backing catalogue search, autocomplete, and faceted filtering."),
    ("Maps / Geolocation",
     ("geolocation", "map service", "address lookup", "serviceab"),
     "Geolocation/serviceability lookup for delivery addresses and PIN-code checks."),
]

# Candidate domain entities (entity name, keywords) — actors are added first, then these.
_ENTITY_LEXICON: list[tuple[str, tuple[str, ...]]] = [
    ("Product", ("product", "item", "sku", "catalogue", "catalog", "variant")),
    ("Category", ("category", "categories")),
    ("Brand", ("brand",)),
    ("Cart", ("cart", "basket")),
    ("Order", ("order",)),
    ("Payment", ("payment", "refund", "invoice")),
    ("Promotion", ("promo", "coupon", "discount", "voucher", "offer")),
    ("Notification", ("notification", "notify", "alert")),
    ("Address", ("address", "pin code", "pincode")),
    ("Review", ("review", "rating")),
    ("Inventory", ("inventory", "stock", "reservation")),
    ("Report", ("report", "analytic", "dashboard")),
]


def user_classes(requirements: list[Requirement]) -> list[tuple[str, str]]:
    """§2.3 rows: (User Class, Description) derived from the actors in the requirements. Never empty
    (falls back to a single generic 'User')."""
    approved = approved_sorted(requirements)
    return [(role, _ROLE_DESCRIPTIONS.get(role, "User of the system.")) for role in _actors(approved)]


def software_interfaces(requirements: list[Requirement]) -> list[tuple[str, str]]:
    """§3.3 rows: (Name, Description) for the external interfaces implied by the requirements. If none
    are recognised, a single honest 'None identified' row keeps the table well-formed."""
    approved = approved_sorted(requirements)
    corpus = " ".join(r.statement.lower() for r in approved)
    rows = [(name, desc) for name, keys, desc in _INTERFACE_LEXICON if any(k in corpus for k in keys)]
    return rows or [("None identified",
                     "No external software interfaces were identified in the approved requirements for this release.")]


def domain_entities(requirements: list[Requirement]) -> list[str]:
    """Ordered, de-duplicated principal entities (actors first, then keyword-matched domain nouns) for
    the Appendix-B 'principal entities (…)' line the parser reads. Never empty."""
    approved = approved_sorted(requirements)
    corpus = " ".join(r.statement.lower() for r in approved)
    ents: list[str] = list(_actors(approved))
    for name, keys in _ENTITY_LEXICON:
        if name not in ents and any(k in corpus for k in keys):
            ents.append(name)
    return ents


def use_case_model(requirements: list[Requirement]) -> str:
    """A Mermaid flowchart standing in for a use-case diagram: actors → use cases
    (one per feature). Deterministic and bounded."""
    approved = approved_sorted(requirements)
    features = features_in(approved)[:MAX_USE_CASES]
    actors = _actors(approved)
    lines = ["flowchart LR", f"  {DRAFT_NOTE}"]
    for i, actor in enumerate(actors):
        lines.append(f'  actor{i}(["{actor}"])')
    if not features:
        lines.append('  UC0["No functional requirements approved yet"]')
        for i in range(len(actors)):
            lines.append(f"  actor{i} --> UC0")
        return "\n".join(lines)
    for j, feature in enumerate(features):
        lines.append(f'  UC{j}["{_safe_label(feature)}"]')
    # connect every actor to every use case (a seed; Design refines who-does-what)
    for i in range(len(actors)):
        for j in range(len(features)):
            lines.append(f"  actor{i} --> UC{j}")
    return "\n".join(lines)


def erd_model(requirements: list[Requirement]) -> str:
    """A Mermaid erDiagram: one candidate entity per feature plus a central actor entity,
    related 1-to-many. A seed skeleton — attributes/relationships are Design's to refine."""
    approved = approved_sorted(requirements)
    features = features_in(approved)[:MAX_ENTITIES]
    lines = ["erDiagram", f"  {DRAFT_NOTE}"]
    if not features:
        lines += ["  USER {", "    string id", "  }"]
        return "\n".join(lines)
    entities = []
    for feature in features:
        ent = _pascal(feature).upper()[:40]
        if ent not in entities:
            entities.append(ent)
    for ent in entities:
        lines.append(f"  USER ||--o{{ {ent} : manages")
    lines.append("  USER {")
    lines += ["    string id", "    string name"]
    lines.append("  }")
    for ent in entities:
        lines.append(f"  {ent} {{")
        lines += ["    string id", "    string status"]
        lines.append("  }")
    return "\n".join(lines)


def seed_models(requirements: list[Requirement]) -> dict[str, str]:
    """Both seed models, each as a Mermaid source string."""
    return {"use_case": use_case_model(requirements), "erd": erd_model(requirements)}


def seed_models_markdown(models: dict[str, str]) -> str:
    """Appendix B content: fenced Mermaid blocks, clearly marked as drafts."""
    return (
        "> **Draft seed models — for human confirmation.** Modelling is a Design-phase "
        "activity; these skeletons are auto-generated from the approved requirements as a "
        "starting point only.\n\n"
        "**Seed Use-Case Model**\n\n"
        f"```mermaid\n{models['use_case']}\n```\n\n"
        "**Seed Entity-Relationship Model**\n\n"
        f"```mermaid\n{models['erd']}\n```\n"
    )
