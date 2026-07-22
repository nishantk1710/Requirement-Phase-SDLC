"""TC6.1-TC6.3 — Human review gate (P6). Driven through the real FastAPI app over an
in-process ASGI transport, against a temp SQLite store. No API key needed.

Goals:
  TC6.1  accept / edit / reject each persist to the decision log with before+after+timestamp;
         an edit changes the statement but KEEPS the requirement id (traceability intact).
  TC6.2  generation is BLOCKED until the gate is open; /generate then returns ONLY approved
         requirements (a rejected or un-reviewed item is never visible to a generator).
  TC6.3  the source quote is available at the point of decision (list + single views).
Plus unit tests of the pure gate rules and service error handling.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from rga.api.app import create_app
from rga.models import AgentRun, Project, Requirement, RType, SourceRef, Status
from rga.review.gate import approved_only, ready_for_generation
from rga.store.db import Database
from rga.store.repository import Repository

PID = "P-REV"


def _req(rid: str, statement: str, quote: str, status: Status = Status.candidate) -> Requirement:
    return Requirement(
        id=rid,
        project_id=PID,
        statement=statement,
        rtype=RType.functional,
        confidence=0.9,
        status=status,
        source_refs=[
            SourceRef(
                doc_id="brd",
                source_type="brd",
                location="3.1",
                raw_quote=quote,
                start=0,
                end=len(quote),
            )
        ],
    )


@pytest_asyncio.fixture
async def repo(tmp_path):
    db = Database(str(tmp_path / "rev.db"))
    await db.init()
    r = Repository(db)
    await r.save_project(Project(id=PID, name="Review"))
    for req in (
        _req("EX-001", "The system shall allow login.", "The system shall allow login."),
        _req("EX-002", "The system shall email the manager.", "email the manager on submission"),
        _req("EX-003", "The system shall export a report.", "export a monthly report"),
    ):
        await r.save_requirement(req)
    try:
        yield r
    finally:
        await db.dispose()


@pytest_asyncio.fixture
async def client(repo):
    app = create_app(repo)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac._repo = repo  # for assertions that reach past HTTP
        ac._app = app    # to reach app.state (e.g. simulate a restart by clearing the artifact cache)
        yield ac


# --- pure gate units ---------------------------------------------------------
def test_gate_approved_only_excludes_rejected_and_pending():
    reqs = [
        _req("a", "s", "q", Status.approved),
        _req("b", "s", "q", Status.rejected),
        _req("c", "s", "q", Status.candidate),
    ]
    assert [r.id for r in approved_only(reqs)] == ["a"]


def test_gate_blocks_until_triaged_then_opens():
    reqs = [_req("a", "s", "q", Status.candidate), _req("b", "s", "q", Status.approved)]
    ok, reason = ready_for_generation(reqs)
    assert ok is False and "awaiting human review" in reason
    reqs[0].status = Status.rejected  # triaged (rejected), one approved remains
    ok, reason = ready_for_generation(reqs)
    assert ok is True
    for r in reqs:
        r.status = Status.rejected  # nothing approved
    ok, reason = ready_for_generation(reqs)
    assert ok is False and "no approved" in reason


# --- TC6.1 -------------------------------------------------------------------
async def test_tc6_1_accept_edit_reject_persist_to_decision_log(client):
    # accept
    r = await client.post("/api/requirements/EX-001/review", json={"action": "accept"})
    assert r.status_code == 200 and r.json()["requirement"]["status"] == "approved"

    # edit — change the statement, keep the id
    r = await client.post(
        "/api/requirements/EX-002/review",
        json={"action": "edit", "edits": {"statement": "The system shall notify the manager by email."}, "actor": "ba@zensar"},
    )
    body = r.json()
    assert r.status_code == 200
    assert body["requirement"]["id"] == "EX-002"  # id is stable across an edit
    assert body["requirement"]["statement"].startswith("The system shall notify")
    assert body["requirement"]["status"] == "approved"

    # reject
    r = await client.post("/api/requirements/EX-003/review", json={"action": "reject"})
    assert r.status_code == 200 and r.json()["requirement"]["status"] == "rejected"

    # every decision is in the log with before/after + timestamp + actor
    for rid, action, actor in (("EX-001", "accept", "reviewer"), ("EX-002", "edit", "ba@zensar"), ("EX-003", "reject", "reviewer")):
        got = (await client.get(f"/api/requirements/{rid}")).json()
        assert len(got["decisions"]) == 1
        d = got["decisions"][0]
        assert d["action"] == action and d["actor"] == actor
        assert d["before"] is not None and d["after"] is not None and d["ts"]

    # the edit's before/after actually differ on the statement
    d = (await client.get("/api/requirements/EX-002")).json()["decisions"][0]
    assert d["before"]["statement"] != d["after"]["statement"]


# --- TC6.2 -------------------------------------------------------------------
async def test_tc6_2_generation_blocked_until_approved_then_only_approved_visible(client):
    # nothing reviewed yet -> gate closed, /generate 409
    gate = (await client.get(f"/api/projects/{PID}/gate")).json()
    assert gate["ready"] is False
    blocked = await client.post(f"/api/projects/{PID}/generate")
    assert blocked.status_code == 409

    # triage all three: approve 2, reject 1
    await client.post("/api/requirements/EX-001/review", json={"action": "accept"})
    await client.post("/api/requirements/EX-002/review", json={"action": "accept"})
    await client.post("/api/requirements/EX-003/review", json={"action": "reject"})

    gate = (await client.get(f"/api/projects/{PID}/gate")).json()
    assert gate["ready"] is True

    # generation now runs in the background: start it, then poll status to completion
    gen = await client.post(f"/api/projects/{PID}/generate")
    assert gen.status_code == 200 and gen.json()["started"] is True
    st = {}
    for _ in range(60):
        st = (await client.get(f"/api/projects/{PID}/generate-status")).json()
        if st["state"] in ("done", "error"):
            break
        await asyncio.sleep(0.05)
    assert st["state"] == "done", st
    assert st["count"] == 2 and "SRS.md" in st["files"]  # only the 2 approved reached generation
    # and only approved are visible downstream
    approved = (await client.get(f"/api/projects/{PID}/requirements?include=approved")).json()
    assert {r["id"] for r in approved["requirements"]} == {"EX-001", "EX-002"}


async def test_tc6_2_generated_artifacts_are_downloadable(client):
    """Regression: GET /artifacts/{name} must serve text (.md) inline AND binary (.docx) as a
    download, from disk — the .docx path always resolves the on-disk file, and after a restart
    (artifact cache cleared) the .md path must fall back to disk. Neither may raise."""
    await client.post("/api/requirements/EX-001/review", json={"action": "accept"})
    await client.post("/api/requirements/EX-002/review", json={"action": "accept"})
    await client.post("/api/requirements/EX-003/review", json={"action": "reject"})
    assert (await client.post(f"/api/projects/{PID}/generate")).status_code == 200
    st = {}
    for _ in range(60):
        st = (await client.get(f"/api/projects/{PID}/generate-status")).json()
        if st["state"] in ("done", "error"):
            break
        await asyncio.sleep(0.05)
    assert st["state"] == "done", st

    # text artifact (served inline from the in-memory cache)
    md = await client.get(f"/api/projects/{PID}/artifacts/SRS.md")
    assert md.status_code == 200 and "Software Requirements Specification" in md.text

    # binary Word artifact path — must resolve via safe_dir_component without raising.
    # 200 with the file when python-docx is installed, else a clean 404 — never a 500/NameError.
    docx = await client.get(f"/api/projects/{PID}/artifacts/SRS.docx")
    assert docx.status_code in (200, 404), (docx.status_code, docx.text)
    if docx.status_code == 200:
        assert docx.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert len(docx.content) > 0

    # simulate a server restart: cache is empty, .md must be read from disk (not 500)
    client._app.state.artifacts.clear()
    md2 = await client.get(f"/api/projects/{PID}/artifacts/SRS.md")
    assert md2.status_code == 200 and "Software Requirements Specification" in md2.text

    # an unknown artifact is a clean 404, not a 500
    missing = await client.get(f"/api/projects/{PID}/artifacts/NOPE.md")
    assert missing.status_code == 404


async def test_tc6_2_include_filter_hides_unapproved(client):
    await client.post("/api/requirements/EX-001/review", json={"action": "accept"})
    approved = (await client.get(f"/api/projects/{PID}/requirements?include=approved")).json()
    assert [r["id"] for r in approved["requirements"]] == ["EX-001"]
    pending = (await client.get(f"/api/projects/{PID}/requirements?include=pending")).json()
    assert {r["id"] for r in pending["requirements"]} == {"EX-002", "EX-003"}


# --- TC6.3 -------------------------------------------------------------------
async def test_tc6_3_source_quote_present_at_decision_point(client):
    listing = (await client.get(f"/api/projects/{PID}/requirements")).json()
    assert listing["counts"]["candidate"] == 3
    for row in listing["requirements"]:
        assert row["sources"] and row["sources"][0]["quote"].strip()
        assert "confidence" in row  # recorded for display, not used to gate
    single = (await client.get("/api/requirements/EX-001")).json()
    assert single["requirement"]["sources"][0]["quote"] == "The system shall allow login."


# --- P5.3: decision resolution (apply-recommended + persistence) -------------
def _cf(rid, stmt, conflicts=None):
    return Requirement(id=rid, project_id=PID, statement=stmt, rtype=RType.functional, confidence=0.9,
                       status=Status.approved, conflicts_with=conflicts or [],
                       source_refs=[SourceRef(doc_id="brd", source_type="brd", location="1",
                                              raw_quote=stmt, start=0, end=len(stmt))])


async def test_apply_recommended_decisions_resolves_persists_and_acts(client):
    repo = client._repo
    await repo.save_requirement(_cf("CF-A", "The checkout flow shall be completed in exactly five steps.", ["CF-B"]))
    await repo.save_requirement(_cf("CF-B", "The checkout flow shall have three steps.", ["CF-A"]))
    await repo.save_requirement(_cf("RV-1", "The system shall display product reviews on the PDP."))
    await repo.log_agent_run(AgentRun(project_id=PID, agent="A1+A0", status="success", output={
        "open_questions": [{"type": "disputed",
                            "statement": "Product reviews are disputed and not confirmed in scope."}]}))

    # decisions start open, nothing resolved
    d0 = (await client.get(f"/api/projects/{PID}/decisions")).json()
    assert d0["open"] >= 2 and d0["resolved"] == 0

    # apply all recommended verdicts in one action
    ap = (await client.post(f"/api/projects/{PID}/decisions/apply-recommended")).json()
    assert ap["applied"]["conflicts"] >= 1 and ap["applied"]["included"] >= 1

    # now resolved + open==0, and DURABLE (a fresh GET recomputes from the store, not memory)
    d1 = (await client.get(f"/api/projects/{PID}/decisions")).json()
    assert d1["resolved"] >= 2 and d1["open"] == 0
    assert all(x["resolved"] for x in d1["decisions"] if x["kind"] in ("conflict", "disputed"))

    # the recommended verdicts were actually applied to requirements
    assert (await repo.get_requirement("CF-A")).status == Status.approved   # kept (fuller statement)
    assert (await repo.get_requirement("CF-B")).status == Status.rejected   # conflict loser rejected
    assert (await repo.get_requirement("RV-1")).status == Status.approved   # disputed -> included


async def test_apply_recommended_does_not_drop_real_reqs_on_a_scope_note(client):
    """FIX 0: an out-of-scope NOTE must not mass-reject the topical affected-set. Only an approved
    requirement that RESTATES the excluded item is dropped; a real, in-scope requirement that merely
    shares a word ('tracking') is KEPT."""
    repo = client._repo
    await repo.save_requirement(_cf("D-KEEP", "The system shall display simulated tracking in the order status timeline."))
    await repo.save_requirement(_cf("D-DROP", "The system shall integrate with live carrier tracking APIs."))
    await repo.log_agent_run(AgentRun(project_id=PID, agent="A1+A0", status="success", output={
        "open_questions": [{"type": "out_of_scope",
                            "statement": "The system shall integrate with live carrier tracking APIs.",
                            "reason": "source marks live carrier integration out of scope"}]}))
    d0 = (await client.get(f"/api/projects/{PID}/decisions")).json()
    scope = next(x for x in d0["decisions"] if x["kind"] == "out_of_scope")
    assert "D-KEEP" in scope["affected"] and "D-DROP" in scope["affected"]   # both topically affected

    await client.post(f"/api/projects/{PID}/decisions/apply-recommended")
    assert (await repo.get_requirement("D-KEEP")).status == Status.approved   # real req KEPT
    assert (await repo.get_requirement("D-DROP")).status == Status.rejected   # restatement dropped


async def test_single_decision_resolve_persists(client):
    repo = client._repo
    await repo.save_requirement(_cf("CF-A", "Checkout shall be completed in exactly five distinct steps.", ["CF-B"]))
    await repo.save_requirement(_cf("CF-B", "Checkout shall have three steps.", ["CF-A"]))
    d0 = (await client.get(f"/api/projects/{PID}/decisions")).json()
    did = next(x["id"] for x in d0["decisions"] if x["kind"] == "conflict")
    r = await client.post(f"/api/projects/{PID}/decisions/{did}/resolve",
                          json={"kind": "conflict", "recommended": "Keep A", "action": "keep-a"})
    assert r.status_code == 200 and r.json()["resolved"] == did
    d1 = (await client.get(f"/api/projects/{PID}/decisions")).json()
    assert next(x for x in d1["decisions"] if x["id"] == did)["resolved"] is True


# --- error handling ----------------------------------------------------------
async def test_review_unknown_requirement_404(client):
    r = await client.post("/api/requirements/EX-999/review", json={"action": "accept"})
    assert r.status_code == 404


async def test_review_uneditable_field_400(client):
    r = await client.post(
        "/api/requirements/EX-001/review",
        json={"action": "edit", "edits": {"confidence": 0.1}},
    )
    assert r.status_code == 400
