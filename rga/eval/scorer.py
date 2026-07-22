"""P4 scorer — measures extraction quality against the gold set using a DETERMINISTIC
span-overlap matching rubric (leveraging the byte-accurate offsets from hardened P3).

For each gold requirement we locate its source span(s) in the document, then check how
well any extracted requirement's cited span covers it:
    coverage = overlap_chars / gold_span_chars   (best across the gold's spans)
    coverage >= CAPTURED_AT  -> captured
    coverage >= PARTIAL_AT   -> partial (counts 0.5)
    else                     -> missed
Recall = (captured + 0.5*partial) / total, reported SEPARATELY for explicit vs implicit
requirements and per difficulty. Precision (source-grounded) = fraction of extracted
requirements that map onto some gold span (a first-order precision signal; true semantic
de-duplication is Wave 2). Recall is always reported with precision — never accuracy alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..agents.grounding import locate_span
from ..models import Requirement
from .dataset import Corpus

CAPTURED_AT = 0.6
PARTIAL_AT = 0.2

DIFFICULTIES = ("explicit", "multi-span", "implicit", "ambiguous", "duplicate", "conflicting")


@dataclass
class Verdict:
    gold_id: str
    status: str  # captured | partial | missed
    coverage: float
    matched_extracted_id: str | None


def _gold_spans(corpus: Corpus) -> dict[str, list[tuple[str, int, int]]]:
    """gold_id -> [(doc_id, start, end)] located in the source documents."""
    out: dict[str, list[tuple[str, int, int]]] = {}
    for req in corpus.requirements:
        spans: list[tuple[str, int, int]] = []
        for s in req["source"]:
            doc = corpus.docs.get(s["doc_id"])
            if doc is None:
                continue
            loc = locate_span(s["quote"], doc.text)
            if loc:
                spans.append((s["doc_id"], loc[0], loc[1]))
        out[req["id"]] = spans
    return out


def _coverage(gold: tuple[str, int, int], ext: tuple[str, int, int]) -> float:
    gd, gs, ge = gold
    ed, es, ee = ext
    if gd != ed:
        return 0.0
    overlap = max(0, min(ge, ee) - max(gs, es))
    return overlap / max(1, ge - gs)


def score(
    extracted: list[Requirement], corpus: Corpus, split_ids: set[str] | None = None
) -> dict:
    gold_spans = _gold_spans(corpus)
    ext_spans = [
        (sr.doc_id, sr.start, sr.end, r.id)
        for r in extracted
        for sr in r.source_refs
        if sr.start is not None and sr.end is not None
    ]
    targets = [
        r for r in corpus.requirements if split_ids is None or r["id"] in split_ids
    ]

    verdicts: dict[str, Verdict] = {}
    matched_ext: set[str] = set()
    for g in targets:
        best, who = 0.0, None
        for span in gold_spans.get(g["id"], []):
            for (ed, es, ee, eid) in ext_spans:
                c = _coverage(span, (ed, es, ee))
                if c > best:
                    best, who = c, eid
        status = (
            "captured" if best >= CAPTURED_AT else "partial" if best >= PARTIAL_AT else "missed"
        )
        verdicts[g["id"]] = Verdict(g["id"], status, round(best, 2), who if best >= PARTIAL_AT else None)
        if who and best >= PARTIAL_AT:
            matched_ext.add(who)

    def recall(items: list[dict]) -> float | None:
        if not items:
            return None
        cap = sum(1 for i in items if verdicts[i["id"]].status == "captured")
        par = sum(1 for i in items if verdicts[i["id"]].status == "partial")
        return round((cap + 0.5 * par) / len(items), 3)

    explicit = [g for g in targets if not g.get("implicit")]
    implicit = [g for g in targets if g.get("implicit")]
    precision = round(len(matched_ext) / len(extracted), 3) if extracted else None

    return {
        "n_target": len(targets),
        "n_extracted": len(extracted),
        "recall_explicit": recall(explicit),
        "recall_implicit": recall(implicit),
        "precision_grounded": precision,
        "per_difficulty": {tag: recall([g for g in targets if tag in g.get("difficulty", [])]) for tag in DIFFICULTIES},
        "captured": sum(1 for v in verdicts.values() if v.status == "captured"),
        "partial": sum(1 for v in verdicts.values() if v.status == "partial"),
        "missed": sum(1 for v in verdicts.values() if v.status == "missed"),
        "verdicts": {gid: vars(v) for gid, v in verdicts.items()},
    }
