"""Shared helpers for the P7 generators (G1 SRS, G2 RTM, seed models).

The single most important rule: generators only ever see APPROVED requirements. Every
entry point funnels through `approved_sorted()`, so a rejected or un-reviewed item can
never appear in an SRS/RTM. `[TBD - Design/BA input]` is the one canonical placeholder.
"""

from __future__ import annotations

from ..models import Requirement, Status

TBD = "[TBD - Design/BA input]"
# For sections that legitimately belong to a LATER phase (visual design, hardware, layout) — not a
# gap in the requirements, so we say so plainly rather than stamping a TBD.
DEFERRED = "*Deferred to the Design phase (no requirements-phase input needed).*"
# For requirement sections/lists that are simply empty for this release.
NONE_ITEMS = "*None identified for this release.*"


def md_cell(text: str) -> str:
    """Sanitise a value for a Markdown TABLE cell: collapse all whitespace to single spaces (a
    newline would prematurely end the table row) and escape pipes (which would add a column). The
    docx converter reverses this by splitting on UNescaped pipes only and unescaping `\\|`."""
    return " ".join((text or "").split()).replace("|", "\\|")


def md_line(text: str) -> str:
    """Collapse a value onto ONE line for a Markdown bullet/paragraph — an embedded newline would
    detach the continuation from its list marker / trailing `[src: …]` tag."""
    return " ".join((text or "").split())


def approved_sorted(requirements: list[Requirement]) -> list[Requirement]:
    """The approved requirements, in a stable order (by internal id)."""
    return sorted(
        (r for r in requirements if r.status == Status.approved), key=lambda r: r.id
    )


def source_label(r: Requirement) -> str:
    """Compact human-readable provenance for one requirement (RTM / [src: ...])."""
    parts = []
    for s in r.source_refs:
        loc = f" §{s.location}" if s.location else ""
        parts.append(f"{s.doc_id}{loc}")
    return "; ".join(parts) if parts else "—"


def source_quotes(r: Requirement) -> str:
    """The verbatim quote(s) backing a requirement, for the RTM evidence column."""
    return " / ".join(f'"{s.raw_quote}"' for s in r.source_refs) if r.source_refs else "—"
