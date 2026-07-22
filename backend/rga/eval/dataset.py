"""Evaluation corpus: loader + validators.

The corpus is "ground truth by construction": every gold requirement cites one or
more source spans (`quote`) that appear VERBATIM in the referenced document. The
validators here are what P1's tests assert, and what future phases reuse to score.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Normalisation: tolerate smart quotes / dashes and whitespace differences, but
# otherwise compare content faithfully (case-insensitive substring).
_TRANSLATE = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)


def normalize(text: str) -> str:
    return " ".join(text.translate(_TRANSLATE).split()).casefold()


def quote_in_doc(quote: str, doc_text: str) -> bool:
    return normalize(quote) in normalize(doc_text)


@dataclass
class Doc:
    doc_id: str
    source_type: str
    file: str
    text: str


@dataclass
class Corpus:
    domain: str
    path: Path
    docs: dict[str, Doc]
    requirements: list[dict]
    dev: list[str] = field(default_factory=list)
    test: list[str] = field(default_factory=list)

    # --- convenience ---------------------------------------------------------
    @property
    def ids(self) -> set[str]:
        return {r["id"] for r in self.requirements}

    def by_id(self, req_id: str) -> dict | None:
        return next((r for r in self.requirements if r["id"] == req_id), None)


def load_corpus(corpus_dir: str | Path) -> Corpus:
    root = Path(corpus_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    gold = json.loads((root / "gold.json").read_text(encoding="utf-8"))
    splits_path = root / "splits.json"
    splits = (
        json.loads(splits_path.read_text(encoding="utf-8"))
        if splits_path.exists()
        else {"dev": [], "test": []}
    )

    docs: dict[str, Doc] = {}
    for d in manifest["docs"]:
        text = (root / d["file"]).read_text(encoding="utf-8")
        docs[d["doc_id"]] = Doc(
            doc_id=d["doc_id"], source_type=d["source_type"], file=d["file"], text=text
        )

    return Corpus(
        domain=manifest.get("domain", gold.get("domain", "unknown")),
        path=root,
        docs=docs,
        requirements=gold["requirements"],
        dev=splits.get("dev", []),
        test=splits.get("test", []),
    )


# --- validators --------------------------------------------------------------
def traceability_failures(corpus: Corpus) -> list[dict]:
    """Return every (requirement, source) whose quote is NOT found verbatim in its
    document, or that references an unknown doc. Empty list == fully traceable."""
    failures: list[dict] = []
    for req in corpus.requirements:
        sources = req.get("source", [])
        if not sources:
            failures.append({"id": req["id"], "reason": "no source spans"})
            continue
        for src in sources:
            doc = corpus.docs.get(src["doc_id"])
            if doc is None:
                failures.append(
                    {"id": req["id"], "reason": f"unknown doc_id {src['doc_id']!r}"}
                )
                continue
            if not quote_in_doc(src["quote"], doc.text):
                failures.append(
                    {
                        "id": req["id"],
                        "doc_id": src["doc_id"],
                        "reason": "quote not found verbatim",
                        "quote": src["quote"],
                    }
                )
    return failures


def coverage_report(corpus: Corpus) -> dict:
    modalities = {d.source_type for d in corpus.docs.values()}
    types: set[str] = set()
    difficulties: dict[str, int] = {}
    for req in corpus.requirements:
        types.add(req["rtype"])
        for tag in req.get("difficulty", []):
            difficulties[tag] = difficulties.get(tag, 0) + 1
    nfr_categories = {
        r.get("nfr_category")
        for r in corpus.requirements
        if r.get("nfr_category")
    }
    return {
        "modalities": modalities,
        "types": types,
        "difficulties": difficulties,
        "nfr_categories": nfr_categories,
        "total": len(corpus.requirements),
    }


def referential_failures(corpus: Corpus) -> list[str]:
    """duplicate_of / conflicts_with must reference existing requirement ids."""
    ids = corpus.ids
    problems: list[str] = []
    for req in corpus.requirements:
        dup = req.get("duplicate_of")
        if dup and dup not in ids:
            problems.append(f"{req['id']} duplicate_of unknown {dup}")
        for c in req.get("conflicts_with", []):
            if c not in ids:
                problems.append(f"{req['id']} conflicts_with unknown {c}")
    return problems


def difficulty_index(corpus: Corpus) -> dict[str, list[str]]:
    """tag -> list of requirement ids carrying it."""
    index: dict[str, list[str]] = {}
    for req in corpus.requirements:
        for tag in req.get("difficulty", []):
            index.setdefault(tag, []).append(req["id"])
    return index
