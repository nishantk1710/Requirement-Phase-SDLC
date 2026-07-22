"""TC0.3 — Store round-trips domain objects and survives concurrent writes.

Goal:
  * a Requirement (with project_id + source_refs) saves and loads back intact;
  * many concurrent writes all persist with NO "database is locked" error (WAL +
    busy_timeout);
  * the review-decision log records decisions.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from rga.agents.pipeline import _req_id
from rga.models import (
    Chunk,
    Project,
    Requirement,
    ReviewAction,
    ReviewDecision,
    SourceRef,
    Status,
)
from rga.store.db import Database
from rga.store.repository import Repository


@pytest_asyncio.fixture
async def repo(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    await db.init()
    try:
        yield Repository(db)
    finally:
        await db.dispose()


async def test_requirement_roundtrip(repo):
    await repo.save_project(Project(id="P1", name="Demo"))
    req = Requirement(
        id="REQ-1",
        project_id="P1",
        statement="The system shall extract requirements with a source quote.",
        feature="Extraction",
        source_refs=[
            SourceRef(
                doc_id="BRD",
                source_type="brd",
                location="§1.2",
                raw_quote="extract requirements with a source quote",
            )
        ],
    )
    await repo.save_requirement(req)

    got = await repo.get_requirement("REQ-1")
    assert got is not None
    assert got.project_id == "P1"
    assert got.statement == req.statement
    assert got.feature == "Extraction"
    assert got.status == Status.candidate
    assert len(got.source_refs) == 1
    assert got.source_refs[0].raw_quote == "extract requirements with a source quote"


async def test_upsert_replaces_children(repo):
    await repo.save_project(Project(id="P1", name="Demo"))
    r = Requirement(
        id="REQ-1",
        project_id="P1",
        statement="v1",
        source_refs=[SourceRef(doc_id="D", source_type="brd", location="a", raw_quote="q1")],
    )
    await repo.save_requirement(r)
    r.statement = "v2"
    r.source_refs = [SourceRef(doc_id="D", source_type="brd", location="b", raw_quote="q2")]
    await repo.save_requirement(r)

    got = await repo.get_requirement("REQ-1")
    assert got.statement == "v2"
    assert len(got.source_refs) == 1
    assert got.source_refs[0].raw_quote == "q2"


async def test_concurrent_writes_no_lock(repo):
    await repo.save_project(Project(id="P1", name="Demo"))
    reqs = [
        Requirement(id=f"REQ-{i}", project_id="P1", statement=f"requirement {i}")
        for i in range(12)
    ]
    # All writes fired concurrently; WAL + busy_timeout must prevent lock errors.
    await asyncio.gather(*(repo.save_requirement(r) for r in reqs))

    stored = await repo.list_requirements("P1")
    assert len(stored) == 12
    assert {r.id for r in stored} == {f"REQ-{i}" for i in range(12)}


# --- cross-project isolation (data-integrity regression) ---------------------
def test_req_id_is_project_scoped():
    """The same statement in two projects must hash to DIFFERENT ids; same project is stable."""
    stmt = "The system shall require authentication."
    assert _req_id(stmt, "P-A") != _req_id(stmt, "P-B")          # no cross-project collision
    assert _req_id(stmt, "P-A") == _req_id(stmt, "P-A")          # deterministic within a project


async def test_two_projects_same_statement_do_not_clobber(repo):
    """A boilerplate statement extracted in two projects must yield two independent rows."""
    await repo.save_project(Project(id="P-A", name="A"))
    await repo.save_project(Project(id="P-B", name="B"))
    stmt = "The system shall log all user actions."
    a = Requirement(id=_req_id(stmt, "P-A"), project_id="P-A", statement=stmt, status=Status.approved)
    b = Requirement(id=_req_id(stmt, "P-B"), project_id="P-B", statement=stmt, status=Status.candidate)
    await repo.save_requirement(a)
    await repo.save_requirement(b)  # must NOT overwrite / reparent A

    la, lb = await repo.list_requirements("P-A"), await repo.list_requirements("P-B")
    assert [r.id for r in la] == [a.id] and la[0].status == Status.approved   # A intact
    assert [r.id for r in lb] == [b.id] and lb[0].status == Status.candidate  # B intact
    assert a.id != b.id


async def test_save_requirement_refuses_cross_project_reparent(repo):
    """If a PK collision across projects ever occurs, saving must fail LOUD, not silently clobber."""
    await repo.save_project(Project(id="P-A", name="A"))
    await repo.save_project(Project(id="P-B", name="B"))
    await repo.save_requirement(Requirement(id="DUP-1", project_id="P-A", statement="x"))
    with pytest.raises(ValueError, match="already belongs to project"):
        await repo.save_requirement(Requirement(id="DUP-1", project_id="P-B", statement="x"))
    assert [r.id for r in await repo.list_requirements("P-A")] == ["DUP-1"]  # A untouched


async def test_chunks_with_shared_doc_id_isolated_by_project(repo):
    """Two projects sharing a doc_id (both corpora use 'brd') must not delete each other's chunks."""
    def chunk(pid, i):
        return Chunk(doc_id="brd", project_id=pid, source_type="brd", index=i,
                     location=f"§{i}", text=f"chunk {i} for {pid}", start=i * 10, end=i * 10 + 5)
    await repo.save_chunks([chunk("P-A", 0), chunk("P-A", 1)])
    await repo.save_chunks([chunk("P-B", 0)])                    # same doc_id, different project

    assert len(await repo.list_project_chunks("P-A")) == 2       # A's chunks survive B's ingest
    assert len(await repo.list_project_chunks("P-B")) == 1


async def test_reset_project_requirements_clears_reqs_and_decisions_keeps_chunks(repo):
    """A re-run reset clears the project's requirements + their decisions, keeps chunks, and
    never touches another project."""
    await repo.save_project(Project(id="P1", name="Demo"))
    await repo.save_project(Project(id="P2", name="Other"))
    await repo.save_requirement(Requirement(id="R1", project_id="P1", statement="a", status=Status.approved))
    await repo.save_requirement(Requirement(id="R2", project_id="P2", statement="b"))
    await repo.log_review_decision(ReviewDecision(requirement_id="R1", action=ReviewAction.accept))
    await repo.save_chunks([Chunk(doc_id="brd", project_id="P1", source_type="brd", index=0,
                                  location="§1", text="kept", start=0, end=4)])

    removed = await repo.reset_project_requirements("P1")
    assert removed == 1
    assert await repo.list_requirements("P1") == []             # requirements cleared
    assert await repo.list_review_decisions("R1") == []         # their decisions cleared
    assert len(await repo.list_project_chunks("P1")) == 1       # chunks kept (re-ingest replaces)
    assert [r.id for r in await repo.list_requirements("P2")] == ["R2"]  # other project untouched


async def test_decision_resolution_store_roundtrip_and_reset(repo):
    """P5.2: resolutions persist (survive reload), replace idempotently, and clear on re-run."""
    await repo.save_project(Project(id="P1", name="Demo"))
    await repo.save_decision_resolution("P1", "DEC-abc", "conflict", "Keep A", "keep-a", actor="ba")
    await repo.save_decision_resolution("P1", "DEC-xyz", "disputed", "Include in v1", "accept")
    assert await repo.resolved_decision_ids("P1") == {"DEC-abc", "DEC-xyz"}

    # re-resolving the same decision REPLACES (no duplicate row)
    await repo.save_decision_resolution("P1", "DEC-abc", "conflict", "Keep B", "keep-b")
    log = await repo.list_decision_resolutions("P1")
    abc = [r for r in log if r["decision_id"] == "DEC-abc"]
    assert len(abc) == 1 and abc[0]["action"] == "keep-b"

    # a fresh run clears stale resolutions
    await repo.reset_project_requirements("P1")
    assert await repo.resolved_decision_ids("P1") == set()


async def test_reingest_is_idempotent_and_populates_more_than_one(repo):
    """Fix 5 regression: a full (re-)populate leaves the store with the expected count (>1), and
    running it AGAIN after a reset yields the SAME count — never accumulating duplicates and never
    degenerating to a single row. (The earlier "DB has 1 requirement" scare was a measurement-script
    cursor-reuse bug, NOT a store/reset defect; this pins the store invariant so a real regression
    of that shape would fail loudly.)"""
    await repo.save_project(Project(id="P1", name="Demo"))

    def batch():
        return [Requirement(id=f"R{i}", project_id="P1",
                            statement=f"The system shall do thing {i}.", status=Status.approved)
                for i in range(20)]

    for r in batch():
        await repo.save_requirement(r)
    assert len(await repo.list_requirements("P1")) == 20            # populated (count > 1)

    removed = await repo.reset_project_requirements("P1")
    assert removed == 20
    assert await repo.list_requirements("P1") == []                 # reset empties the store

    for r in batch():                                               # re-ingest the same corpus
        await repo.save_requirement(r)
    stored = await repo.list_requirements("P1")
    assert len(stored) == 20                                        # idempotent: same count, not 1, no dupes
    assert len({r.id for r in stored}) == 20


async def test_review_decision_log(repo):
    await repo.save_project(Project(id="P1", name="Demo"))
    await repo.save_requirement(Requirement(id="REQ-1", project_id="P1", statement="x"))
    await repo.log_review_decision(
        ReviewDecision(
            requirement_id="REQ-1",
            action=ReviewAction.edit,
            before={"statement": "x"},
            after={"statement": "x, clarified"},
        )
    )
    decisions = await repo.list_review_decisions("REQ-1")
    assert len(decisions) == 1
    assert decisions[0].action == ReviewAction.edit
    assert decisions[0].after == {"statement": "x, clarified"}
