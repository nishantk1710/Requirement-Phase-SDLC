"""Ask 1 — the tech-stack review endpoint accepts a reviewer's own "Other" technology.

A custom (Other) value need not be one of the proposed candidates; it is stored as the aspect's
selection and reaches §7. Non-custom picks still validate against the candidate list.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from rga.api.app import create_app
from rga.models import AgentRun, Project
from rga.store.db import Database
from rga.store.repository import Repository

PID = "P-TS"

_TECH_STACK = {
    "stated_in_inputs": False,
    "basis": "No stack fixed in the inputs.",
    "aspects": [
        {"key": "backend", "title": "Backend / API", "rationale": "Where logic runs.",
         "candidates": [
             {"name": "Node.js + Express", "recommended": True, "reason": "simple web API"},
             {"name": "Python + FastAPI", "recommended": False, "reason": "fast to build"},
         ]},
    ],
}


@pytest_asyncio.fixture
async def repo(tmp_path):
    db = Database(str(tmp_path / "ts.db"))
    await db.init()
    r = Repository(db)
    await r.save_project(Project(id=PID, name="TS"))
    await r.log_agent_run(AgentRun(project_id=PID, agent="A1+A0", status="success",
                                   input={"chunks": 1}, output={"tech_stack": _TECH_STACK}))
    try:
        yield r
    finally:
        await db.dispose()


@pytest_asyncio.fixture
async def client(repo):
    transport = ASGITransport(app=create_app(repo))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_select_custom_other_value_is_accepted_and_persisted(client):
    r = await client.post(f"/api/projects/{PID}/tech-stack/select",
                          json={"aspect": "backend", "candidate": "Deno + Hono", "custom": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["selected"] == "Deno + Hono" and body["custom"] is True
    assert body["recommended"] == "Node.js + Express"
    # GET reflects the custom selection for that aspect
    g = await client.get(f"/api/projects/{PID}/tech-stack")
    assert g.json()["selections"]["backend"] == "Deno + Hono"


async def test_select_custom_empty_is_rejected(client):
    r = await client.post(f"/api/projects/{PID}/tech-stack/select",
                          json={"aspect": "backend", "candidate": "   ", "custom": True})
    assert r.status_code == 400


async def test_select_noncustom_unknown_candidate_still_rejected(client):
    # a normal (non-custom) pick must still be one of the proposed candidates
    r = await client.post(f"/api/projects/{PID}/tech-stack/select",
                          json={"aspect": "backend", "candidate": "COBOL", "custom": False})
    assert r.status_code == 400


async def test_select_known_candidate_ok(client):
    r = await client.post(f"/api/projects/{PID}/tech-stack/select",
                          json={"aspect": "backend", "candidate": "Python + FastAPI"})
    assert r.status_code == 200 and r.json()["selected"] == "Python + FastAPI"


async def test_get_filters_out_switched_off_aspects(tmp_path):
    """GET /tech-stack drops Payments and Hosting / Infrastructure from a stored run, so the review
    screen matches the (payments/hosting-free) §7."""
    db = Database(str(tmp_path / "ts2.db"))
    await db.init()
    r = Repository(db)
    await r.save_project(Project(id="P-TS2", name="TS2"))
    stored = {"stated_in_inputs": False, "basis": "b", "aspects": [
        {"key": "backend", "title": "Backend / API", "rationale": "r",
         "candidates": [{"name": "Node.js + Express", "recommended": True, "reason": "x"}]},
        {"key": "payments", "title": "Payments", "rationale": "r",
         "candidates": [{"name": "Stripe", "recommended": True, "reason": "x"}]},
        {"key": "hosting", "title": "Hosting / Infrastructure", "rationale": "r",
         "candidates": [{"name": "Docker", "recommended": True, "reason": "x"}]},
    ]}
    await r.log_agent_run(AgentRun(project_id="P-TS2", agent="A1+A0", status="success",
                                   input={"chunks": 1}, output={"tech_stack": stored}))
    transport = ASGITransport(app=create_app(r))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        g = await ac.get("/api/projects/P-TS2/tech-stack")
    keys = [a["key"] for a in g.json()["tech_stack"]["aspects"]]
    assert keys == ["backend"]                       # payments + hosting filtered out
    await db.dispose()
