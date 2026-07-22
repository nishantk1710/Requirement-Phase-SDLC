"""The RGA pipeline as a LangGraph StateGraph.

    START → ingest → extract → analyze → rules → review → generate → metrics → baseline → END
                                                    └──(gate closed)──────────────────→ END

`interrupt_before=["review"]` pauses the run at the human gate: the graph stops after
`rules` (stage AWAITING_REVIEW), the human accepts/edits/rejects out-of-band via the P6
review API, then the run is RESUMED (`ainvoke(None, config)`). With a persistent
checkpointer, resume works across a process restart — the kill/resume of TC8.1.

The graph nodes close over `provider` and `repo` (not serialisable, so not in state); the
serialisable run state (ids, counts, stage, manifest, metrics) is what the checkpointer
persists. Domain truth (requirements, decisions) lives in the SQLite store.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..review.gate import ready_for_generation

# The documented lifecycle (FINAL-Plan §orchestration).
STAGES = [
    "PROJECT_CREATED", "INGESTED", "EXTRACTED", "ANALYZED", "RULES_VALIDATED",
    "AWAITING_REVIEW", "APPROVED", "SRS_GENERATED", "RTM_GENERATED", "BASELINED",
]

# Node execution order (for docs/tests); the human gate sits between rules and generate.
run_stage_order = ["ingest", "extract", "analyze", "rules", "review", "generate", "metrics", "baseline"]


class RGAState(TypedDict, total=False):
    project_id: str
    project_name: str
    corpus_dir: str
    date: str
    out_dir: str
    stage: str
    n_chunks: int
    n_extracted: int
    n_open_questions: int
    gate_open: bool
    gate_reason: str
    manifest: dict
    metrics: dict
    traceability_complete: bool
    error: str


def build_graph(
    provider,
    repo,
    *,
    run_critic: bool = False,
    max_passes: int = 1,
    max_workers: int = 4,
    analyze_llm: bool = True,
    generate_narrative: bool = True,
) -> StateGraph:
    """Build (uncompiled) the RGA StateGraph. Compile it with a checkpointer and
    `interrupt_before=["review"]` (see `compile_graph`)."""

    async def ingest(state: RGAState) -> dict:
        from ..ingest.pipeline import ingest_corpus_to_store
        from ..models import Project

        pid = state["project_id"]
        await repo.save_project(Project(id=pid, name=state.get("project_name", pid), status="PROJECT_CREATED"))
        ing = await ingest_corpus_to_store(state["corpus_dir"], repo, pid)
        n = sum(len(v) for v in ing.values())
        return {"stage": "INGESTED", "n_chunks": n}

    async def extract(state: RGAState) -> dict:
        from ..agents.pipeline import extract_and_store

        pid = state["project_id"]
        chunks = await repo.list_project_chunks(pid)
        reqs, open_q = await extract_and_store(
            provider, chunks, repo, project_id=pid,
            max_passes=max_passes, run_critic=run_critic, max_workers=max_workers,
        )
        return {"stage": "EXTRACTED", "n_extracted": len(reqs), "n_open_questions": len(open_q)}

    async def analyze(state: RGAState) -> dict:
        # The SHARED analysis phase (rga.agents.analysis.run_analysis) — IDENTICAL to the API path,
        # so `rga run` (CLI) and the UI can never diverge (E-M1). Consolidation/verification/
        # completeness/coverage run here too (they used to be UI-only).
        from ..agents.analysis import run_analysis
        from ..models import AgentRun

        pid = state["project_id"]
        reqs = await repo.list_requirements(pid)
        chunks = await repo.list_project_chunks(pid)
        out = await repo.latest_agent_run_output(pid)
        open_q = (out or {}).get("open_questions", [])
        res = run_analysis(
            provider, reqs, open_q, chunks,
            consolidate_llm=analyze_llm, adversarial=analyze_llm, analyze_llm=analyze_llm,
        )
        # replace the raw extracted set with the converged, analysed canonical set
        await repo.reset_project_requirements(pid)
        for r in res.reqs:
            await repo.save_requirement(r)
        await repo.log_agent_run(AgentRun(
            project_id=pid, agent="analysis", provider=getattr(provider, "name", None),
            status="success", input={"extracted": len(reqs)},
            output={"accepted": len(res.reqs), "open_questions_count": len(res.open_q),
                    "open_questions": res.open_q, "conflicts": len(res.conflicts)},
        ))
        return {"stage": "ANALYZED", "n_extracted": len(res.reqs), "n_open_questions": len(res.open_q)}

    async def rules(state: RGAState) -> dict:
        pid = state["project_id"]
        reqs = await repo.list_requirements(pid)
        ungrounded = [r.id for r in reqs if not r.source_refs]  # anti-hallucination invariant
        upd: dict = {"stage": "AWAITING_REVIEW"}
        if ungrounded:
            upd["error"] = f"ungrounded requirements present: {ungrounded}"
        proj = await repo.get_project(pid)
        if proj is not None:
            proj.status = "AWAITING_REVIEW"
            await repo.save_project(proj)
        return upd

    async def review(state: RGAState) -> dict:
        """Runs on RESUME, after the human triaged requirements via the review API."""
        pid = state["project_id"]
        ok, reason = ready_for_generation(await repo.list_requirements(pid))
        return {"gate_open": ok, "gate_reason": reason, "stage": "APPROVED" if ok else "AWAITING_REVIEW"}

    async def generate(state: RGAState) -> dict:
        from ..generate.handoff import generate_handoff
        from ..util.paths import safe_dir_component

        pid = state["project_id"]
        reqs = await repo.list_requirements(pid)
        out = await repo.latest_agent_run_output(pid)
        oq = (out or {}).get("open_questions", [])
        pack = generate_handoff(
            reqs, project_name=state.get("project_name", pid).strip(), date=state.get("date", "<date>"),
            provider=(provider if generate_narrative else None),
            open_questions=oq, run_narrative=generate_narrative,
        )
        outdir = Path(state.get("out_dir") or ("handoff/" + safe_dir_component(pid)))
        outdir.mkdir(parents=True, exist_ok=True)
        # Design-phase handoff pack = cleaned SRS + cleaned RTM only (Part J); no seed-models /
        # open-questions files (Appendix C lives inside the SRS). manifest.json is operational metadata.
        (outdir / "SRS.md").write_text(pack["srs_markdown"], encoding="utf-8")
        (outdir / "RTM.md").write_text(pack["rtm_markdown"], encoding="utf-8")
        # remove stale pack files from a previous (pre-alignment) run so the on-disk pack is exact
        for stale in ("open-questions.md", "seed-models.md", "open-questions.docx", "seed-models.docx"):
            (outdir / stale).unlink(missing_ok=True)
        import json as _json

        (outdir / "manifest.json").write_text(_json.dumps(pack["manifest"], indent=2), encoding="utf-8")
        # also emit Word (.docx) versions (best-effort; never breaks the .md output)
        from ..generate.docx_export import write_docx_versions

        write_docx_versions(outdir, {
            "SRS.md": pack["srs_markdown"],
            "RTM.md": pack["rtm_markdown"],
        })
        return {
            "stage": "RTM_GENERATED",
            "out_dir": str(outdir),
            "manifest": pack["manifest"],
            "traceability_complete": pack["manifest"]["traceability_complete"],
        }

    async def metrics(state: RGAState) -> dict:
        from .metrics import compute_exit_metrics

        m = await compute_exit_metrics(
            repo, state["project_id"],
            corpus_dir=state.get("corpus_dir"),
            traceability_complete=state.get("traceability_complete"),
        )
        return {"metrics": m}

    async def baseline(state: RGAState) -> dict:
        pid = state["project_id"]
        proj = await repo.get_project(pid)
        if proj is not None:
            proj.status = "BASELINED"
            await repo.save_project(proj)
        return {"stage": "BASELINED"}

    def route_after_review(state: RGAState) -> str:
        return "generate" if state.get("gate_open") else "end"

    g = StateGraph(RGAState)
    for name, fn in [
        ("ingest", ingest), ("extract", extract), ("analyze", analyze), ("rules", rules),
        ("review", review), ("generate", generate), ("metrics", metrics), ("baseline", baseline),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "extract")
    g.add_edge("extract", "analyze")
    g.add_edge("analyze", "rules")
    g.add_edge("rules", "review")
    g.add_conditional_edges("review", route_after_review, {"generate": "generate", "end": END})
    g.add_edge("generate", "metrics")
    g.add_edge("metrics", "baseline")
    g.add_edge("baseline", END)
    return g


def compile_graph(builder: StateGraph, checkpointer):
    """Compile with the human-review interrupt and a checkpointer (resume support)."""
    return builder.compile(checkpointer=checkpointer, interrupt_before=["review"])
