"""A4 — semantic de-duplication.

The lexical pass (`dedupe_requirements`) only merges near-identical wording. Real corpora
restate the same requirement across documents in different words (a backlog item, a BRD
paragraph, an email) — lexical overlap is too low to catch, yet they are the SAME obligation.
This agent judges duplicates by MEANING:

  1. Candidate generation (cheap, deterministic): within each requirement TYPE, connect items
     that are plausibly related by loose lexical similarity. This only narrows the search — it
     decides nothing.
  2. Semantic arbitration (the LLM): for each candidate group, the model groups the statements
     that express the SAME obligation, however differently worded, and is told to keep distinct
     requirements separate when unsure.
  3. Safe merge (in code): keep the fullest statement as the representative, UNION every source
     quote, and record merged ids in `duplicate_of`. Evidence is never dropped; merging only
     happens within a single type; and it runs before human review, so a bad merge is catchable.

Design bounds (documented, not hidden): two true duplicates with almost no shared vocabulary
(< the candidate threshold) won't be compared — this favours precision (never wrongly merging)
over catching every last paraphrase. Merging MAY cross requirement types, because the same
obligation is sometimes captured under different types (e.g. a data-retention rule listed once as
an assumption and once as a constraint); the LLM decides same-obligation, and the surviving type
is the fuller statement's (a human confirms at review).
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

from ..eval.dataset import normalize
from ..llm.base import LLMProvider
from ..models import Requirement
from .pipeline import _OPENQ_KIND_PRIORITY, _merge_refs, _statement_similarity

CANDIDATE_THRESHOLD = 0.45   # loose: "possibly the same" -> goes to the LLM to decide
MAX_GROUP = 40               # cap statements per LLM call (prompt bound)

SEM_SYSTEM = (
    "You de-duplicate software requirements. You are given a numbered list of requirement "
    "statements that are candidates for being duplicates. Group together ONLY the numbers whose "
    "statements express the SAME underlying obligation — even if worded very differently, and even "
    "if they would be classified under different requirement types. Requirements that merely share "
    "vocabulary, or that add a distinct obligation, MUST stay separate. When in doubt, keep them "
    "separate. Omit singletons."
)


class DupGroups(BaseModel):
    groups: list[list[int]] = Field(default_factory=list)  # groups of 1-based item numbers


def _components(idxs: list[int], reqs: list[Requirement], threshold: float) -> list[list[int]]:
    """Connected components of `idxs` where statements are lexically similar >= threshold."""
    parent = {i: i for i in idxs}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(idxs)):
        for b in range(a + 1, len(idxs)):
            i, j = idxs[a], idxs[b]
            if _statement_similarity(reqs[i].statement, reqs[j].statement) >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[int]] = defaultdict(list)
    for i in idxs:
        groups[find(i)].append(i)
    return list(groups.values())


def _llm_groups(provider: LLMProvider, statements: list[str], system: str = SEM_SYSTEM) -> list[list[int]]:
    """Ask the model which of these statements are the same item. Returns 0-based
    local index groups (size >= 2 only)."""
    listing = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(statements))
    user = (
        "Items (candidates that may contain duplicates):\n"
        + listing
        + "\n\nReturn the groups of item numbers that express the SAME thing."
    )
    result = provider.structured(system, user, DupGroups)
    n = len(statements)
    out: list[list[int]] = []
    for g in result.groups:
        local = sorted({x - 1 for x in g if isinstance(x, int) and 1 <= x <= n})
        if len(local) >= 2:
            out.append(local)
    return out


def semantic_dedupe(
    provider: LLMProvider | None,
    reqs: list[Requirement],
    *,
    run_llm: bool = True,
    candidate_threshold: float = CANDIDATE_THRESHOLD,
) -> tuple[list[Requirement], int]:
    """Merge meaning-level duplicate requirements. Returns (deduped, merged_count)."""
    if not run_llm or provider is None or len(reqs) < 2:
        return list(reqs), 0

    kept = list(reqs)
    remove: set[int] = set()
    merged = 0

    # Candidate clusters across ALL requirements (cross-type), then let the LLM confirm which
    # express the same obligation — this catches the same requirement captured under different types.
    for comp in _components(list(range(len(kept))), kept, candidate_threshold):
        if len(comp) < 2:
            continue
        # cap prompt size: process a large component in fixed windows
        for start in range(0, len(comp), MAX_GROUP):
            window = comp[start:start + MAX_GROUP]
            if len(window) < 2:
                continue
            for group in _llm_groups(provider, [kept[i].statement for i in window]):
                members = [window[p] for p in group if window[p] not in remove]
                if len(members) < 2:
                    continue
                rep = max(members, key=lambda i: len(kept[i].statement))  # fullest wording survives
                for i in members:
                    if i == rep:
                        continue
                    _merge_refs(kept[rep], kept[i])
                    if kept[i].id not in kept[rep].duplicate_of:
                        kept[rep].duplicate_of.append(kept[i].id)
                    remove.add(i)
                    merged += 1

    return [r for i, r in enumerate(kept) if i not in remove], merged


# --- open-questions semantic de-duplication ----------------------------------
# The same open item is often restated across many chunks/documents in different words (a return
# window "7 vs 10 days" note appearing six times, "cash on delivery undecided" a dozen times). The
# deterministic near-dup pass only catches near-verbatim wording, so these stacks survive and bloat
# Appendix C. This collapses them by MEANING, keeping the most-decisive kind and fullest statement.
OQ_DEDUP_SYSTEM = (
    "You de-duplicate OPEN QUESTIONS from a requirements review. Each numbered line is a note, an "
    "undecided point, an out-of-scope item, or an unverified statement. Group together ONLY the "
    "numbers that refer to the SAME underlying point, topic, or decision — even if worded very "
    "differently. Items about different topics, features, or decisions MUST stay separate. When in "
    "doubt, keep them separate. Omit singletons."
)


class _Shim:
    """Minimal object exposing `.statement`, so open-question dicts can reuse `_components`."""

    __slots__ = ("statement",)

    def __init__(self, statement: str) -> None:
        self.statement = statement


def semantic_dedupe_open_questions(
    provider: LLMProvider | None,
    open_q: list[dict],
    *,
    run_llm: bool = True,
    candidate_threshold: float = 0.5,
) -> tuple[list[dict], int]:
    """Collapse meaning-level duplicate open-questions. Returns (deduped, merged_count).

    Candidate clusters are formed deterministically, then SORTED by statement so near-identical
    restatements sit in the same LLM window (robust when a broad cluster spans several windows).
    The LLM confirms same-topic groups; the survivor keeps the most-decisive kind and the fullest
    statement, and records how many entries folded into it. No provider -> no-op.
    """
    if not run_llm or provider is None or len(open_q) < 2:
        return list(open_q), 0

    shims = [_Shim(o.get("statement") or "") for o in open_q]
    remove: set[int] = set()
    merged = 0
    for comp in _components(list(range(len(open_q))), shims, candidate_threshold):
        if len(comp) < 2:
            continue
        comp = sorted(comp, key=lambda i: normalize(open_q[i].get("statement") or ""))
        for start in range(0, len(comp), MAX_GROUP):
            window = comp[start:start + MAX_GROUP]
            if len(window) < 2:
                continue
            for group in _llm_groups(provider, [open_q[i].get("statement", "") for i in window], system=OQ_DEDUP_SYSTEM):
                members = [window[p] for p in group if window[p] not in remove]
                if len(members) < 2:
                    continue
                # survivor: most-decisive kind, then the fullest statement
                rep = min(
                    members,
                    key=lambda i: (_OPENQ_KIND_PRIORITY.get(open_q[i].get("type", ""), 99),
                                   -len(open_q[i].get("statement", ""))),
                )
                for i in members:
                    if i == rep:
                        continue
                    if open_q[i].get("merged_refs"):
                        open_q[rep].setdefault("merged_refs", []).extend(open_q[i]["merged_refs"])
                    open_q[rep]["merged_count"] = open_q[rep].get("merged_count", 1) + open_q[i].get("merged_count", 1)
                    remove.add(i)
                    merged += 1

    return [o for i, o in enumerate(open_q) if i not in remove], merged
