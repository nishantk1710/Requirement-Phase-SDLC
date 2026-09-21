"""Synthesize an ENHANCEMENT-REQUIREMENTS input document (PDF) from an analyzed codebase.

This is a SEPARATE, distinct artifact from the uploaded source: RGA reads the code (scan + understand)
and proposes NEW requirements — enhancements, gaps and improvements that are NOT already implemented,
i.e. deliberately DISTINCT from what the zip already builds. That PDF is written as the project's
run corpus, so the normal pipeline extracts requirements from it and the resulting SRS/RTM specify
the CHANGES to make to the existing system (mapped back to the real files by the impact analysis).

Deterministic by default (a generic cross-cutting enhancement backlog); when an LLM provider is
given it proposes domain-specific enhancements grounded in — but distinct from — the existing code.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from .scan import CodeIndex
from .understand import system_capabilities


class _Feature(BaseModel):
    feature: str = Field(description="a theme for a group of proposed enhancements")
    requirements: list[str] = Field(description="new 'The system shall …' requirements NOT already "
                                                "implemented — distinct from the existing capabilities")


class _Enhancements(BaseModel):
    features: list[_Feature]


_ENH_SYSTEM = (
    "You are a senior business analyst proposing an ENHANCEMENT backlog for an EXISTING system. From "
    "the system summary and module/component map, propose NEW requirements that are NOT already "
    "implemented — improvements, missing capabilities and gaps. Write concise, testable 'The system "
    "shall …' statements grouped by theme. Hard rules: (1) they MUST be genuinely NEW — never restate, "
    "describe, or re-specify a capability the code already has; (2) NEVER name or reference an existing "
    "function, class, component or symbol (e.g. do not write 'AccountNav', 'attachCart', 'timedRequest'); "
    "(3) describe the desired NEW behaviour in plain business language only. Propose 5-10 themes, 2-5 "
    "requirements each."
)

# Deterministic fallback: cross-cutting enhancements most systems can adopt, phrased as requirements.
_GENERIC: list[tuple[str, list[str]]] = [
    ("Observability & Monitoring", [
        "The system shall emit structured, correlated logs for every key operation to support diagnosis.",
        "The system shall expose health and readiness endpoints for automated monitoring.",
        "The system shall record latency and error-rate metrics for its primary endpoints."]),
    ("Security Hardening", [
        "The system shall enforce rate limiting on authentication and other sensitive endpoints.",
        "The system shall validate and sanitize all external input at the API boundary.",
        "The system shall maintain an audit trail of privileged administrative actions."]),
    ("Accessibility & Usability", [
        "The system shall meet WCAG 2.1 AA accessibility guidelines across its primary user flows.",
        "The system shall present clear, actionable error messages when an operation fails."]),
    ("Performance & Scalability", [
        "The system shall serve primary page loads within 2 seconds at the 95th percentile.",
        "The system shall cache frequently-read reference data to reduce backend load."]),
    ("Resilience", [
        "The system shall retry transient downstream failures with bounded exponential backoff.",
        "The system shall degrade gracefully, preserving core flows when a non-critical dependency is unavailable."]),
    ("Internationalization & Reporting", [
        "The system shall support presenting content and currency for multiple locales.",
        "The system shall provide an exportable report of key business events over a chosen period."]),
]


def _llm_enhancements(index: CodeIndex, caps: list[dict], understanding: dict, provider) -> _Enhancements | None:
    modules = "\n".join(
        f"- {c['module']}/ ({c['files']} {c['language']} files): {', '.join(c['key_symbols'][:12]) or '—'}"
        for c in caps[:20])
    readme = (index.get("readme") or "").strip()
    user = (f"EXISTING SYSTEM SUMMARY:\n{understanding.get('summary', '')}\n\n"
            f"ALREADY-IMPLEMENTED MODULES & COMPONENTS (do not restate these):\n{modules}"
            + (f"\n\nREADME (excerpt):\n{readme[:1500]}" if readme else ""))
    try:
        return provider.structured(_ENH_SYSTEM, user, _Enhancements, max_tokens=1800,
                                   timeout_s=60.0, max_attempts=2)
    except Exception:
        return None


# camelCase / PascalCase marker — a code identifier, not natural language (e.g. attachCart, AccountNav)
_CODE_IDENT = re.compile(r"[a-z][A-Z]|[A-Z][a-z]+[A-Z]|[a-z]+[A-Z][a-z]")


def _existing_identifiers(index: CodeIndex) -> set[str]:
    """The existing code's distinctive symbol names (camelCase/PascalCase, ≥5 chars)."""
    out: set[str] = set()
    for f in index.get("files", []):
        for s in f.get("symbols", []):
            if len(s) >= 5 and _CODE_IDENT.search(s):
                out.add(s)
    return out


def synthesize_requirements(index: CodeIndex, understanding: dict, *,
                            project_name: str = "System", provider=None) -> list[dict]:
    """Propose enhancement requirements (distinct from the existing code) as [{feature, requirements}].

    A requirement that names an existing code identifier (e.g. `attachCart`, `AccountNav`) is
    RESTATING what the code already does, not proposing a change — those are dropped so the output
    stays a genuine enhancement backlog."""
    caps = understanding.get("capabilities") or system_capabilities(index)
    if provider is not None:
        res = _llm_enhancements(index, caps, understanding, provider)
        if res and res.features:
            idents = _existing_identifiers(index)
            out: list[dict] = []
            for f in res.features:
                if not (f.feature or "").strip():
                    continue
                reqs = [r.strip() for r in (f.requirements or [])
                        if r and r.strip() and not any(sym in r for sym in idents)]
                if reqs:
                    out.append({"feature": f.feature.strip(), "requirements": reqs})
            if out:
                return out
    return [{"feature": f, "requirements": list(rs)} for f, rs in _GENERIC]


def _latin1(s: str) -> str:
    """fpdf's core fonts are latin-1 only; drop characters it can't encode (em-dash, smart quotes…)."""
    return (s or "").encode("latin-1", "replace").decode("latin-1")


def write_requirements_pdf(project_name: str, understanding: dict, features: list[dict], path: str | Path) -> Path:
    """Render the proposed enhancement requirements to a real (text-extractable) PDF."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def line(txt: str, h: float, size: int, style: str = "") -> None:
        # reset x to the left margin first — fpdf2 leaves the cursor at the RIGHT edge after a
        # multi_cell, which would give the next full-width (w=0) cell zero space.
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(0, h, _latin1(txt))

    line(f"{project_name} - Enhancement Requirements", 10, 18, "B")
    # NOTE: deliberately NO description of the existing system here — the extraction pipeline reads
    # this PDF, so any prose describing the current system would be pulled in as (wrong) requirements.
    # Only the proposed enhancement statements below become requirements; the existing-system context
    # is added to the change SRS's "Existing System Overview" separately, from the code understanding.
    line("Proposed enhancement requirements for an existing system - new capabilities to be built. "
         "Each item below is a requirement.", 6, 10)
    pdf.ln(2)
    for feat in features:
        line(feat["feature"], 8, 16, "B")   # 16 > body 11 => pdfplumber marks these as headings
        for r in feat.get("requirements", []):
            line(f"- {r}", 6, 11)
        pdf.ln(1)
    path = Path(path)
    pdf.output(str(path))
    return path
