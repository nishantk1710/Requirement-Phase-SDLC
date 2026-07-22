"""Deterministic MoSCoW prioritisation.

Assigns each requirement a proposed MoSCoW priority (must / should / could) from explainable
signals, so a reviewer can triage by importance instead of reading a flat list. Zero-cost and
reproducible; the human confirms or overrides at the review gate.

'wont' is never assigned here — out-of-scope items are routed to open-questions, not kept as firm
requirements. NOTE: the requirement's modal verb is deliberately NOT the primary signal — the
extractor normalises almost everything to "shall", so modal alone would mark every requirement
'must' (it did: 220/239). Instead we key on CRITICALITY and explicit optionality, so the proposal
actually discriminates. Signals are applied in order of decisiveness:

  1. an explicit optionality cue ("nice to have", "optional", "if feasible")  -> could  (an explicit
     hedge wins even over a must-domain keyword, so "optionally … audit export" is not forced to must)
  2. compliance / legal / security / payment obligation  -> must  (regulatory, non-negotiable)
  3. inferred (tentative) requirement  -> could  (confirm before committing)
  4. explicit soft modal ("may"/"could", without a hard modal)  -> could
  5. business rule / constraint (firm policy)  -> must
  6. everything else  -> should  (proposed default; the product owner elevates to must)
"""

from __future__ import annotations

import re

from ..models import Priority

_MUST_DOMAIN = re.compile(
    r"\b(complian\w*|legal|regulat\w*|statutory|security|secure|privacy|gdpr|dpdp|pci|"
    r"payment\w*|tax|invoic\w*|consent|authenticat\w*|authoris\w*|authoriz\w*|encrypt\w*|"
    r"audit|data[-\s]?retention|erasure|grievance)\b",
    re.IGNORECASE,
)
_COULD_CUE = re.compile(
    r"\b(nice[-\s]to[-\s]have|optional\w*|if feasible|where possible|if time permits|"
    r"stretch goal|tentativ\w*)\b",
    re.IGNORECASE,
)
_HARD_MODAL = re.compile(r"\b(shall|must|required?|mandatory|critical)\b", re.IGNORECASE)
_SOFT_MODAL = re.compile(r"\b(may|might|could|can optionally)\b", re.IGNORECASE)
# Launch-critical commerce basics — a storefront cannot ship without these, so they are High (must),
# never the 'should' default (Part I). Kept generic (verb+object patterns), not project-specific.
_CORE_DOMAIN = re.compile(
    r"\b(add(?:ing)?[^.]{0,20}\bto (?:the )?cart|to cart|shopping cart|persistent cart|provide[^.]{0,15}cart|"
    r"cart\b|check[-\s]?out|checkout|place (?:an? )?order|order placement|complete[^.]{0,15}purchase|"
    r"reserve (?:the )?stock|decrement[^.]{0,15}stock|stock (?:on|when|at)|out[-\s]?of[-\s]?stock|"
    r"product listing|category tree|multi[-\s]?categor|categor(?:y|ies)|faceted|browse the catalogue)\b",
    re.IGNORECASE,
)


def prioritize(statement: str, rtype, *, inferred: bool = False) -> tuple[Priority, str]:
    """Return (Priority, reason) for a requirement. Deterministic and explainable. This is a
    PROPOSAL — the product owner confirms/overrides at the review gate."""
    s = statement or ""
    # an EXPLICIT optionality cue wins even over a must-domain keyword, so "optionally provide an
    # audit export" is not forced to 'must' by the word 'audit' (L2)
    if _COULD_CUE.search(s):
        return Priority.could, "explicitly optional / nice-to-have"
    if _MUST_DOMAIN.search(s):
        return Priority.must, "compliance / security / payment obligation — non-negotiable"
    if _CORE_DOMAIN.search(s):
        return Priority.must, "launch-critical commerce capability"
    if inferred:
        return Priority.could, "inferred / tentative — confirm before committing"
    if _SOFT_MODAL.search(s) and not _HARD_MODAL.search(s):
        return Priority.could, "stated as optional (may / could)"
    rt = getattr(rtype, "value", str(rtype))
    if rt in ("business", "constraint"):
        return Priority.must, "business rule / constraint — firm policy"
    return Priority.should, "proposed default — confirm with the product owner"
