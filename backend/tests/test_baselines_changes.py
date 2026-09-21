"""Agile iteration loop: baselines + change/delta detection + versioned SRS Revision History.

Additive over the sequential flow — a first generate creates baseline v1; later requirement edits
show up as a delta vs that baseline, and the next generate becomes v2 with a growing Revision
History. Existing behaviour (generate_srs with no version/revision args) is unchanged.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from rga.api.app import create_app
from rga.generate.srs import generate_srs
from rga.models import Priority, Project, Requirement, ReviewAction, RType, SourceRef, Status
from rga.review.changes import compute_changes, delta_reason, snapshot_approved
from rga.review.service import apply_decision
from rga.store.db import Database
from rga.store.repository import Repository


def _r(rid, s, t="functional", *, feature="Core", status=Status.approved, pid="P"):
    return Requirement(id=rid, project_id=pid, statement=s, rtype=RType(t), feature=feature,
                       priority=Priority.must, status=status,
                       source_refs=[SourceRef(doc_id="brd", source_type="brd", location="1",
                                              raw_quote=s, start=0, end=len(s))])


# ---------------------------------------------------------------- pure delta
def test_snapshot_and_compute_changes_classifies_the_delta():
    base_reqs = [_r("a", "The system shall let a customer log in."),
                 _r("b", "The system shall let a customer log out."),
                 _r("c", "The system shall show order history.")]
    snap = snapshot_approved(base_reqs)
    assert len(snap) == 3 and snap[0]["id"] and snap[0]["srs_id"].startswith("REQ-")

    # edit 'b' (same id, new text), keep 'a', drop 'c' (rejected), add 'd'
    current = [
        _r("a", "The system shall let a customer log in."),                 # unchanged
        _r("b", "The system shall let a customer log out securely."),       # modified (same id)
        _r("c", "The system shall show order history.", status=Status.rejected),  # removed
        _r("d", "The system shall let a customer reset their password."),   # added
    ]
    d = compute_changes(current, snap)
    assert d["summary"] == {"added": 1, "modified": 1, "removed": 1, "unchanged": 1}
    assert d["added"][0]["id"] == "d"
    assert d["modified"][0]["id"] == "b" and d["modified"][0]["before"].endswith("log out.")
    assert d["removed"][0]["id"] == "c"
    assert d["total_changes"] == 3


def test_delta_reason_wording():
    d = compute_changes([_r("a", "X"), _r("b", "Y-new")], snapshot_approved([_r("a", "X"), _r("b", "Y")]))
    assert delta_reason(d) == "Revised — 1 modified."
    assert delta_reason({"summary": {"added": 0, "modified": 0, "removed": 0, "unchanged": 5}}) \
        == "Revised — no requirement changes."


# ---------------------------------------------------------------- SRS versioning (additive)
def test_srs_version_defaults_unchanged():
    md = generate_srs([_r("a", "The system shall log in.")], project_name="P", date="2026-01-01")
    assert "Version 1.0 approved" in md                       # default title-page version
    assert "| RGA | 2026-01-01 | Initial draft generated from approved requirements | 0.1 |" in md


def test_srs_version_and_revision_rows_render_when_supplied():
    rows = [{"name": "RGA", "date": "2026-01-01", "reason": "Initial draft", "version": "1.0"},
            {"name": "RGA", "date": "2026-02-01", "reason": "Revised — 1 modified.", "version": "2.0"}]
    md = generate_srs([_r("a", "The system shall log in.")], project_name="P", date="2026-02-01",
                      srs_version="2.0", revision_rows=rows)
    assert "Version 2.0 approved" in md
    assert "| RGA | 2026-01-01 | Initial draft | 1.0 |" in md
    assert "| RGA | 2026-02-01 | Revised — 1 modified. | 2.0 |" in md
    assert "0.1 |" not in md                                  # the hard-coded default row is gone


# ---------------------------------------------------------------- store + endpoints
@pytest_asyncio.fixture
async def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                               # handoff/ writes under tmp_path
    db = Database(str(tmp_path / "bl.db"))
    await db.init()
    r = Repository(db)
    await r.save_project(Project(id="P-BL", name="BL"))
    try:
        yield r
    finally:
        await db.dispose()


@pytest_asyncio.fixture
async def client(repo):
    async with AsyncClient(transport=ASGITransport(app=create_app(repo)), base_url="http://t") as ac:
        yield ac


@pytest.mark.asyncio
async def test_baseline_repo_roundtrip(repo):
    assert await repo.latest_baseline("P-BL") is None
    await repo.save_baseline("P-BL", 1, "Initial draft", snapshot_approved([_r("a", "X")]))
    await repo.save_baseline("P-BL", 2, "Revised — 1 added.", snapshot_approved([_r("a", "X"), _r("b", "Y")]))
    latest = await repo.latest_baseline("P-BL")
    assert latest["version"] == 2 and len(latest["snapshot"]) == 2
    hist = await repo.list_baselines("P-BL")
    assert [b["version"] for b in hist] == [1, 2] and hist[0]["reason"] == "Initial draft"


@pytest.mark.asyncio
async def test_changes_endpoint_empty_without_baseline(client):
    r = await client.get("/api/projects/P-BL/changes")
    assert r.status_code == 200
    j = r.json()
    assert j["has_baseline"] is False and j["total_changes"] == 0


@pytest.mark.asyncio
async def test_changes_endpoint_reports_delta_after_baseline(client, repo):
    for req in (_r("a", "The system shall log in.", pid="P-BL"),
                _r("b", "The system shall log out.", pid="P-BL")):
        await repo.save_requirement(req)
    await repo.save_baseline("P-BL", 1, "Initial draft",
                             snapshot_approved([_r("a", "The system shall log in."),
                                                _r("b", "The system shall log out.")]))
    # edit 'b' in the live set
    await repo.save_requirement(_r("b", "The system shall log out securely.", pid="P-BL"))
    j = (await client.get("/api/projects/P-BL/changes")).json()
    assert j["has_baseline"] is True and j["baseline_version"] == 1
    assert j["summary"]["modified"] == 1 and j["modified"][0]["id"] == "b"
    assert j["review_complete"] is True                    # all approved -> delta is final


@pytest.mark.asyncio
async def test_changes_endpoint_holds_delta_until_review_complete(client, repo):
    """A fresh run leaves un-reviewed `candidate` requirements; the delta is provisional until review
    is done, so `review_complete` is False (the UI holds the delta) until nothing is pending."""
    await repo.save_requirement(_r("a", "The system shall log in.", pid="P-BL"))
    await repo.save_baseline("P-BL", 1, "Initial draft",
                             snapshot_approved([_r("a", "The system shall log in.")]))
    # a freshly-extracted, un-reviewed requirement -> batch not ready -> delta provisional
    await repo.save_requirement(_r("z", "The system shall send a receipt.", status=Status.candidate, pid="P-BL"))
    j = (await client.get("/api/projects/P-BL/changes")).json()
    assert j["has_baseline"] is True and j["review_complete"] is False
    # once it is reviewed (approved), the delta becomes final
    await repo.save_requirement(_r("z", "The system shall send a receipt.", pid="P-BL"))
    j2 = (await client.get("/api/projects/P-BL/changes")).json()
    assert j2["review_complete"] is True


@pytest.mark.asyncio
async def test_add_requirement_endpoint_honours_feature_and_priority(client, repo):
    r = await client.post("/api/projects/P-BL/requirements", json={
        "statement": "The system shall export an audit log.",
        "rtype": "functional", "feature": "Auditing", "priority": "should"})
    assert r.status_code == 200
    j = r.json()
    assert j["added"] is True and j["id"].startswith("HU-")
    added = next(x for x in await repo.list_requirements("P-BL") if x.id == j["id"])
    assert added.status == Status.approved                    # human-authored -> created approved
    assert added.feature == "Auditing" and added.priority.value == "should"


@pytest.mark.asyncio
async def test_add_non_functional_requirement_files_by_category(client, repo):
    r = await client.post("/api/projects/P-BL/requirements", json={
        "statement": "The system shall encrypt customer data at rest.",
        "rtype": "non_functional", "nfr_category": "security"})
    assert r.status_code == 200
    added = next(x for x in await repo.list_requirements("P-BL") if x.id == r.json()["id"])
    assert added.rtype.value == "non_functional" and added.nfr_category == "security"
    assert added.feature is None                              # NFRs are not grouped by feature


@pytest.mark.asyncio
async def test_added_requirement_shows_as_new_in_delta(client, repo):
    await repo.save_requirement(_r("a", "The system shall log in.", pid="P-BL"))
    await repo.save_baseline("P-BL", 1, "Initial draft",
                             snapshot_approved([_r("a", "The system shall log in.")]))
    r = await client.post("/api/projects/P-BL/requirements",
                          json={"statement": "The system shall export an audit log."})
    assert r.status_code == 200
    j = (await client.get("/api/projects/P-BL/changes")).json()
    assert j["review_complete"] is True and j["summary"]["added"] == 1
    assert j["added"][0]["statement"].startswith("The system shall export")


async def _wait_generate(client, pid, timeout=20.0):
    for _ in range(int(timeout / 0.1)):
        st = (await client.get(f"/api/projects/{pid}/generate-status")).json()
        if st.get("state") in ("done", "error"):
            return st
        await asyncio.sleep(0.1)
    return {"state": "timeout"}


@pytest.mark.asyncio
async def test_generate_creates_baseline_v1_then_v2_with_revision_history(client, repo):
    for req in (_r("a", "The system shall apply GST at checkout.", pid="P-BL"),
                _r("b", "The system shall resolve each SKU variant.", pid="P-BL")):
        await repo.save_requirement(req)

    # v1
    assert (await client.post("/api/projects/P-BL/generate")).status_code == 200
    assert (await _wait_generate(client, "P-BL"))["state"] == "done"
    assert [b["version"] for b in (await client.get("/api/projects/P-BL/baselines")).json()["baselines"]] == [1]
    srs_v1 = (await client.get("/api/projects/P-BL/artifacts/SRS.md")).text
    assert "Version 1.0 approved" in srs_v1

    # edit a requirement, then regenerate -> v2
    await repo.save_requirement(_r("b", "The system shall resolve each SKU variant to a specific SKU.", pid="P-BL"))
    ch = (await client.get("/api/projects/P-BL/changes")).json()
    assert ch["summary"]["modified"] == 1
    assert (await client.post("/api/projects/P-BL/generate")).status_code == 200
    assert (await _wait_generate(client, "P-BL"))["state"] == "done"
    versions = [b["version"] for b in (await client.get("/api/projects/P-BL/baselines")).json()["baselines"]]
    assert versions == [1, 2]
    srs_v2 = (await client.get("/api/projects/P-BL/artifacts/SRS.md")).text
    assert "Version 2.0 approved" in srs_v2 and "| 2.0 |" in srs_v2 and "| 1.0 |" in srs_v2


@pytest.mark.asyncio
async def test_editing_approved_requirement_reopens_only_after_baseline(repo):
    """Change-control: a statement edit re-opens an approved requirement for re-approval ONLY once
    a baseline exists; pre-generation edits stay approved, and metadata-only edits keep approval."""
    await repo.save_requirement(_r("a", "The system shall log in.", pid="P-BL"))

    # no baseline yet -> editing an approved requirement keeps it approved (first-pass flow intact)
    r, _ = await apply_decision(repo, "a", ReviewAction.edit,
                                edits={"statement": "The system shall log in with SSO."})
    assert r.status == Status.approved

    # freeze a baseline, then change the statement -> re-opened (needs_review, blocks the gate)
    await repo.save_baseline("P-BL", 1, "Initial draft", snapshot_approved([r]))
    r2, _ = await apply_decision(repo, "a", ReviewAction.edit,
                                 edits={"statement": "The system shall log in with SSO and MFA."})
    assert r2.status == Status.needs_review

    # re-approve, then a metadata-only edit (same statement) keeps the approval
    r3, _ = await apply_decision(repo, "a", ReviewAction.accept)
    assert r3.status == Status.approved
    r4, _ = await apply_decision(repo, "a", ReviewAction.edit, edits={"feature": "Auth"})
    assert r4.status == Status.approved


@pytest.mark.asyncio
async def test_regenerate_without_changes_keeps_same_version(client, repo):
    """Clicking Generate again with no requirement changes must NOT bump the version or add a
    pointless 'no changes' revision — the version stays put and the baseline is refreshed in place."""
    await repo.save_requirement(_r("a", "The system shall apply GST at checkout.", pid="P-BL"))

    assert (await client.post("/api/projects/P-BL/generate")).status_code == 200
    assert (await _wait_generate(client, "P-BL"))["state"] == "done"
    assert [b["version"] for b in (await client.get("/api/projects/P-BL/baselines")).json()["baselines"]] == [1]

    # regenerate with nothing changed -> still v1, still one baseline row, SRS unchanged (v1.0)
    assert (await client.post("/api/projects/P-BL/generate")).status_code == 200
    assert (await _wait_generate(client, "P-BL"))["state"] == "done"
    assert [b["version"] for b in (await client.get("/api/projects/P-BL/baselines")).json()["baselines"]] == [1]
    srs = (await client.get("/api/projects/P-BL/artifacts/SRS.md")).text
    assert "Version 1.0 approved" in srs and "Version 2.0" not in srs
    assert "no requirement changes" not in srs
