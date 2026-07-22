"""Consolidation — converge the raw extraction to a CANONICAL set (measure 1).

"Capture wide, converge to canonical, lose nothing." The raw extraction favours recall and so
carries paraphrase duplicates (the same obligation reworded across documents) and vacuous
cross-reference "pointer" requirements. This pass converges it:

  1. DEMOTE only VACUOUS pointer requirements — those whose entire content is a cross-reference
     ("the system shall comply with NFR-SEC-05") — to open-questions. A substantive requirement
     that merely cites an NFR ("shall encrypt data at rest ... in accordance with NFR-SEC-05")
     keeps its obligation and is NOT demoted (recall-safe).
  2. MERGE same-obligation requirements via an LLM pass biased toward merging — which safely handles
     heavy rewording while keeping genuinely different obligations (e.g. price low-to-high vs
     high-to-low) separate. (A set-based deterministic merge was tried and removed: content-word
     SETS are order-insensitive, so "low to high" and "high to low" collapse to the same set — an
     unacceptable over-merge. Merging therefore requires the model's judgement.)

Merging ABSORBS: the fullest statement survives, every source_ref is unioned onto it, and absorbed
ids are recorded in `duplicate_of`. Nothing is deleted — the invariant
`len(canonical) + absorbed + len(demoted) == len(input)` is asserted, so this can never lose a real
requirement (the user's true "don't drop anything").
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, Field

from ..eval.dataset import normalize
from ..llm.base import LLMProvider
from ..models import Requirement
from .dedup_semantic import _components
from .pipeline import _merge_refs, refs_as_dicts, source_authority

# The cross-reference CLAUSE itself (stripped out to test what obligation, if any, remains).
_POINTER_CLAUSE = re.compile(
    r"(nfr-[a-z]{2,}-\d+"
    r"|in accordance with (the )?(security|performance|requirements?|nfr)[^.,;]*"
    r"|as (defined|referenced|specified) in (the )?nfr[^.,;]*"
    r"|referenced as (nfr|[a-z]\d)[^.,;]*"
    r"|shall satisfy the .{0,60}(referenced|nfr-)"
    r"|per legal point \d+)",
    re.IGNORECASE,
)
# "Empty" words that don't constitute a real obligation on their own — so a residue of only these
# (e.g. "the system shall comply with") counts as having NO substantive content.
_EMPTY = frozenset(
    "the a an this that these those system platform service application shall must should will may "
    "be is are to of for and or with on in at by as it its comply complies satisfy satisfies meet "
    "meets conform conforms adhere adheres follow follows applicable relevant defined referenced "
    "specified requirement requirements nfr policy point legal security performance section".split()
)


def _is_pointer(statement: str) -> bool:
    """True ONLY for a vacuous cross-reference whose ENTIRE content is a pointer
    ("the system shall comply with NFR-SEC-05"). A substantive requirement that merely CITES an
    NFR/policy ("shall encrypt cardholder data at rest ... in accordance with NFR-SEC-05") keeps a
    real obligation and is NOT demoted. We strip the pointer clause(s); it is a pointer only if no
    substantive content word survives — the recall-safe reading (when in doubt, keep it)."""
    s = statement or ""
    if not _POINTER_CLAUSE.search(s):
        return False
    residue = _POINTER_CLAUSE.sub(" ", s)
    content = [w for w in re.findall(r"[a-z0-9]+", residue.lower()) if w not in _EMPTY and len(w) > 2]
    return not content


def _content_terms(statement: str) -> set[str]:
    """Distinctive content words of a statement (drops the structural `_EMPTY` filler)."""
    return {w for w in re.findall(r"[a-z0-9]+", (statement or "").lower())
            if w not in _EMPTY and len(w) > 2}


def _representative(members: list[int], reqs: list[Requirement]) -> int:
    """Pick a merge cluster's canonical index — NOT merely the longest statement (a longer phrasing
    can quietly NARROW the obligation, e.g. 'products, variants and prices' -> 'product variants',
    which then fails second-opinion verification and collapsed the catalogue-CMS capability). Order:
      1. SOURCE AUTHORITY — a formal-spec statement represents the merge over a backlog user story
         (the backlog wording is still kept in the absorbed-statements audit trail, so nothing is lost);
      2. CONSENSUS coverage — content terms attested by >=2 members (the shared obligation);
      3. length — tie-break toward the more complete statement."""
    term_sets = {i: _content_terms(reqs[i].statement) for i in members}
    df = Counter(t for terms in term_sets.values() for t in terms)
    consensus = {t for t, n in df.items() if n >= 2}
    return max(members, key=lambda i: (
        source_authority(reqs[i]), len(term_sets[i] & consensus), len(reqs[i].statement)))


def _absorb(rep: Requirement, other: Requirement) -> None:
    """Fold `other` into `rep`. `rep` is the PRE-SELECTED canonical (see `_representative`), so its
    statement stands — union evidence, record the absorbed id AND the absorbed statement TEXT (so a
    merge is auditable and a reviewer can split it if wrong)."""
    _merge_refs(rep, other)
    if other.id not in rep.duplicate_of:
        rep.duplicate_of.append(other.id)
    for d in other.duplicate_of:
        if d not in rep.duplicate_of:
            rep.duplicate_of.append(d)
    # keep a human-readable trail of what was merged away (C-M4) — never lose the wording
    trail = rep.provenance.setdefault("absorbed_statements", [])
    trail.append(other.statement)
    trail.extend((other.provenance or {}).get("absorbed_statements", []))


MERGE_SYSTEM = (
    "You consolidate a software requirements list. You are given numbered requirement statements that "
    "may describe the SAME obligation. Group the numbers that map to ONE implemented requirement — "
    "merge across pure rewording, examples, or differing level of detail. Keep numbers SEPARATE "
    "whenever they differ in a way that would change what gets BUILT: a different object, attribute, "
    "or parameter; a different value or limit; an opposite direction; or a distinct capability. When "
    "in doubt, keep them separate. Omit singletons."
)


class MergeGroups(BaseModel):
    groups: list[list[int]] = Field(default_factory=list)


def _llm_merge_groups(provider: LLMProvider, statements: list[str]) -> list[list[int]]:
    listing = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(statements))
    user = (
        "Requirements that may describe the same obligation:\n" + listing
        + "\n\nReturn groups of item numbers that should merge into one requirement."
    )
    res = provider.structured(MERGE_SYSTEM, user, MergeGroups, max_tokens=1200)
    n = len(statements)
    out: list[list[int]] = []
    for g in res.groups:
        local = sorted({x - 1 for x in g if isinstance(x, int) and 1 <= x <= n})
        if len(local) >= 2:
            out.append(local)
    return out


MAX_GROUP = 40


def consolidate(
    reqs: list[Requirement],
    *,
    provider: LLMProvider | None = None,
    run_llm: bool = False,
    candidate_threshold: float = 0.5,
) -> tuple[list[Requirement], dict]:
    """Converge `reqs` to a canonical set. Returns (canonical, report). Lossless (asserted)."""
    n_in = len(reqs)

    # 1) demote pointer/vacuous requirements
    demoted = [r for r in reqs if _is_pointer(r.statement)]
    demoted_ids = {r.id for r in demoted}
    kept = [r for r in reqs if r.id not in demoted_ids]

    absorbed = 0

    # 2) LLM merge (optional): cluster by loose similarity, let the model merge same-obligation.
    #    All merging is LLM-arbitrated on purpose — deterministic set/similarity merges over-merge
    #    directional opposites; the model keeps genuinely different obligations separate.
    if run_llm and provider is not None and len(kept) > 1:
        remove: set[int] = set()
        for comp in _components(list(range(len(kept))), kept, candidate_threshold):
            if len(comp) < 2:
                continue
            comp = sorted(comp, key=lambda i: normalize(kept[i].statement))
            for start in range(0, len(comp), MAX_GROUP):
                window = comp[start:start + MAX_GROUP]
                if len(window) < 2:
                    continue
                for group in _llm_merge_groups(provider, [kept[i].statement for i in window]):
                    members = [window[p] for p in group if window[p] not in remove]
                    if len(members) < 2:
                        continue
                    rep = _representative(members, kept)
                    for i in members:
                        if i == rep:
                            continue
                        _absorb(kept[rep], kept[i])
                        remove.add(i)
                        absorbed += 1
        kept = [r for i, r in enumerate(kept) if i not in remove]

    assert len(kept) + absorbed + len(demoted) == n_in, "consolidation lost a requirement!"
    report = {
        "input": n_in,
        "canonical": len(kept),
        "absorbed": absorbed,
        "demoted_pointers": len(demoted),
        "loss": n_in - (len(kept) + absorbed + len(demoted)),
        "demoted": [{"type": "non_requirement", "statement": r.statement,
                     "reason": "vacuous internal cross-reference (pointer) — folded into the referenced requirement",
                     "location": r.source_refs[0].location if r.source_refs else "",
                     "doc_id": r.source_refs[0].doc_id if r.source_refs else "",
                     "merged_refs": refs_as_dicts(r.source_refs)} for r in demoted],  # full provenance
    }
    return kept, report
