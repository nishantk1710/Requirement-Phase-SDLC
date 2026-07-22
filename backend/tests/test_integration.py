"""Integration — extract -> persist -> reload -> score, end to end (mock LLM, no key)."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from rga.agents.pipeline import extract_and_store
from rga.llm.mock import MockProvider
from rga.models import Chunk, Project
from rga.store.db import Database
from rga.store.repository import Repository

CHUNK = Chunk(
    doc_id="d1",
    project_id="P-INT",
    source_type="brd",
    index=0,
    location="3.1",
    text="The system shall allow login. Passwords must be at least 8 characters.",
    start=0,
    end=69,
)


@pytest_asyncio.fixture
async def repo(tmp_path):
    db = Database(str(tmp_path / "int.db"))
    await db.init()
    try:
        yield Repository(db)
    finally:
        await db.dispose()


async def test_extract_and_store_roundtrips_and_audits(repo):
    await repo.save_project(Project(id="P-INT", name="Integration"))
    responses = [
        json.dumps(
            {
                "requirements": [
                    {"statement": "Users can log in.", "rtype": "functional", "quotes": ["The system shall allow login."], "inferred": False, "rationale": "r", "confidence": 0.9},
                    {"statement": "Passwords are 8+ chars.", "rtype": "business", "quotes": ["Passwords must be at least 8 characters."], "inferred": False, "rationale": "r", "confidence": 0.9},
                ]
            }
        )
    ]
    prov = MockProvider(responses=responses)

    reqs, open_q = await extract_and_store(prov, [CHUNK], repo, project_id="P-INT", max_passes=1, run_critic=False)
    assert len(reqs) == 2

    # reload from the store and check persistence + byte-accurate offsets survived
    stored = await repo.list_requirements("P-INT")
    assert len(stored) == 2
    for r in stored:
        assert r.source_refs and r.source_refs[0].start is not None
        sr = r.source_refs[0]
        assert sr.raw_quote == CHUNK.text[sr.start : sr.end]

    # audit trail written
    assert await repo.count_agent_runs("P-INT") == 1
