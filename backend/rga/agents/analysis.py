"""The shared ANALYSIS phase — the single place the post-extraction agent pipeline lives, so the
CLI orchestrator (LangGraph) and the API run IDENTICAL analysis and can never diverge (E-M1).

Pure and repo-free: it takes the extracted requirements + open-questions + chunks and returns the
converged/analysed set; the CALLER persists. An optional `progress(stage, message, **extra)`
callback drives UI progress (a no-op otherwise).

Order (recall-first, then precision): lexical dedup -> consolidate (merge same-obligation, demote
vacuous pointers) -> reconcile scope -> detect conflicts -> adversarial second-opinion -> complete-
ness gaps -> coverage floor (nothing silently missed) -> open-question cleanup -> per-requirement
clarity flag + mechanical normalize + MoSCoW priority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..llm.base import LLMProvider
from ..models import Requirement


@dataclass
class AnalysisResult:
    reqs: list[Requirement]
    open_q: list[dict]
    conflicts: list[dict]
    coverage: dict = field(default_factory=dict)


def _noop(*_args, **_kwargs) -> None:
    pass


def run_analysis(
    provider: LLMProvider | None,
    reqs: list[Requirement],
    open_q: list[dict],
    chunks: list,
    *,
    consolidate_llm: bool = True,
    adversarial: bool = True,
    analyze_llm: bool = False,
    progress: Callable[..., None] | None = None,
) -> AnalysisResult:
    """Run the full analysis phase on already-extracted requirements. In-memory only (no repo)."""
    from .ambiguity import flag_requirement
    from .completeness import analyze_completeness
    from .conflict import detect_conflicts
    from .consolidate import consolidate as consolidate_reqs
    from .coverage import coverage_report
    from .dedup_semantic import semantic_dedupe_open_questions
    from .normalize import auto_normalize
    from .pipeline import dedupe_requirements, postprocess_open_questions
    from .prioritize import prioritize
    from .reconcile import reconcile_scope
    from .verify import adversarial_verify

    p = progress or _noop
    open_q = list(open_q)

    # 1) lexical near-duplicate merge (identical/near-identical wording)
    reqs, merged = dedupe_requirements(reqs)

    # 2) converge to a canonical set (LLM merge-biased) + demote vacuous pointer requirements
    p("consolidating", f"Converging {len(reqs)} requirements into a canonical set…")
    reqs, consol = consolidate_reqs(reqs, provider=provider, run_llm=(provider is not None and consolidate_llm))
    open_q += consol["demoted"]
    merged += consol["absorbed"]
    if merged or consol["demoted_pointers"]:
        p("consolidating",
          f"Merged {merged} duplicate(s), demoted {consol['demoted_pointers']} pointer(s); "
          f"{len(reqs)} canonical requirements.")

    # 3) reconcile: withdraw firm requirements the sources actually left non-firm (kept in open items)
    p("reconciling", f"Reconciling {len(reqs)} requirements against open items…")
    reqs, open_q, withdrawn = reconcile_scope(provider, reqs, open_q)
    if withdrawn:
        p("reconciling", f"Withdrew {withdrawn} over-committed requirement(s); {len(reqs)} remain.")

    # 4) contradictions — both sides kept; surfaced for a human + recorded on the requirements
    p("conflicts", f"Checking {len(reqs)} requirements for contradictions…")
    conflicts = detect_conflicts(provider, reqs)
    by_id = {r.id: r for r in reqs}
    for cf in conflicts:
        open_q.append({"type": "conflict", "statement": f"{cf['a']}  ⟷  {cf['b']}",
                       "reason": cf["reason"], "location": "", "doc_id": ""})
        for x, y in ((cf["a_id"], cf["b_id"]), (cf["b_id"], cf["a_id"])):
            rr = by_id.get(x)
            if rr is not None and y not in rr.conflicts_with:
                rr.conflicts_with.append(y)

    # 5) per-requirement clarity flag + mechanical auto-fix + MoSCoW priority — run BEFORE the
    # adversarial pass so its RISK TRIAGE (which keys on ambiguity flags + priority + conflict
    # membership) is fully informed and targets the genuinely high-risk subset, instead of an
    # under-informed guess that over- or mis-selects (C-M3). Conflict membership was set in step 4.
    p("analyzing", f"Clarity + priority agents flagging {len(reqs)} requirements…")
    for r in reqs:
        r.quality = flag_requirement(provider, r.statement, run_llm=analyze_llm)
        auto_normalize(r)
        r.priority, _ = prioritize(r.statement, r.rtype, inferred=r.inferred)

    # 6) adversarial second-opinion over the high-risk subset (precision; refuted -> open items)
    if adversarial:
        p("verifying", "Second-opinion review of high-risk requirements…")
        reqs, open_q, refuted = adversarial_verify(provider, reqs, open_q)
        if refuted:
            p("verifying", f"Second opinion moved {refuted} shaky requirement(s) to open items; {len(reqs)} remain.")

    # 7) completeness gaps
    p("completeness", f"Checking {len(reqs)} requirements for coverage gaps…")
    open_q.extend(analyze_completeness(provider, reqs))

    # 8) coverage floor (recall): every source chunk accounted for; substantive orphans surface
    cov = coverage_report(chunks, reqs, open_q)
    open_q.extend(cov["possible_misses"])
    if cov["possible_misses"]:
        p("coverage",
          f"Coverage {cov['accounted_pct']}% — {len(cov['possible_misses'])} possible miss(es) flagged for review.")

    # 9) clean the open-questions list (deterministic echoes + LLM restatement collapse)
    open_q = postprocess_open_questions(reqs, open_q)
    open_q, oq_merged = semantic_dedupe_open_questions(provider, open_q)
    if oq_merged:
        p("deduping", f"Collapsed {oq_merged} duplicate open item(s); {len(open_q)} remain.")

    return AnalysisResult(reqs=reqs, open_q=open_q, conflicts=conflicts, coverage=cov)
