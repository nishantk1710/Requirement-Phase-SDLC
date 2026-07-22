"""Appendix A: Glossary (auto-built) — Part F.

Scans the approved requirements for domain terms / acronyms actually used and emits an alphabetised
Term / Definition table. Deterministic; only terms present in the corpus are emitted, so the glossary
never invents entries and never shows a bare TBD.
"""

from __future__ import annotations

import re

from ..models import Requirement
from .common import NONE_ITEMS

# Domain acronyms/terms we expand when they appear in an approved requirement. Extend freely — only
# entries whose term is actually present in the corpus are rendered.
GLOSSARY: dict[str, str] = {
    "API": "Application Programming Interface",
    "AWB": "Air Waybill (carrier shipment tracking number)",
    "CDN": "Content Delivery Network",
    "CMS": "Content Management System",
    "COD": "Cash on Delivery",
    "CSP": "Content Security Policy",
    "CSRF": "Cross-Site Request Forgery",
    "CX": "Customer Experience",
    "DPDP": "Digital Personal Data Protection Act",
    "ETA": "Estimated Time of Arrival",
    "GST": "Goods and Services Tax",
    "HSTS": "HTTP Strict Transport Security",
    "IGST": "Integrated Goods and Services Tax",
    "IRN": "Invoice Reference Number (GST e-invoice)",
    "JWT": "JSON Web Token",
    "KYC": "Know Your Customer",
    "LCP": "Largest Contentful Paint",
    "MFA": "Multi-Factor Authentication",
    "MRP": "Maximum Retail Price",
    "OMS": "Order Management System",
    "OTP": "One-Time Password",
    "PDP": "Product Detail Page",
    "PII": "Personally Identifiable Information",
    "PLP": "Product Listing Page",
    "PSP": "Payment Service Provider",
    "RBAC": "Role-Based Access Control",
    "RMA": "Return Merchandise Authorisation",
    "RPO": "Recovery Point Objective",
    "RTO": "Recovery Time Objective",
    "RTM": "Requirements Traceability Matrix",
    "SKU": "Stock Keeping Unit",
    "SLA": "Service-Level Agreement",
    "SPA": "Single-Page Application",
    "SRS": "Software Requirements Specification",
    "SSO": "Single Sign-On",
    "SSRF": "Server-Side Request Forgery",
    "TLS": "Transport Layer Security",
    "TTFB": "Time To First Byte",
    "UGC": "User-Generated Content",
    "WCAG": "Web Content Accessibility Guidelines",
    "XSS": "Cross-Site Scripting",
}


def glossary_rows(reqs: list[Requirement], extra_text: str = "") -> list[tuple[str, str]]:
    """Terms from GLOSSARY that actually appear (whole-word) in the approved requirements (and any
    extra SRS prose passed in), alphabetised. Empty if none present."""
    blob = " ".join(r.statement for r in reqs) + " " + (extra_text or "")
    return [
        (term, defn) for term, defn in sorted(GLOSSARY.items())
        if re.search(rf"\b{re.escape(term)}\b", blob)
    ]


def glossary_markdown(reqs: list[Requirement], extra_text: str = "") -> list[str]:
    """Appendix A table lines (header + rows). A single 'None' row if the corpus uses no known term."""
    header = ["| Term | Definition |", "|---|---|"]
    rows = glossary_rows(reqs, extra_text)
    if not rows:
        return header + [f"| {NONE_ITEMS} | — |"]
    return header + [f"| {term} | {defn} |" for term, defn in rows]
