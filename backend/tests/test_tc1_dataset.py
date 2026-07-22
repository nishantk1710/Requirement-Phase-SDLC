"""TC1.1-TC1.4 — the evaluation corpus is correct, complete, and honestly split.

Goals:
  TC1.1  every gold requirement traces to an exact verbatim span in its document.
  TC1.2  coverage: all modalities, all requirement types, all difficulty tags present.
  TC1.3  planted hard cases exist and are catalogued; links are referentially valid.
  TC1.4  dev / test are disjoint and together cover every requirement (sealed hold-out).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rga.eval.dataset import (
    coverage_report,
    difficulty_index,
    load_corpus,
    referential_failures,
    traceability_failures,
)

CORPUS_DIR = Path(__file__).resolve().parent.parent / "datasets" / "elams"

EXPECTED_MODALITIES = {"brd", "transcript", "email", "form", "jira", "legacy"}
EXPECTED_TYPES = {
    "functional",
    "non_functional",
    "business",
    "constraint",
    "assumption",
}
EXPECTED_DIFFICULTIES = {
    "explicit",
    "multi-span",
    "implicit",
    "ambiguous",
    "duplicate",
    "conflicting",
}


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(CORPUS_DIR)


# --- TC1.1 -------------------------------------------------------------------
def test_tc1_1_every_requirement_is_traceable(corpus):
    failures = traceability_failures(corpus)
    assert failures == [], (
        "Some gold requirements are not traceable to a verbatim source span:\n"
        + "\n".join(str(f) for f in failures)
    )


def test_tc1_1_referential_integrity(corpus):
    problems = referential_failures(corpus)
    assert problems == [], "Dangling duplicate_of/conflicts_with: " + "; ".join(problems)


# --- TC1.2 -------------------------------------------------------------------
def test_tc1_2_coverage_complete(corpus):
    rep = coverage_report(corpus)
    assert rep["modalities"] == EXPECTED_MODALITIES, rep["modalities"]
    assert EXPECTED_TYPES <= rep["types"], rep["types"]
    assert EXPECTED_DIFFICULTIES <= set(rep["difficulties"]), rep["difficulties"]
    assert rep["total"] >= 30, f"corpus too small: {rep['total']}"
    # a few distinct ISO/IEC 25010 NFR categories are represented
    assert len(rep["nfr_categories"]) >= 3, rep["nfr_categories"]


def test_tc1_2_hard_difficulties_have_enough_items(corpus):
    rep = coverage_report(corpus)
    d = rep["difficulties"]
    assert d.get("multi-span", 0) >= 2
    assert d.get("implicit", 0) >= 3
    assert d.get("ambiguous", 0) >= 2
    assert d.get("duplicate", 0) >= 2
    assert d.get("conflicting", 0) >= 2


# --- TC1.3 -------------------------------------------------------------------
def test_tc1_3_duplicates_and_conflicts_are_linked(corpus):
    idx = difficulty_index(corpus)
    for rid in idx.get("duplicate", []):
        assert corpus.by_id(rid)["duplicate_of"], f"{rid} lacks duplicate_of"
    for rid in idx.get("conflicting", []):
        assert corpus.by_id(rid)["conflicts_with"], f"{rid} lacks conflicts_with"


def test_tc1_3_hard_cases_are_catalogued(corpus):
    catalog = (CORPUS_DIR / "catalog.md").read_text(encoding="utf-8")
    idx = difficulty_index(corpus)
    hard_ids = {
        rid
        for tag in ("multi-span", "implicit", "ambiguous", "duplicate", "conflicting")
        for rid in idx.get(tag, [])
    }
    missing = [rid for rid in hard_ids if rid not in catalog]
    assert not missing, f"hard cases missing from catalog.md: {missing}"


# --- TC1.4 -------------------------------------------------------------------
def test_tc1_4_split_is_disjoint_and_complete(corpus):
    dev, test = set(corpus.dev), set(corpus.test)
    assert dev.isdisjoint(test), f"overlap: {dev & test}"
    assert dev | test == corpus.ids, {
        "missing_from_splits": corpus.ids - (dev | test),
        "unknown_in_splits": (dev | test) - corpus.ids,
    }
    # sealed test set is a meaningful fraction (not trivially tiny/huge)
    frac = len(test) / len(corpus.ids)
    assert 0.2 <= frac <= 0.4, f"test fraction {frac:.2f} outside 0.2-0.4"


def test_tc1_4_both_splits_contain_hard_cases(corpus):
    idx = difficulty_index(corpus)
    hard = {
        rid
        for tag in ("multi-span", "implicit", "ambiguous", "duplicate", "conflicting")
        for rid in idx.get(tag, [])
    }
    dev, test = set(corpus.dev), set(corpus.test)
    assert hard & dev, "dev has no hard cases"
    assert hard & test, "test has no hard cases"
