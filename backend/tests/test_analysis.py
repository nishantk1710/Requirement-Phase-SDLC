"""The SHARED analysis phase (run_analysis) — the single post-extraction pipeline the CLI graph
and the API both call, so they can never diverge (E-M1)."""

from __future__ import annotations

from rga.agents.analysis import run_analysis
from rga.llm.mock import MockProvider
from rga.models import Chunk, Requirement, RType, SourceRef


def _req(rid, stmt, rtype=RType.functional, doc="brd", start=0):
    return Requirement(
        id=rid, project_id="P", statement=stmt, rtype=rtype,
        source_refs=[SourceRef(doc_id=doc, source_type="brd", location="1",
                               raw_quote=stmt, start=start, end=start + len(stmt))],
    )


def test_run_analysis_dedups_prioritises_and_reports_coverage():
    a = _req("EX-1", "The system shall allow a user to log in.")
    b = _req("EX-2", "The system shall allow a user to log in.", doc="jira")  # exact duplicate
    c = _req("EX-3", "The system shall calculate GST on each order.")
    chunk = Chunk(doc_id="brd", project_id="P", source_type="brd", index=0, location="1",
                  text="The system shall allow a user to log in.", start=0, end=40)
    # MockProvider with no script -> analysis LLM calls return empty (deterministic path exercised)
    res = run_analysis(MockProvider(responses=[]), [a, b, c], [], [chunk],
                       consolidate_llm=False, adversarial=False)
    assert len(res.reqs) == 2                                   # exact duplicate merged away
    assert all(r.priority is not None for r in res.reqs)       # MoSCoW assigned to every req
    assert isinstance(res.conflicts, list)
    assert "accounted_pct" in res.coverage                     # coverage floor ran


def test_run_analysis_is_a_noop_on_empty_input():
    res = run_analysis(MockProvider(responses=[]), [], [], [], consolidate_llm=False, adversarial=False)
    assert res.reqs == [] and res.conflicts == []


def test_verify_targeting_sees_informed_triage(monkeypatch):
    """C-M3: adversarial verify must run AFTER per-requirement clarity/priority, so its risk triage
    keys on real signals. We spy on adversarial_verify and assert the reqs it receives already have
    ambiguity flags + priority populated (they would be empty under the old ordering)."""
    import rga.agents.verify as verify_mod

    captured = {}

    def _spy(provider, reqs, open_q, **kw):
        captured["reqs"] = list(reqs)
        return list(reqs), open_q, 0

    monkeypatch.setattr(verify_mod, "adversarial_verify", _spy)
    r = _req("EX-1", "The system shall be fast.")   # 'fast' is a QuARS weak word (flagged deterministically)
    run_analysis(MockProvider(responses=[]), [r], [], [], consolidate_llm=False, adversarial=True, analyze_llm=False)
    got = captured["reqs"][0]
    assert got.quality.ambiguity_flags        # quality already computed when verify is targeted
    assert got.priority is not None           # priority already assigned
