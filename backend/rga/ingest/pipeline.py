"""Ingestion pipeline: manifest -> load -> Source-ID -> chunk (-> optional persist)."""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Chunk
from ..store.repository import Repository
from .chunker import chunk_document
from .loaders import load_raw
from .source_id import identify


def ingest_document(
    doc_id: str, path: str | Path, source_type: str | None = None, project_id: str | None = None
) -> tuple[dict, list[Chunk]]:
    """Load one document, tag it deterministically, and chunk it. Pure (no persistence)."""
    meta = identify(doc_id, path, source_type)
    raw = load_raw(path)
    chunks = chunk_document(raw, meta["source_type"], doc_id, project_id=project_id)
    return meta, chunks


def ingest_corpus(
    corpus_dir: str | Path, project_id: str | None = None
) -> dict[str, list[Chunk]]:
    """Ingest every document in a corpus `manifest.json`. Returns doc_id -> chunks."""
    root = Path(corpus_dir)
    # utf-8-sig tolerates a BOM (common from Windows editors) that would otherwise break json.loads
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    out: dict[str, list[Chunk]] = {}
    for d in manifest["docs"]:
        # source_type is OPTIONAL — `identify()` falls back to the file extension when it's absent,
        # so a manifest without it degrades gracefully instead of raising KeyError (A-F10).
        _, chunks = ingest_document(
            d["doc_id"], root / d["file"], d.get("source_type"), project_id=project_id
        )
        out[d["doc_id"]] = chunks
    return out


async def ingest_corpus_to_store(
    corpus_dir: str | Path, repo: Repository, project_id: str
) -> dict[str, list[Chunk]]:
    """Ingest and persist chunks to the store."""
    result = ingest_corpus(corpus_dir, project_id=project_id)
    for chunks in result.values():
        await repo.save_chunks(chunks)
    return result
