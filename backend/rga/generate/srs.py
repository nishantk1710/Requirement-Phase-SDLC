"""G1 — SRS assembler (IEEE-830 / Karl Wiegers structure).

Walks `SRS_STRUCTURE` in order, so EVERY section/subsection of the reference template
appears in the output. Each section is filled by its mode:

  * REQUIREMENTS — rendered deterministically from the APPROVED repository
                   (§4 functional by feature, §5 NFR/BR, §2.5 constraints, §2.7 assumptions);
                   an empty section renders "None identified for this release." (a fact, not a gap);
  * NARRATIVE    — the LLM-drafted prose if supplied, else `[TBD - Design/BA input]`;
  * DESIGN       — a legitimately later-phase artifact (visual tokens, hardware, layout, user
                   docs): LLM prose if drafted, else "Deferred to the Design phase.";
  * TABLE        — the fixed tables (revision history, …);
  * APPENDIX     — B = seed models, C = open-questions;
  * PLACEHOLDER  — a section empty for this release -> "None identified for this release.".

§1.5 References is auto-built from the approved requirements' source documents, and
Appendix A: Glossary auto-expands the acronyms that actually appear in the corpus — so
neither is a TBD anymore. Only approved requirements are ever rendered, and every
functional requirement line carries its internal id, so the RTM (G2) can cross-check
line-by-line (TC7.1/TC7.2).
"""

from __future__ import annotations

from ..models import Requirement, RType
from .common import DEFERRED, NONE_ITEMS, TBD, approved_sorted, md_line
from .glossary import glossary_markdown
from .open_questions import open_questions_markdown
from .srs_template import (
    DOCUMENT_CONVENTIONS,
    FEATURE_SUBSECTIONS,
    NFR_SUBSECTIONS,
    SRS_STRUCTURE,
    TABLE_SPECS,
    TECH_STACK,
    assign_srs_ids,
    canonical_feature,
    features_in,
    to_shall_voice,
)
from .models import domain_entities, software_interfaces, user_classes
from .tech_stack import tech_stack_markdown

# Friendly names for the source documents referenced in §1.5 (keyed by ingest doc_id / stem).
# Unknown ids fall back to the raw id, so a new corpus never crashes — it just shows the id.
_DOC_NAMES: dict[str, str] = {
    "brd": "Business Requirements Document (BRD)",
    "backlog": "Product Backlog",
    "discovery": "Discovery / Stakeholder Call Transcript",
    "discovery_call": "Discovery / Stakeholder Call Transcript",
    "transcript": "Stakeholder Interview Transcript",
    "email_legal": "Legal & Compliance Email Thread",
    "email_payments": "Payments & Checkout Email Thread",
    "email": "Stakeholder Email Thread",
    "ops_intake": "Operations Intake Form",
    "nfr": "Non-Functional Requirements & Security Addendum",
    "nfr_security": "Non-Functional Requirements & Security Addendum",
    "security": "Security Addendum",
    "notes": "Working Notes",
    "spec": "Product Specification",
}

# Appendix A: Glossary is auto-built in generate/glossary.py (Part F).


# MoSCoW (the system's internal priority) -> the High/Medium/Low vocabulary the SRS §1.2 uses.
_MOSCOW_RANK = {"must": 0, "should": 1, "could": 2, "wont": 3}   # lower = higher priority
_MOSCOW_DISPLAY = {"must": "High", "should": "Medium", "could": "Low", "wont": "Low"}


def _heading(num: str, title: str) -> str:
    if num == "":
        return f"## {title}"
    if num in {"A", "B", "C"}:
        return f"## {title}"  # title already carries "Appendix A: ..."
    dots = num.count(".")
    if dots == 0:
        return f"## {num}. {title}"
    if dots == 1:
        return f"### {num} {title}"
    return f"#### {num} {title}"  # e.g. 3.1.1 design-system subsections


def _toc_outline() -> list[str]:
    """A readable heading outline (main + sub headings) for the .md Table of Contents. It is wrapped
    in <!--TOC-->…<!--/TOC--> so the docx export replaces it with a live, page-numbered Word TOC field
    (Markdown has no pages, so the .md keeps the outline; the .docx gets real page numbers)."""
    out = ["[[TOC]]"]
    for num, ttl, _mode in SRS_STRUCTURE:
        if ttl in ("Title Page", "Revision History", "Table of Contents"):
            continue
        if num in ("A", "B", "C"):                       # appendices (title carries "Appendix A: …")
            out.append(f"- **{ttl}**")
        elif num:
            depth = num.count(".")
            label = f"**{num}. {ttl}**" if depth == 0 else f"{num} {ttl}"
            out.append(f"{'    ' * depth}- {label}")
    out.append("[[/TOC]]")
    return out


def _table(title: str, project_name: str, date: str) -> list[str]:
    spec = TABLE_SPECS.get(title)
    if title == "Title Page":
        # Wrapped in a marker the docx export renders as a CENTRED, page-1 title block (page break
        # after). Lines are plain text (no sub-headings) so nothing here leaks into the Table of
        # Contents; in the .md they read as a simple centred title block.
        return [
            "[[TITLEPAGE]]",
            "# Software Requirements Specification",
            "for",
            project_name,
            "Version 1.0 approved",
            "Prepared by RGA (Agentic Requirement Gathering & Analysis)",
            f"{date}",
            "Document format based on the IEEE SRS template. Copyright © 1999 by Karl E. Wiegers. "
            "Permission is granted to use, modify, and distribute this document.",
            "[[/TITLEPAGE]]",
        ]
    if title == "Table of Contents":
        return _toc_outline()
    if title == "Revision History":
        return [
            "| " + " | ".join(spec) + " |",
            "|" + "|".join(["---"] * len(spec)) + "|",
            f"| RGA | {date} | Initial draft generated from approved requirements | 0.1 |",
        ]
    if spec:  # generic empty table with header + a TBD row
        return [
            "| " + " | ".join(spec) + " |",
            "|" + "|".join(["---"] * len(spec)) + "|",
            "| " + " | ".join([TBD] + [""] * (len(spec) - 1)) + " |",
        ]
    return [TBD]


def _stimulus_response(idx: int, feature: str, feats_reqs: list[Requirement], narrative: dict[str, str]) -> str:
    """§4.x.2 Stimulus/Response — prefer the LLM-drafted DISCRETE sequences for this feature;
    otherwise emit an honest, requirement-grounded default. The default is itself DISCRETE (a
    primary success sequence + an alternate/error sequence, each with its own explicit Stimulus and
    Response), matching the reference SRS shape — never one packed paragraph and never a bare TBD."""
    drafted = narrative.get(f"feature_flow::{feature}")
    if drafted and drafted.strip():
        return drafted.strip()
    n = len(feats_reqs)
    plural = "requirement" if n == 1 else "requirements"
    return (
        f"1. **Stimulus —** An actor initiates a *{feature}* action from the interface or an "
        f"integrating system.\n"
        f"   **Response —** The system validates the request and fulfils it per the {n} functional "
        f"{plural} in §4.{idx}.3, then confirms the outcome to the actor.\n"
        f"2. **Stimulus —** The actor submits invalid input, or a downstream dependency fails during "
        f"the *{feature}* action.\n"
        f"   **Response —** The system rejects the request with a clear, specific message and leaves "
        f"all data in a consistent state, with no partial change applied.\n\n"
        f"*Detailed per-scenario sequences are elaborated in the Design phase.*"
    )


def _feature_section(
    idx: int, feature: str, reqs: list[Requirement], id_map: dict[str, str],
    narrative: dict[str, str] | None = None,
) -> list[str]:
    feats_reqs = [r for r in reqs if canonical_feature(r.feature) == feature]
    # the feature inherits the HIGHEST MoSCoW priority among its requirements, shown in the
    # High/Medium/Low vocabulary §1.2 promises (not the raw MoSCoW enum, and not an arbitrary pick).
    prios = [r.priority.value for r in feats_reqs if r.priority]
    top = min(prios, key=lambda p: _MOSCOW_RANK.get(p, 1), default=None)
    priority = _MOSCOW_DISPLAY.get(top, "Medium") if top else "Medium"
    out = [
        f"### 4.{idx} {feature}",
        f"#### 4.{idx}.1 {FEATURE_SUBSECTIONS[0]}",
        f'Requirements grouped under the "{feature}" capability. Priority: {priority}.',
        f"#### 4.{idx}.2 {FEATURE_SUBSECTIONS[1]}",
        _stimulus_response(idx, feature, feats_reqs, narrative or {}),
        f"#### 4.{idx}.3 {FEATURE_SUBSECTIONS[2]}",
    ]
    for r in feats_reqs:
        # SRS body shows the clean statement only; provenance lives in the RTM (Part L). Present the
        # obligation in a uniform 'shall' voice (Fix E) — meaning-preserving, RTM keeps the original.
        out.append(f"- **{id_map.get(r.id, '—')}:** {md_line(to_shall_voice(r.statement))}")
    return out


def _requirements_section(
    num: str, reqs: list[Requirement], id_map: dict[str, str],
    narrative: dict[str, str] | None = None,
) -> list[str]:
    """Render a REQUIREMENTS-mode section from the approved repository. An empty section is
    a real fact ("nothing of this kind in scope"), rendered as NONE_ITEMS — not a TBD gap."""
    def line(r: Requirement) -> str:
        tag = id_map.get(r.id)
        prefix = f"**{tag}:** " if tag else ""
        # SRS body shows the clean statement only; provenance lives in the RTM (Part L).
        return f"- {prefix}{md_line(r.statement)}"

    if num == "4":  # System Features — grouped by feature
        functional = [r for r in reqs if r.rtype == RType.functional]
        feats = features_in(functional)
        if not feats:
            return [NONE_ITEMS]
        out: list[str] = []
        for i, feature in enumerate(feats, 1):
            out += _feature_section(i, feature, functional, id_map, narrative)
        return out

    if num == "2.5":  # Design and Implementation Constraints
        items = [r for r in reqs if r.rtype == RType.constraint]
        return [line(r) for r in items] or [NONE_ITEMS]

    if num == "2.7":  # Assumptions and Dependencies
        items = [r for r in reqs if r.rtype == RType.assumption]
        return [line(r) for r in items] or [NONE_ITEMS]

    if num == "5.5":  # Business Rules
        items = [r for r in reqs if r.rtype == RType.business]
        return [line(r) for r in items] or [NONE_ITEMS]

    # NFR subsections 5.1 / 5.2 / 5.3 / 5.4
    target = {"5.1": "5.1 Performance Requirements", "5.2": "5.2 Safety Requirements",
              "5.3": "5.3 Security Requirements", "5.4": "5.4 Software Quality Attributes"}.get(num)
    if target:
        items = [
            r for r in reqs
            if r.rtype == RType.non_functional
            and NFR_SUBSECTIONS.get(r.nfr_category or "", "5.4 Software Quality Attributes") == target
        ]
        return [line(r) for r in items] or [NONE_ITEMS]
    return [NONE_ITEMS]


def _references(reqs: list[Requirement]) -> list[str]:
    """§1.5 References — the distinct source documents the requirements were traced from."""
    docs: list[str] = []
    for r in reqs:
        for s in r.source_refs:
            if s.doc_id and s.doc_id not in docs:
                docs.append(s.doc_id)
    if not docs:
        return [NONE_ITEMS]
    lines = ["The requirements in this document were elicited from the following sources:", ""]
    lines += [f"- {_DOC_NAMES.get(d, d)}  (`{d}`)" for d in sorted(docs)]
    return lines


def _cell(text: str) -> str:
    """Collapse whitespace and escape the column separator so cell text can't break the table."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _two_col_table(headers: list[str], rows: list[tuple[str, str]]) -> list[str]:
    """A GitHub-flavoured markdown table the docx export renders as a real Word table."""
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return lines


def _user_classes_section(reqs: list[Requirement]) -> list[str]:
    """§2.3 — a short intro then a GUARANTEED `User Class | Description` table derived from the
    actors in the requirements (never LLM-dependent; the Design parser reads roles from this table)."""
    headers = TABLE_SPECS["User Classes and Characteristics"]
    return ["The system serves the following user classes:", ""] + _two_col_table(headers, user_classes(reqs))


def _software_interfaces_section(reqs: list[Requirement]) -> list[str]:
    """§3.3 — a short intro then a GUARANTEED `Name | Description` table of external interfaces
    (never LLM-dependent; the Design parser reads external interfaces from this table)."""
    headers = TABLE_SPECS["Software Interfaces"]
    return (["The platform integrates with the following external software interfaces:", ""]
            + _two_col_table(headers, software_interfaces(reqs)))


def _appendix_b_placeholder(reqs: list[Requirement]) -> str:
    """Appendix B — a short DEFERRAL paragraph, NO diagrams (matches the reference SRS, which ships
    no seed models). Names the analysis models Design will produce, derived-in-name from this SRS's
    Section-4 features. Emits no Mermaid/flowchart/ER syntax."""
    functional = [r for r in reqs if r.rtype == RType.functional]
    feats = features_in(functional)
    feat_list = (", ".join(feats[:8]) + ("…" if len(feats) > 8 else "")) if feats else "the Section 4 capabilities"
    # Name the principal entities so the Design parser can extract them (it reads "principal
    # entities (…)"). Derived deterministically from the requirements — never LLM-dependent.
    entities = domain_entities(reqs)
    entity_line = (
        f"The principal entities ({', '.join(entities)}) identified from the approved requirements "
        "will be elaborated in the Design-phase models.\n\n"
    ) if entities else ""
    return (
        entity_line
        + "Detailed analysis models are produced in the **Design phase** and attached here when "
        "available. Derived-in-name from the actors and the Section 4 features defined in this "
        "SRS, the Design phase will produce:\n\n"
        "- a **context data-flow diagram** — the system boundary and the data exchanged with its "
        "external actors and third-party services;\n"
        "- an **entity–relationship diagram** — the core domain entities and their relationships;\n"
        f"- a **domain class / use-case model** — covering {feat_list}.\n\n"
        "These models are intentionally deferred to Design; this SRS specifies the requirements they "
        "will realise."
    )


def generate_srs(
    requirements: list[Requirement],
    *,
    project_name: str = "<Project Name>",
    date: str = "<date>",
    narrative: dict[str, str] | None = None,
    open_questions: list[dict] | None = None,
    tech_stack: dict | None = None,
    tech_stack_selection: dict | None = None,   # aspect key -> chosen candidate name
    design_tokens: dict[str, str] | None = None,   # section number -> §3.1.x markdown (Parts B/C)
) -> str:
    """Assemble the full IEEE-830 SRS (Markdown) from APPROVED requirements only.

    `narrative` maps section-number -> prose (from the narrative agent), plus optional
    "feature_flow::<name>" entries for §4.x.2. A missing prose section renders TBD; a missing
    DESIGN section renders "Deferred..."; a missing feature flow uses a grounded default.
    `open_questions` fills Appendix C; Appendix B is a deferral placeholder (no diagrams).
    """
    approved = approved_sorted(requirements)
    id_map = assign_srs_ids(approved)
    narrative = narrative or {}
    blocks: list[str] = []

    for num, title, mode in SRS_STRUCTURE:
        # Section 4's own body is produced by the feature sub-structure; emit its heading
        # then the grouped features.
        if mode is None:
            blocks.append(_heading(num, title))
            continue

        if num == "4":
            blocks.append(_heading(num, title))
            blocks.extend(_requirements_section("4", approved, id_map, narrative))
            continue

        if title == "Title Page":  # the title-page body is its own H1; no extra heading
            blocks.append("\n".join(_table(title, project_name, date)))
            continue

        heading = _heading(num, title)

        if design_tokens and num in design_tokens:  # §3.1.1–3.1.5 generated tokens (Parts B/C)
            body = [design_tokens[num]]
        elif num == "1.2":  # Document Conventions is ours, deterministic (not LLM/TBD)
            body = [DOCUMENT_CONVENTIONS]
        elif num == "1.5":  # References — auto-built from the approved requirements' sources
            body = _references(approved)
        elif num == "2.3":  # User Classes — guaranteed table (parser reads roles here), not LLM prose
            body = _user_classes_section(approved)
        elif num == "3.3":  # Software Interfaces — guaranteed table (parser reads interfaces here)
            body = _software_interfaces_section(approved)
        elif title == "Appendix A: Glossary":  # auto-expanded acronyms found in the corpus
            body = glossary_markdown(approved)
        elif mode == "requirements":
            body = _requirements_section(num, approved, id_map)
        elif mode == "narrative":
            body = [narrative.get(num, TBD)]
        elif mode == "design":  # later-phase artifact: LLM prose if drafted, else "Deferred..."
            body = [narrative.get(num) or DEFERRED]
        elif mode == "table":
            body = _table(title, project_name, date)
        elif mode == TECH_STACK:  # §7 — adopted-from-inputs stack, or two proposed options
            body = [tech_stack_markdown(tech_stack, tech_stack_selection)]
        elif mode == "placeholder":  # section genuinely empty for this release
            body = [NONE_ITEMS]
        elif mode == "appendix":
            if num == "B":
                body = [_appendix_b_placeholder(approved)]   # deferral paragraph, no diagrams (Part G)
            elif num == "C":
                from .open_questions import compile_open_questions, reconcile_open_questions

                # Part H: drop pointer/absorbed items and suppress open items already covered by an
                # approved requirement (recall-safe) before compiling the disciplined TBD list.
                reconciled = reconcile_open_questions(open_questions or [], approved)
                body = [open_questions_markdown(compile_open_questions(reconciled))]
            else:
                body = [TBD]
        else:
            body = [TBD]

        blocks.append(heading)
        blocks.append("\n".join(body))

    return "\n\n".join(b for b in blocks if b) + "\n"
