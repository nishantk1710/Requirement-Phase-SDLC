"""Open-questions / TBD compiler → Appendix C of the SRS.

The extraction pipeline emits open-questions (inferred-without-span, critic-rejected,
conflicts). This turns that raw list into a deduplicated, stably-numbered TBD table so
nothing that needs a human answer is silently lost — it surfaces in the SRS instead.
"""

from __future__ import annotations

from ..eval.dataset import normalize
from .common import md_cell

# Open-question kinds that are "coverage noise" — if an APPROVED requirement already covers them,
# they are redundant and suppressed (Part H #1). GENUINE decisions (conflict/disputed/undecided/
# out-of-scope/deferred) are NEVER suppressed by coverage — they carry a decision that stands even
# when a related requirement is approved.
#
# `critic_rejected` IS coverage-suppressible: a critic rejection of a candidate that near-restates
# an APPROVED requirement is a duplicate the critic happened to reject while the clean twin survived
# — the capability is already in the SRS body, so the rejected copy is redundant noise. It is only
# ever suppressed when a >=_COVERAGE_SIM-similar approved requirement exists (recall-safe); a
# critic_rejected item with no approved twin (a genuine unverifiable claim) is always kept.
_COVERAGE_SUPPRESSIBLE = {"ungrounded", "inferred", "possible_miss", "gap", "critic_flag",
                          "critic_rejected", "non_requirement", "ambiguous"}
# Vacuous pointer / absorbed demotions never belong in Appendix C (Part H #4).
_POINTER_MARKERS = ("pointer", "folded into", "vacuous", "absorbed")
_COVERAGE_SIM = 0.6   # an open item this close to an approved requirement is a restatement of it


def reconcile_open_questions(open_q: list[dict], approved_reqs: list) -> list[dict]:
    """Part H reconciliation (recall-safe): drop pointer/absorbed demotions, and suppress any
    coverage-noise open item that NEAR-RESTATES an approved requirement (it is already in the SRS
    body). Genuine decisions are always kept. Never touches the approved set — it can only shrink
    Appendix C, never recall."""
    from ..agents.pipeline import _statement_similarity, topic_overlap

    approved_stmts = [getattr(r, "statement", "") or "" for r in (approved_reqs or [])]
    kept: list[dict] = []
    for o in open_q or []:
        kind = o.get("type", "")
        reason = (o.get("reason") or "").lower()
        if kind == "non_requirement" and any(m in reason for m in _POINTER_MARKERS):
            continue  # (#4) pointer/absorbed demotion — not a real open question
        stmt = o.get("statement", "") or ""
        if kind in _COVERAGE_SUPPRESSIBLE and stmt and any(
            _statement_similarity(stmt, a) >= _COVERAGE_SIM or topic_overlap(stmt, a) >= 0.85
            for a in approved_stmts
        ):
            continue  # (#1) already covered by an approved requirement — redundant in Appendix C
        kept.append(o)
    return kept

# Human-readable prefix per open-question kind.
_KIND_LABEL = {
    "inferred": "Implied requirement — confirm and make explicit",
    "critic_rejected": "Unverifiable claim — needs a source or removal",
    "critic_flag": "Retained requirement (cross-corroborated) the second-opinion review flagged — verify wording",
    "conflict": "Conflicting requirements — needs resolution",
    "ambiguous": "Ambiguous requirement — needs clarification",
    "ungrounded": "Stated but not grounded in a verbatim source quote — verify or discard",
    "non_requirement": "Not a product requirement (document meta / aside / KPI) — informational",
    "out_of_scope": "Marked out of scope in the source — exclusion, confirm",
    "disputed": "Disputed / not confirmed in scope — needs a decision",
    "undecided": "Undecided / decision pending in the source — needs a decision",
    "deferred": "Deferred to a later phase in the source — confirm timing",
    "gap": "Potential gap — a commonly-expected requirement may be missing",
    "possible_miss": "Possible missed requirement — substantive source text that produced no requirement; verify",
}


# A reviewer should not face a flat list of hundreds. Each kind maps to a TIER by the action it
# needs, so the Appendix leads with the handful of real decisions and keeps the rest as reference.
_TIER_OF_KIND = {
    "conflict": "A", "possible_miss": "A", "gap": "A", "out_of_scope": "A", "disputed": "A",
    "undecided": "A", "deferred": "A",
    "critic_rejected": "B", "inferred": "B", "ungrounded": "B", "ambiguous": "B",
    "non_requirement": "C", "critic_flag": "C",
}
_TIER_ORDER = {"A": 0, "B": 1, "C": 2}
_TIER_TITLE = {
    "A": "A · Decisions Required — resolve a conflict, confirm a possible miss/gap, or choose in/out of scope",
    "B": "B · To Verify or Confirm — check the source, then keep or drop",
    "C": "C · Informational / Captured for Completeness — reference only, usually no action",
}
_TIER_NOUN = {"A": "decisions", "B": "to verify", "C": "informational"}
# Ordering WITHIN a tier (most-actionable kind first).
_KIND_ORDER = {
    "conflict": 0, "possible_miss": 1, "gap": 2, "out_of_scope": 3, "disputed": 4, "undecided": 5,
    "deferred": 6, "critic_rejected": 7, "inferred": 8, "ungrounded": 9, "ambiguous": 10,
    "non_requirement": 11, "critic_flag": 12,
}


def _tier_of(kind: str) -> str:
    return _TIER_OF_KIND.get(kind, "B")


def compile_open_questions(open_q: list[dict]) -> list[dict]:
    """Dedup + tier + number the open-questions as TBD-n entries. Deterministic ordering
    (by tier, then kind, then statement) so the Appendix is reproducible and reviewable: the few
    real decisions (tier A) get the lowest TBD ids and lead the list."""
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for o in open_q or []:
        kind = o.get("type", "open")
        statement = (o.get("statement") or "").strip()
        key = (kind, normalize(statement))
        if key in seen or not statement:
            continue
        seen.add(key)
        unique.append(o)

    unique.sort(key=lambda o: (
        _TIER_ORDER[_tier_of(o.get("type", ""))],
        _KIND_ORDER.get(o.get("type", ""), 99),
        normalize(o.get("statement", "")),
    ))
    items: list[dict] = []
    tbd_n, note_n = 0, 0
    for o in unique:
        kind = o.get("type", "open")
        tier = _tier_of(kind)
        where = o.get("location") or o.get("doc_id") or ""
        reason = o.get("reason") or _KIND_LABEL.get(kind, "Open item")
        desc = f"{_KIND_LABEL.get(kind, kind)}: \"{o['statement'].strip()}\""
        if where:
            desc += f" (from {where})"
        desc += f" — {reason}"
        # Tier-C items are informational captures that need NO action — they are not "to be
        # determined", so they are numbered NOTE-n, not TBD-n. Only actionable items (A/B) count as
        # TBDs. Nothing is dropped — this just stops no-action notes inflating the TBD figure.
        if tier == "C":
            note_n += 1
            oid = f"NOTE-{note_n}"
        else:
            tbd_n += 1
            oid = f"TBD-{tbd_n}"
        items.append({"id": oid, "kind": kind, "tier": tier, "description": desc})
    return items


# TEMPORARY display cap (per user request): the SRS Appendix C shows at most this many actionable
# (Tier A/B) TBD rows — the highest-priority ones, since `compile_open_questions` sorts them A-first —
# and summarises the remainder as a count. NOTHING IS LOST: the full open-questions set still lives in
# the manifest (`open_questions` count) and every requirement's provenance stays in the RTM; this only
# trims what the Appendix C TABLE renders so a reviewer sees a short, prioritised decision list instead
# of ~40 rows. Set `max_tbds=None` (or raise APPENDIX_C_MAX_TBDS) to show them all.
APPENDIX_C_MAX_TBDS = 5


def open_questions_markdown(items: list[dict], *, max_tbds: int | None = APPENDIX_C_MAX_TBDS) -> str:
    """Appendix C, tiered: a one-line summary, then a table per tier (A decisions first). The
    actionable (Tier A/B) rows are capped at `max_tbds` for a crisp appendix; the overflow count is
    surfaced in the summary line (the full set remains in the manifest + RTM)."""
    if not items:
        return (
            "| ID | Description |\n|---|---|\n"
            "| — | No open questions: every requirement is grounded and reviewed. |\n"
        )

    # Actionable rows only (TBD-n, already priority-sorted by compile). Tier C (Informational /
    # Captured for Completeness) is intentionally NOT rendered — that section was removed on request.
    actionable = [it for it in items if it.get("tier", "B") in ("A", "B")]
    overflow = 0
    if max_tbds is not None and len(actionable) > max_tbds:
        overflow = len(actionable) - max_tbds
        actionable = actionable[:max_tbds]                 # keep the highest-priority TBDs

    by_tier: dict[str, list[dict]] = {}
    for it in actionable:
        by_tier.setdefault(it.get("tier", "B"), []).append(it)

    shown_ab = len(actionable)
    ab_summary = ", ".join(f"{len(by_tier[t])} {_TIER_NOUN[t]}" for t in ("A", "B") if by_tier.get(t))
    line = f"_{shown_ab} item(s) need attention"
    if ab_summary:
        line += f" — {ab_summary}"
    if overflow:
        line += (f"; showing the top {shown_ab} of {shown_ab + overflow} open items — {overflow} "
                 f"further lower-priority item(s) retained in the review log and RTM")
    line += ". Section A needs active decisions; B is for verification._"
    out = [line, ""]
    for t in ("A", "B"):
        group = by_tier.get(t)
        if not group:
            continue
        out.append(f"### {_TIER_TITLE[t]} ({len(group)})")
        out.append("")
        out.append("| ID | Description |")
        out.append("|---|---|")
        for it in group:
            out.append(f"| {it['id']} | {md_cell(it['description'])} |")
        out.append("")
    return "\n".join(out) + "\n"
