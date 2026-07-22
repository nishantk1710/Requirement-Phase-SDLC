"""TC8.1-TC8.3 — End-to-end orchestration (LangGraph) + PoC exit gate.

Goals:
  TC8.1  full run with a human review; kill & resume mid-run — completes end-to-end and is
         RESUMABLE via the checkpointer (a fresh saver instance on the same file = restart).
  TC8.2  exit metrics — recall vs the human bar, ambiguity, acceptance/edit rate, time-saved
         — are all REPORTED (measured, honestly, with pending where the baseline is pending).
  TC8.3  handoff pack matches the Wave-1 scope exactly (manifest present; nothing over-promised).

Driven by the mock provider over a tiny self-contained eval corpus — no API key needed.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from rga.models import Project, ReviewAction
from rga.orchestrator.graph import STAGES, build_graph, compile_graph, run_stage_order
from rga.review.service import apply_decision
from rga.store.db import Database
from rga.store.repository import Repository

CANNED = json.dumps({
    "requirements": [
        {"statement": "The system shall allow a user to log in.", "rtype": "functional",
         "quotes": ["The system shall allow login."], "inferred": False, "rationale": "r", "confidence": 0.9}
    ]
})


def _tiny_corpus(tmp) -> str:
    d = tmp / "corpus"
    (d / "docs").mkdir(parents=True)
    (d / "docs" / "brd.md").write_text(
        "## 3.1 Access\n\nThe system shall allow login. Passwords must be at least 8 characters.\n",
        encoding="utf-8",
    )
    (d / "manifest.json").write_text(
        json.dumps({"docs": [{"doc_id": "brd", "source_type": "brd", "file": "docs/brd.md"}]}), encoding="utf-8")
    (d / "gold.json").write_text(json.dumps({
        "domain": "tiny",
        "requirements": [
            {"id": "G1", "statement": "The system shall allow a user to log in.", "rtype": "functional",
             "implicit": False, "difficulty": ["explicit"],
             "source": [{"doc_id": "brd", "quote": "The system shall allow login."}]}
        ],
    }), encoding="utf-8")
    (d / "splits.json").write_text(json.dumps({"dev": [], "test": ["G1"]}), encoding="utf-8")
    (d / "human_baseline.json").write_text(json.dumps(
        {"recall": 0.0, "minutes": 0.0, "reviewer": "", "notes": "pending", "pending": True}), encoding="utf-8")
    return str(d)


class _MockProvider:
    """Local mock so extraction is deterministic; analyze/narrative are disabled in the graph."""
    name = "mock"
    deployment = None

    def __init__(self):
        self.calls = 0

    def structured(self, system, user, schema, **kw):
        self.calls += 1
        from rga.agents.schemas import ExtractionResult
        if schema is ExtractionResult:
            return ExtractionResult.model_validate_json(CANNED)
        return schema()  # not used (analyze_llm/narrative off)


@pytest_asyncio.fixture
async def repo(tmp_path):
    db = Database(str(tmp_path / "store.db"))
    await db.init()
    try:
        yield Repository(db)
    finally:
        await db.dispose()


def _builder(repo):
    return build_graph(
        _MockProvider(), repo,
        run_critic=False, max_passes=1, max_workers=1, analyze_llm=False, generate_narrative=False,
    )


def _initial(tmp_path, corpus):
    return {
        "project_id": "P-E2E", "project_name": "E2E Demo", "corpus_dir": corpus,
        "date": "2026-07-09", "out_dir": str(tmp_path / "handoff"),
    }


# --- TC8.1 -------------------------------------------------------------------
async def test_tc8_1_pauses_at_review_then_resumes_after_restart(repo, tmp_path):
    corpus = _tiny_corpus(tmp_path)
    cfg = {"configurable": {"thread_id": "P-E2E"}}
    cp_path = str(tmp_path / "checkpoints.sqlite")

    # Phase 1 — run until the human-review interrupt, then STOP.
    async with AsyncSqliteSaver.from_conn_string(cp_path) as cp:
        graph = compile_graph(_builder(repo), cp)
        await graph.ainvoke(_initial(tmp_path, corpus), cfg)
        snap = await graph.aget_state(cfg)
        assert snap.next == ("review",), snap.next        # paused BEFORE review
        assert snap.values["stage"] == "AWAITING_REVIEW"

    # Requirements were extracted + persisted before the pause; handoff not yet produced.
    reqs = await repo.list_requirements("P-E2E")
    assert len(reqs) == 1
    assert not (tmp_path / "handoff" / "SRS.md").exists()

    # Human review out-of-band (the P6 gate): approve.
    await apply_decision(repo, reqs[0].id, ReviewAction.accept, actor="ba@zensar")

    # Phase 2 — a BRAND-NEW checkpointer + graph (simulated process restart) resumes.
    async with AsyncSqliteSaver.from_conn_string(cp_path) as cp2:
        graph2 = compile_graph(_builder(repo), cp2)
        final = await graph2.ainvoke(None, cfg)

    assert final["gate_open"] is True
    assert final["stage"] == "BASELINED"                  # ran to completion after resume
    assert (tmp_path / "handoff" / "SRS.md").exists()
    proj = await repo.get_project("P-E2E")
    assert proj.status == "BASELINED"


async def test_tc8_1_gate_closed_resume_does_not_generate(repo, tmp_path):
    corpus = _tiny_corpus(tmp_path)
    cfg = {"configurable": {"thread_id": "P-NOAPPROVE"}}
    cp_path = str(tmp_path / "cp2.sqlite")
    init = _initial(tmp_path, corpus)
    init["project_id"] = "P-NOAPPROVE"
    cfg2 = {"configurable": {"thread_id": "P-NOAPPROVE"}}

    async with AsyncSqliteSaver.from_conn_string(cp_path) as cp:
        graph = compile_graph(_builder(repo), cp)
        await graph.ainvoke(init, cfg2)
    # resume WITHOUT approving anything -> gate closed -> routes to END, no handoff
    async with AsyncSqliteSaver.from_conn_string(cp_path) as cp2:
        graph2 = compile_graph(_builder(repo), cp2)
        final = await graph2.ainvoke(None, cfg2)
    assert final["gate_open"] is False
    assert final["stage"] == "AWAITING_REVIEW"
    assert not (tmp_path / "handoff" / "SRS.md").exists()


# --- shared full-run helper --------------------------------------------------
async def _run_to_completion(repo, tmp_path, corpus, thread="P-E2E", pid="P-E2E"):
    cfg = {"configurable": {"thread_id": thread}}
    cp_path = str(tmp_path / f"cp_{thread}.sqlite")
    init = _initial(tmp_path, corpus)
    init["project_id"] = pid
    async with AsyncSqliteSaver.from_conn_string(cp_path) as cp:
        graph = compile_graph(_builder(repo), cp)
        await graph.ainvoke(init, cfg)
    for r in await repo.list_requirements(pid):
        await apply_decision(repo, r.id, ReviewAction.accept)
    async with AsyncSqliteSaver.from_conn_string(cp_path) as cp2:
        graph2 = compile_graph(_builder(repo), cp2)
        return await graph2.ainvoke(None, cfg)


# --- TC8.2 -------------------------------------------------------------------
async def test_tc8_2_exit_metrics_all_reported(repo, tmp_path):
    corpus = _tiny_corpus(tmp_path)
    final = await _run_to_completion(repo, tmp_path, corpus)
    m = final["metrics"]
    # recall vs the human bar
    assert m["recall_vs_bar"]["available"] is True
    assert m["recall_vs_bar"]["recall_explicit"] is not None
    assert m["recall_vs_bar"]["go_no_go"]["verdict"] == "pending_human_baseline"
    # acceptance / edit / reject rates
    assert m["review"]["acceptance_rate"] == 1.0 and m["review"]["decisions"] == 1
    # ambiguity + time-saved reported (time pending until the BA baseline)
    assert "ambiguity_flagged" in m
    assert m["time_saved"]["verdict"] == "pending_human_baseline"


# --- TC8.3 -------------------------------------------------------------------
async def test_tc8_3_handoff_pack_matches_wave1_scope(repo, tmp_path):
    corpus = _tiny_corpus(tmp_path)
    final = await _run_to_completion(repo, tmp_path, corpus)
    outdir = tmp_path / "handoff"
    # Part J: the pack is the cleaned SRS + RTM only (+ operational manifest) — no seed-models /
    # open-questions files.
    for f in ("SRS.md", "RTM.md", "manifest.json"):
        assert (outdir / f).exists(), f
    for absent in ("open-questions.md", "seed-models.md", "seed-models.docx", "open-questions.docx"):
        assert not (outdir / absent).exists(), f"pack must not contain {absent}"
    manifest = json.loads((outdir / "manifest.json").read_text())
    assert manifest["traceability_complete"] is True
    assert manifest["approved"] == 1
    # scope_note is Wave-1 and honest: analysis IS applied; analysis models deferred to Design
    assert "Wave-1" in manifest["scope_note"] and "Design phase" in manifest["scope_note"]
    srs = (outdir / "SRS.md").read_text()
    assert "# Software Requirements Specification" in srs
    for section in ("## 4. System Features", "## 5. Other Nonfunctional Requirements", "## Appendix C:"):
        assert section in srs
    # Parts G & L: no diagram syntax, no inline citations anywhere in the SRS
    for banned in ("flowchart", "erDiagram", "-->", "||--o{", "[src:"):
        assert banned not in srs, f"SRS must not contain {banned!r}"


# --- documented lifecycle ----------------------------------------------------
def test_stage_sequence_matches_documented_lifecycle():
    assert STAGES[0] == "PROJECT_CREATED" and STAGES[-1] == "BASELINED"
    assert "AWAITING_REVIEW" in STAGES and "APPROVED" in STAGES
    assert run_stage_order[:4] == ["ingest", "extract", "analyze", "rules"]
    assert "review" in run_stage_order and run_stage_order[-1] == "baseline"
