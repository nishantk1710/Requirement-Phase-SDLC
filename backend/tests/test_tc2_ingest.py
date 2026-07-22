"""TC2.1-TC2.3 — ingestion loads every modality, chunks by structure (never splitting
a requirement), and tags source type deterministically.

Goals:
  TC2.1  all 6 modalities load; every chunk is a verbatim slice with doc_id/type/
         location/offsets; chunks persist and reload intact.
  TC2.2  every gold requirement's source quote sits WHOLLY inside one chunk
         (no requirement is split across chunk boundaries).
  TC2.3  Source-ID is deterministic (two runs identical) and types match the manifest.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from rga.eval.dataset import load_corpus, normalize
from rga.ingest.pipeline import ingest_corpus, ingest_corpus_to_store
from rga.store.db import Database
from rga.store.repository import Repository

CORPUS_DIR = Path(__file__).resolve().parent.parent / "datasets" / "elams"
EXPECTED_MODALITIES = {"brd", "transcript", "email", "form", "jira", "legacy"}


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(CORPUS_DIR)


@pytest.fixture(scope="module")
def ingested():
    return ingest_corpus(CORPUS_DIR, project_id="P-ELAMS")


# --- TC2.1 -------------------------------------------------------------------
def test_tc2_1_all_docs_load_and_chunks_are_verbatim(corpus, ingested):
    assert set(ingested) == set(corpus.docs), "not all documents were ingested"
    assert {c.source_type for chunks in ingested.values() for c in chunks} <= (
        EXPECTED_MODALITIES
    )
    for doc_id, chunks in ingested.items():
        raw = corpus.docs[doc_id].text
        assert chunks, f"{doc_id} produced no chunks"
        for c in chunks:
            assert c.doc_id == doc_id
            assert c.source_type == corpus.docs[doc_id].source_type
            assert c.text.strip(), "empty chunk text"
            assert c.location, "missing location"
            assert 0 <= c.start < c.end <= len(raw)
            # verbatim: the chunk is exactly the slice it claims
            assert c.text == raw[c.start : c.end]


def test_tc2_1_all_six_modalities_present(ingested):
    got = {chunks[0].source_type for chunks in ingested.values() if chunks}
    assert got == EXPECTED_MODALITIES, got


@pytest_asyncio.fixture
async def repo(tmp_path):
    db = Database(str(tmp_path / "ingest.db"))
    await db.init()
    try:
        yield Repository(db)
    finally:
        await db.dispose()


async def test_tc2_1_chunks_persist_and_reload(repo):
    ingested = await ingest_corpus_to_store(CORPUS_DIR, repo, project_id="P-ELAMS")
    for doc_id, chunks in ingested.items():
        reloaded = await repo.list_chunks(doc_id)
        assert len(reloaded) == len(chunks)
        assert [c.text for c in reloaded] == [c.text for c in chunks]
        assert [(c.start, c.end) for c in reloaded] == [(c.start, c.end) for c in chunks]


# --- TC2.2 (the traceability test) ------------------------------------------
def test_tc2_2_every_gold_quote_is_contained_in_one_chunk(corpus, ingested):
    failures = []
    for req in corpus.requirements:
        for src in req["source"]:
            doc_id = src["doc_id"]
            nq = normalize(src["quote"])
            containing = [
                c for c in ingested[doc_id] if nq in normalize(c.text)
            ]
            if len(containing) == 0:
                failures.append(
                    f"{req['id']}: quote split across chunks / not found in {doc_id}: "
                    f"{src['quote'][:60]}..."
                )
    assert not failures, "Requirements not wholly contained in a single chunk:\n" + "\n".join(
        failures
    )


# --- TC2.3 -------------------------------------------------------------------
def test_tc2_3_ingestion_is_deterministic():
    a = ingest_corpus(CORPUS_DIR, project_id="P-ELAMS")
    b = ingest_corpus(CORPUS_DIR, project_id="P-ELAMS")
    assert set(a) == set(b)
    for doc_id in a:
        assert [c.model_dump() for c in a[doc_id]] == [c.model_dump() for c in b[doc_id]]


def test_tc2_3_source_type_matches_manifest(corpus, ingested):
    for doc_id, chunks in ingested.items():
        for c in chunks:
            assert c.source_type == corpus.docs[doc_id].source_type
