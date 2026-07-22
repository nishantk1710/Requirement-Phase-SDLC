"""RACI owner routing — who should decide/confirm each requirement or decision.

The human isn't one person reviewing everything; it's the right person confirming their few. This
maps a requirement/decision to an owning function by deterministic keyword + type rules, so the
review UI can route (and filter) by owner. Explainable and zero-cost; a lead can override.
"""

from __future__ import annotations

import re

# Ordered, first-match-wins. Each is (owner, keyword pattern over statement + feature + nfr-category).
# Noun stems allow a suffix (\w*) so plurals/inflections match ("review"->"reviews", "payment"->"payments").
_OWNER_RULES: list[tuple[str, str]] = [
    ("Legal", r"\b(consent\w*|privac\w*|gdpr|dpdp|legal|complian\w*|regulat\w*|statutory|retention|"
              r"erasure|grievance\w*|cookie\w*|data[-\s]?principal|data[-\s]?subject)\b"
              r"|\bterms (of (service|use)|and conditions)\b"    # not bare 'terms' (e.g. 'search terms')
              r"|(privacy|retention|cookie|data|refund|return) polic\w*"),  # 'policy' only in a legal-ish context
    ("Finance", r"\b(payment\w*|psp|card|tokenis\w*|tokeniz\w*|tax|gst|invoic\w*|refund\w*|pric\w*|"
                r"cod|cash on delivery|billing|reconcil\w*|charge\w*)\b"),
    ("CX / Operations", r"\b(return\w*|rma|cancel\w*|ship\w*|deliver\w*|courier\w*|pickup|fulfil\w*|"
                        r"inventor\w*|stock\w*|warehouse|reserv\w*|backorder\w*|low[-\s]?stock)\b"),
    ("Engineering", r"\b(performanc\w*|latenc\w*|throughput|availab\w*|uptime|scal\w*|secur\w*|encrypt\w*|"
                    r"rate[-\s]?limit\w*|backup\w*|recovery|observ\w*|logging|health check|"
                    r"session\w*|rbac|csrf|xss|sql\w*|nfr)\b"),
    ("Product / Sponsor", r"\b(scope|roadmap|phase\s?2|in v1|out of scope|wishlist\w*|compar\w*|review\w*|"
                          r"rating\w*|loyalty|promotion\w*|coupon\w*|recommendation\w*|personalis\w*|personaliz\w*)\b"),
]

_DEFAULT = "Product Owner"


def owner_of(statement: str, feature: str | None = None, rtype: str | None = None,
             nfr_category: str | None = None) -> str:
    """Return the owning function for a requirement/decision."""
    hay = f"{statement or ''} {feature or ''} {nfr_category or ''}".lower()
    for owner, pattern in _OWNER_RULES:
        if re.search(pattern, hay, re.IGNORECASE):
            return owner
    if (rtype or "") == "non_functional":
        return "Engineering"
    if (rtype or "") in ("business", "constraint"):
        return "Product / Sponsor"
    return _DEFAULT
