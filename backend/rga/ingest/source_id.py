"""Deterministic Source-ID.

The source_type is NOT guessed by an LLM. It comes from the intake metadata (the
manifest / upload form) as the source of truth, with a plain extension-based fallback.
This is pure, reproducible code.
"""

from __future__ import annotations

from pathlib import Path

# Extension -> source_type, used ONLY as a fallback when no type was declared.
_EXT_FALLBACK = {
    ".csv": "form",
    ".json": "jira",
    ".eml": "email",
    ".pdf": "brd",
    ".docx": "brd",
}


def infer_source_type(path: str | Path) -> str | None:
    return _EXT_FALLBACK.get(Path(path).suffix.lower())


def identify(
    doc_id: str, path: str | Path, declared_source_type: str | None = None
) -> dict:
    p = Path(path)
    source_type = declared_source_type or infer_source_type(p) or "unknown"
    return {
        "doc_id": doc_id,
        "source_type": source_type,
        "filename": p.name,
        "ext": p.suffix.lower(),
    }
