"""Handoff pack composer — ties the P7 generators together behind the P6 gate.

Produces the Wave-1 Requirements→Design handoff: SRS (IEEE-830) + RTM + open-questions +
DRAFT seed models + a manifest. Generation is REFUSED unless the review gate is open
(every requirement triaged, ≥1 approved), so an un-reviewed batch can never be handed off.

Honest scope (Wave-1): NO completeness/conflict analysis and NO refined models — those are
Wave-2. The pack says so in its manifest.
"""

from __future__ import annotations

from ..llm.base import LLMProvider
from ..models import Requirement
from ..review.gate import approved_only, counts, ready_for_generation
from .common import approved_sorted
from .open_questions import compile_open_questions, reconcile_open_questions
from .rtm import build_rtm, rtm_markdown, traceability_check
from .srs import generate_srs
from .srs_template import assign_srs_ids


class GateNotOpen(RuntimeError):
    """Raised when generation is attempted before the human review gate is open."""


class TraceabilityIncomplete(RuntimeError):
    """Raised when an approved requirement has no source — an untraceable batch must not be handed
    off (traceability is ENFORCED here, not merely reported in the manifest)."""


def generate_handoff(
    requirements: list[Requirement],
    *,
    project_name: str = "<Project Name>",
    date: str = "<date>",
    provider: LLMProvider | None = None,
    open_questions: list[dict] | None = None,
    run_narrative: bool = True,
    tech_stack: dict | None = None,
    tech_stack_selection: dict | None = None,   # aspect key -> chosen candidate name
) -> dict:
    """Compose the handoff pack. Raises `GateNotOpen` if the gate is closed.

    If a `provider` is given and `run_narrative` is True, the prose sections are LLM-drafted;
    otherwise they render as `[TBD - Design/BA input]`. Everything else is deterministic.
    """
    ok, reason = ready_for_generation(requirements)
    if not ok:
        raise GateNotOpen(reason)

    approved = approved_sorted(requirements)
    id_map = assign_srs_ids(approved)

    narrative: dict[str, str] = {}
    if provider is not None and run_narrative:
        from ..agents.narrative import draft_narrative

        narrative = draft_narrative(provider, project_name, approved)

    # §3.1.1–3.1.5 design tokens (Parts B/C) — always populated (deterministic without a provider),
    # clearly marked provisional. LLM only refines the brand personality when narrative is on.
    from .design_tokens import generate_design_tokens

    design = generate_design_tokens(approved, provider=provider, project_name=project_name,
                                    run_llm=(provider is not None and run_narrative))

    oq = compile_open_questions(reconcile_open_questions(open_questions or [], approved))   # Part H
    srs_md = generate_srs(
        approved,
        project_name=project_name,
        date=date,
        narrative=narrative,
        open_questions=open_questions or [],
        tech_stack=tech_stack,
        tech_stack_selection=tech_stack_selection,
        design_tokens=design,
    )
    rtm_rows = build_rtm(approved, id_map)
    trace = traceability_check(approved)
    if not trace["complete"]:
        # ENFORCE the traceability invariant at the point it matters — never hand off an approved
        # requirement that lacks a source / RTM row / SRS section (E-M4).
        raise TraceabilityIncomplete(
            "cannot generate an untraceable handoff — "
            f"{len(trace['requirements_without_source'])} approved requirement(s) without a source "
            f"{trace['requirements_without_source']}, "
            f"{len(trace['missing_rows'])} missing RTM row(s), "
            f"{len(trace['rows_without_section'])} row(s) without an SRS section."
        )

    manifest = {
        "project": project_name,
        "generated": date,
        "counts": counts(requirements),
        "approved": len(approved),
        "srs_ids": {"functional": _n(id_map, "REQ"), "non_functional": _n(id_map, "NFR"), "business": _n(id_map, "BR")},
        "rtm_rows": len(rtm_rows),
        "open_questions": len(oq),
        "tech_stack": {
            "stated_in_inputs": bool((tech_stack or {}).get("stated_in_inputs")),
            "aspects": len((tech_stack or {}).get("aspects", [])),
            "selections": len(tech_stack_selection or {}),
        },
        "traceability_complete": trace["complete"],
        "scope_note": (
            "Wave-1 handoff: the cleaned SRS + cleaned RTM. Requirements carry full source "
            "traceability (in the RTM) and human-reviewed acceptance, with consolidation, conflict "
            "detection and completeness/coverage analysis applied. Analysis models are deferred to "
            "the Design phase (Appendix B names them; no seed diagrams are emitted)."
        ),
    }
    return {
        "srs_markdown": srs_md,
        "rtm_rows": rtm_rows,
        "rtm_markdown": rtm_markdown(rtm_rows),
        "open_questions": oq,
        "traceability": trace,
        "manifest": manifest,
        "approved": approved_only(requirements),
    }


def _n(id_map: dict[str, str], prefix: str) -> int:
    return sum(1 for v in id_map.values() if v.startswith(prefix + "-"))
