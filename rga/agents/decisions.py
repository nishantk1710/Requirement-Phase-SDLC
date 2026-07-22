"""Decision clustering — review by DECISION, not by requirement (#3, #6, #7).

A reviewer should face ~15-20 decisions, not 300 rows. This groups the things a human must actually
resolve — conflicts, scope/value calls, gaps, possible misses — into a small set of DECISIONS. Each
decision:
  * asks one question and PROPOSES a resolution with options (propose, don't ask — #6);
  * carries its evidence and the REASON it surfaced (#10);
  * lists the requirements it AFFECTS, so one resolution propagates to all of them (#3);
  * is routed to an OWNER (Finance / CX / Legal / Eng / Product) so the right person confirms (#7).

Requirements that are clean/confident produce no decision — they are auto-approved (#1). Only the
genuinely-uncertain surface here. Deterministic and evidence-backed; the human confirms/overrides.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

from ..eval.dataset import normalize
from .owner import owner_of

_STOP = frozenset(
    "the a an this that these those system platform shall must should may will can could be is are "
    "to of for and or so with on in at by as it its their them they user users customer customers "
    "shopper shoppers product products order orders page pages account accounts data field fields "
    "provide provides support supports allow allows enable enables display show when "
    "where which who via using use each all any not no also able v1 phase feature features requirement "
    "requirements shall's item items scope "
    # scope-framing words (not topic-bearing) so clustering keys on the real subject noun
    "disputed undecided confirmed release included pending decision open current deferred defer "
    "resolved settled maybe tentative approval whether first initial launch need needs needed "
    "before build defined define value option options approach".split()
)

_SCOPE_KINDS = {"out_of_scope", "disputed", "undecided", "deferred"}
_ADD_KINDS = {"gap", "possible_miss"}


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", normalize(text)) if w not in _STOP and len(w) > 3}


def _distinctive(terms: set[str]) -> set[str]:
    """Topic-bearing terms (longer words), used to key clustering + affected-set propagation."""
    return {t for t in terms if len(t) > 4}


def _has_alternatives(text: str) -> bool:
    return bool(re.search(r"\b(vs\.?|versus|either)\b|\b\d+\s*(days?|hours?|units?)\b.*\bor\b", text, re.I))


def _recommend(kind: str) -> tuple[str, list[str]]:
    """The recommended VERDICT (a concrete decision, never a 'decide…' instruction) + its options.
    Recall-first: genuinely-uncertain items (disputed/undecided) and gaps default to INCLUDE/ADD in
    v1; items the source EXPLICITLY excludes or defers keep that honest verdict, with 'Include in v1'
    offered as a prominent alternative. options[0] always mirrors the recommended verdict so an
    'apply recommended' action can key on it."""
    return {
        "out_of_scope": ("Exclude from v1 — source marks it out of scope",
                         ["Exclude from v1", "Include in v1", "Revisit later"]),
        "disputed": ("Include in v1 (confirm wording)",
                     ["Include in v1", "Exclude from v1", "Revisit later"]),
        "undecided": ("Include in v1 (confirm the open value/detail)",
                      ["Include in v1", "Defer to phase 2", "Exclude from v1"]),
        "deferred": ("Defer to phase 2 — source defers it",
                     ["Defer to phase 2", "Include in v1", "Exclude from v1"]),
        "possible_miss": ("Add to v1 as a requirement (verify wording)",
                          ["Add requirement", "Not a requirement"]),
        "gap": ("Add requirement(s) for v1",
                ["Add requirement(s)", "Not needed for v1"]),
        "conflict": ("Keep A — the fuller, more specific statement",
                     ["Keep A", "Keep B", "Keep both with a condition"]),
    }.get(kind, ("Include in v1 (confirm)", ["Accept", "Edit", "Reject"]))


def _did(question: str) -> str:
    return "DEC-" + hashlib.sha1(normalize(question).encode("utf-8")).hexdigest()[:8]


def build_decisions(reqs: list, open_q: list[dict] | None = None) -> list[dict]:
    """Return the deduplicated, owner-routed decision list. Pure/deterministic."""
    open_q = open_q or []
    by_id = {r.id: r for r in reqs}
    decisions: list[dict] = []

    # Term specificity: how many requirements each distinctive term appears in. A scope decision
    # propagates to a requirement ONLY via a SPECIFIC (rare) shared term, so a corpus-common word
    # like "delivery"/"payment"/"tracking" never sweeps unrelated requirements into an exclusion's
    # affected-set (which previously caused real, in-scope requirements to be mass-rejected).
    df: Counter = Counter()
    for r in reqs:
        for t in _distinctive(_terms(r.statement)):
            df[t] += 1
    rare_cap = max(3, len(reqs) // 12)

    def _specific(terms: set[str]) -> set[str]:
        return {t for t in terms if df.get(t, 0) <= rare_cap}

    # 1) CONFLICTS — one decision per pair; recommend the more specific side
    seen: set[tuple] = set()
    for r in reqs:
        for other in (getattr(r, "conflicts_with", None) or []):
            key = tuple(sorted((r.id, other)))
            if key in seen or other not in by_id:
                continue
            seen.add(key)
            a, b = r, by_id[other]
            keep = "Keep A" if len(a.statement) >= len(b.statement) else "Keep B"
            decisions.append({
                "kind": "conflict",
                "question": f"Conflict — these cannot both hold:\n  A) {a.statement}\n  B) {b.statement}",
                # length is a heuristic for specificity, not correctness — say so, don't claim "safer" (L3)
                "recommended": f"{keep} — the fuller, more specific statement (confirm which is correct)",
                "options": ["Keep A", "Keep B", "Keep both with a condition"],
                "evidence": [a.statement, b.statement],
                "affected": [a.id, b.id],
                "owner": owner_of(a.statement, a.feature, a.rtype.value, a.nfr_category),
                "tier": "attention",
                "reason": "mutually-exclusive requirements",
            })

    # 2) SCOPE / VALUE — build raw, then cluster by topic so one call covers all its requirements
    raw_scope: list[dict] = []
    for o in open_q:
        kind = o.get("type", "")
        stmt = (o.get("statement") or "").strip()
        if kind in _SCOPE_KINDS and stmt:
            rec, opts = _recommend(kind)
            raw_scope.append({"kind": kind, "stmt": stmt, "terms": _terms(stmt),
                              "reason": o.get("reason") or kind, "rec": rec, "opts": opts})

    used = [False] * len(raw_scope)
    for i, d in enumerate(raw_scope):
        if used[i]:
            continue
        cluster = [d]
        used[i] = True
        # greedy cluster: add a scope item sharing a distinctive topic term. Precision now comes from
        # the expanded stop-list (generic nouns like customer/product/order are removed), so items
        # only cluster on SPECIFIC topic words ("reviews"), not incidental common ones ("customer").
        for j in range(i + 1, len(raw_scope)):
            if used[j]:
                continue
            if any(_distinctive(c["terms"]) & _distinctive(raw_scope[j]["terms"]) for c in cluster):
                cluster.append(raw_scope[j]); used[j] = True
        # merge the cluster into one decision; propagate to requirements sharing a distinctive topic
        # term (again, generic words are stopped, so a resolution no longer sweeps in a requirement
        # that merely shares a common noun).
        topic_terms: set[str] = set().union(*[c["terms"] for c in cluster])
        # match affected requirements on SPECIFIC topic terms only (rare across the corpus), so a
        # decision never propagates through an incidental common word.
        specific_topic = _specific(_distinctive(topic_terms))
        rep = max(cluster, key=lambda c: len(c["stmt"]))
        affected = [r.id for r in reqs if _distinctive(_terms(r.statement)) & specific_topic]
        is_value = _has_alternatives(rep["stmt"])
        decisions.append({
            "kind": rep["kind"],
            "question": ("Value/approach: " if is_value else "Scope: ") + rep["stmt"],
            "recommended": rep["rec"],
            "options": rep["opts"],
            "evidence": [c["stmt"] for c in cluster][:5],
            "affected": affected,
            "owner": owner_of(rep["stmt"]),
            "tier": "attention",
            "reason": rep["reason"],
        })

    # 3) GAPS / POSSIBLE MISSES — add-or-skip
    for o in open_q:
        kind = o.get("type", "")
        stmt = (o.get("statement") or "").strip()
        if kind in _ADD_KINDS and stmt:
            rec, opts = _recommend(kind)
            decisions.append({
                "kind": kind,
                "question": ("Possible missed requirement: " if kind == "possible_miss" else "Coverage gap: ") + stmt,
                "recommended": rec,
                "options": opts,
                "evidence": [stmt],
                "affected": [],
                "owner": owner_of(stmt),
                "tier": "attention" if kind == "possible_miss" else "review",
                "reason": o.get("reason") or kind,
            })

    # stable ids + de-dupe identical questions
    out: list[dict] = []
    seen_q: set[str] = set()
    for d in decisions:
        did = _did(d["question"])
        if did in seen_q:
            continue
        seen_q.add(did)
        d["id"] = did
        out.append(d)
    # attention first, then review; stable within
    out.sort(key=lambda d: 0 if d["tier"] == "attention" else 1)
    return out


def decision_summary(decisions: list[dict]) -> dict:
    from collections import Counter
    by_owner = Counter(d["owner"] for d in decisions)
    by_tier = Counter(d["tier"] for d in decisions)
    return {"total": len(decisions), "by_owner": dict(by_owner), "by_tier": dict(by_tier)}
