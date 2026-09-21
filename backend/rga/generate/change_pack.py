"""Brownfield change pack — a CHANGE-ONLY SRS + RTM for enhancements to an EXISTING codebase.

Unlike the greenfield handoff (a full IEEE-830 SRS of the whole system), this documents only the
requirements to be *implemented in the existing system*, grounded in a `SystemUnderstanding`
(codebase.understand): an "Existing System Overview" for context and an "Impact on Existing System"
section mapping each change requirement to the existing files it most likely touches.

Deterministic: given the same approved requirements + code index, it always produces the same pack.
"""

from __future__ import annotations

import csv
import io

from ..codebase.understand import impact_for
from ..models import Requirement, RType
from ..review.gate import ready_for_generation
from .common import approved_sorted, md_cell, md_line
from .srs_template import assign_srs_ids, canonical_feature, features_in, to_shall_voice

_RTYPE_LABEL = {
    RType.functional: "Functional", RType.non_functional: "Non-Functional",
    RType.business: "Business Rule", RType.constraint: "Constraint", RType.assumption: "Assumption",
}


class NoChangeRequirements(RuntimeError):
    """Raised when a change pack is requested with no approved requirements to document."""


def _affected(index: dict | None, statement: str) -> list[dict]:
    return impact_for(statement, index) if index else []


def _existing_system_section(understanding: dict | None) -> list[str]:
    if not understanding:
        return ["_No codebase has been attached to this project — attach one to ground the change "
                "in the existing system._"]
    caps = understanding.get("capabilities", [])
    langs = ", ".join(f"{k} ({v})" for k, v in (understanding.get("languages") or {}).items()) or "—"
    out = [understanding.get("summary", ""), "",
           f"**Scanned:** {understanding.get('n_files', 0)} source file(s) · **Languages:** {langs}", ""]
    if caps:
        out += ["| Module | Files | Language | Key components |",
                "|---|---|---|---|"]
        for c in caps[:20]:
            syms = ", ".join(c.get("key_symbols", [])[:8]) or "—"
            out.append(f"| `{md_cell(c['module'])}` | {c['files']} | {md_cell(c['language'])} | {md_cell(syms)} |")
    return out


def _requirements_section(approved: list[Requirement], id_map: dict[str, str]) -> list[str]:
    """The change requirements — functional grouped by feature, then NFR / Business / Constraints /
    Assumptions — each line carrying its change id so the RTM cross-checks line-by-line."""
    out: list[str] = []
    functional = [r for r in approved if r.rtype == RType.functional]
    feats = features_in(functional)
    if feats:
        out.append("### 3.1 Functional changes")
        for i, feat in enumerate(feats, 1):
            out.append(f"#### 3.1.{i} {feat}")
            for r in [x for x in functional if canonical_feature(x.feature) == feat]:
                out.append(f"- **{id_map.get(r.id, '—')}:** {md_line(to_shall_voice(r.statement))}")
    for num, rtype, title in ((2, RType.non_functional, "Non-functional changes"),
                              (3, RType.business, "Business-rule changes"),
                              (4, RType.constraint, "Constraint changes"),
                              (5, RType.assumption, "Assumption changes")):
        items = [r for r in approved if r.rtype == rtype]
        if items:
            out.append(f"### 3.{num} {title}")
            for r in items:
                out.append(f"- **{id_map.get(r.id, '—')}:** {md_line(to_shall_voice(r.statement))}")
    return out or ["_None._"]


def _impact_section(approved: list[Requirement], id_map: dict[str, str], index: dict | None) -> list[str]:
    if not index:
        return ["_Attach a codebase to compute which existing files each change touches._"]
    out: list[str] = []
    any_hit = False
    for r in approved:
        hits = _affected(index, r.statement)
        if not hits:
            continue
        any_hit = True
        files = ", ".join(f"`{h['path']}`" for h in hits)
        out.append(f"- **{id_map.get(r.id, '—')}** → {files}")
    if not any_hit:
        return ["_No existing files matched the change requirements by name — the change may be net-new, "
                "or the impacted modules are named differently; confirm during design._"]
    return ["The following existing files are the most likely to be affected (ranked by name/symbol "
            "overlap — confirm during design):", ""] + out


def _change_srs_markdown(approved: list[Requirement], id_map: dict[str, str], *,
                         project_name: str, date: str, understanding: dict | None,
                         index: dict | None, version: str | None) -> str:
    blocks: list[str] = [
        "[[TITLEPAGE]]",
        "# Change Requirements Specification",
        "for",
        project_name,
        f"Version {version or '1.0'}",
        "Prepared by RGA (Agentic Requirement Gathering & Analysis)",
        f"{date}",
        "A change-only specification: the requirements to be implemented in the existing system.",
        "[[/TITLEPAGE]]",
        "## 1. Introduction",
        f"This document specifies the changes to be implemented in the existing **{project_name}** "
        "system. It covers only the new and changed requirements (not the full system), and grounds "
        "them in the existing codebase so the impact is explicit. It is the Requirements→Design "
        "handoff for this enhancement.",
        "## 2. Existing System Overview",
        "\n".join(_existing_system_section(understanding)),
        "## 3. Change Requirements",
        f"{len(approved)} approved requirement(s) to be implemented, below. Each carries a change id "
        "(REQ-/NFR-/BR-) that the accompanying Change RTM traces to the affected components.",
        "\n".join(_requirements_section(approved, id_map)),
        "## 4. Impact on Existing System",
        "\n".join(_impact_section(approved, id_map, index)),
        "## 5. Assumptions and Notes",
        "- The existing system's current behaviour is unchanged except where a requirement above "
        "specifies otherwise.\n- Affected-file lists are heuristic (name/symbol overlap) and must be "
        "confirmed during design.\n- Full traceability (change id → requirement → affected components) "
        "is in the accompanying **Change RTM**.",
    ]
    return "\n\n".join(b for b in blocks if b) + "\n"


def _change_rtm_csv(approved: list[Requirement], id_map: dict[str, str], index: dict | None) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Change ID", "Requirement", "Type", "Priority", "Affected Components (existing)",
                "Design", "Implementation", "Test Case", "Status"])
    for r in approved:
        hits = _affected(index, r.statement)
        affected = "; ".join(h["path"] for h in hits)
        w.writerow([
            id_map.get(r.id, "—"),
            " ".join(r.statement.split()),
            _RTYPE_LABEL.get(r.rtype, r.rtype.value),
            r.priority.value if r.priority else "",
            affected,
            "", "", "",           # Design / Implementation / Test Case — filled downstream
            "To Do",
        ])
    return buf.getvalue()


def generate_change_pack(
    requirements: list[Requirement],
    *,
    project_name: str = "<Project Name>",
    date: str = "<date>",
    understanding: dict | None = None,
    index: dict | None = None,
    version: str | None = None,
    change_ids: set[str] | None = None,
) -> dict:
    """Compose the brownfield change pack (Change SRS markdown + Change RTM CSV + manifest).

    Scope: when `change_ids` is given, ONLY those requirements are documented (the added/modified
    delta since the last baseline — a focused change spec, not the whole existing spec). When it is
    None, every approved requirement is the change set (a fresh brownfield project). Raises
    `NoChangeRequirements` if the gate is closed or the resulting change set is empty."""
    ok, reason = ready_for_generation(requirements)
    if not ok:
        raise NoChangeRequirements(reason)
    approved = approved_sorted(requirements)
    if change_ids is not None:
        approved = [r for r in approved if r.id in change_ids]
    if not approved:
        raise NoChangeRequirements("no change requirements to document")
    id_map = assign_srs_ids(approved)
    srs_md = _change_srs_markdown(approved, id_map, project_name=project_name, date=date,
                                  understanding=understanding, index=index, version=version)
    rtm_csv = _change_rtm_csv(approved, id_map, index)
    manifest = {
        "project": project_name,
        "generated": date,
        "kind": "change-pack",
        "change_version": version or "1.0",
        "change_requirements": len(approved),
        "scoped_to_changes": change_ids is not None,   # True = delta since baseline; False = all approved
        "codebase_attached": bool(index),
        "codebase_files": (understanding or {}).get("n_files", 0),
        "srs_ids": {
            "functional": sum(1 for v in id_map.values() if v.startswith("REQ-")),
            "non_functional": sum(1 for v in id_map.values() if v.startswith("NFR-")),
            "business": sum(1 for v in id_map.values() if v.startswith("BR-")),
        },
    }
    return {"change_srs_markdown": srs_md, "change_rtm_csv": rtm_csv, "manifest": manifest}
