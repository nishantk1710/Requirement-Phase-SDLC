"""Exit metrics for the PoC gate (P8 TC8.2).

Success is MEASURED, not asserted. This computes, from the store (+ the eval corpus when
one is present):
  * recall vs. the data-driven human bar (go/no-go) — the critical PoC metric;
  * ambiguity-flag coverage;
  * human review acceptance / edit / reject rates (from the decision log);
  * time-saved (tool wall-clock vs. the human baseline — `pending` until the BA session).
Everything is reported honestly; where a number needs the (still-pending) human baseline,
the verdict says so rather than inventing a pass.
"""

from __future__ import annotations

from pathlib import Path

from ..eval.baseline import go_no_go, load_baseline
from ..eval.dataset import load_corpus
from ..eval.scorer import score
from ..models import Requirement, ReviewAction, Status
from ..review.gate import counts


async def _decision_rates(repo, requirements: list[Requirement]) -> dict:
    accepts = edits = rejects = 0
    for r in requirements:
        for d in await repo.list_review_decisions(r.id):
            if d.action == ReviewAction.accept:
                accepts += 1
            elif d.action == ReviewAction.edit:
                edits += 1
            elif d.action == ReviewAction.reject:
                rejects += 1
    total = accepts + edits + rejects
    rate = lambda n: round(n / total, 3) if total else None  # noqa: E731
    return {
        "decisions": total,
        "accepted": accepts,
        "edited": edits,
        "rejected": rejects,
        "acceptance_rate": rate(accepts),
        "edit_rate": rate(edits),
        "reject_rate": rate(rejects),
    }


def _recall_block(requirements: list[Requirement], corpus_dir: str | None) -> dict:
    """Recall vs. the human bar — only if an eval corpus (gold + splits) is available."""
    from ..review.gate import approved_only

    if not corpus_dir or not (Path(corpus_dir) / "gold.json").exists():
        return {"available": False, "note": "no eval corpus for this project; recall not computed"}
    # Exit recall/precision are measured on the DELIVERED set — the APPROVED requirements — not on
    # everything the extractor produced (a human-rejected item must not count as captured). E-M6.
    delivered = approved_only(requirements)
    corpus = load_corpus(corpus_dir)
    test_ids = set(corpus.test) or None
    sc = score(delivered, corpus, split_ids=test_ids)
    baseline = load_baseline(Path(corpus_dir) / "human_baseline.json")
    verdict = go_no_go(sc["recall_explicit"] or 0.0, None, baseline)
    return {
        "available": True,
        "basis": "approved (delivered)",
        "split": "test" if test_ids else "all",
        "recall_explicit": sc["recall_explicit"],
        "recall_implicit": sc["recall_implicit"],
        "precision_grounded": score(delivered, corpus, split_ids=None)["precision_grounded"],
        "per_difficulty": sc["per_difficulty"],
        "go_no_go": verdict,
    }


async def compute_exit_metrics(
    repo,
    project_id: str,
    *,
    corpus_dir: str | None = None,
    tool_minutes: float | None = None,
    traceability_complete: bool | None = None,
) -> dict:
    """Assemble the full exit-metrics report for a project."""
    reqs = await repo.list_requirements(project_id)
    approved = [r for r in reqs if r.status == Status.approved]
    ambiguity_flagged = sum(1 for r in reqs if r.quality.ambiguity_flags)
    recall = _recall_block(reqs, corpus_dir)

    baseline = load_baseline(Path(corpus_dir) / "human_baseline.json") if corpus_dir else None
    human_minutes = baseline.minutes if (baseline and not baseline.pending) else None
    time_saved = {
        "tool_minutes": tool_minutes,
        "human_minutes": human_minutes,
        "verdict": (
            "pending_human_baseline"
            if human_minutes is None
            else ("faster" if (tool_minutes or 0) <= human_minutes else "slower")
        ),
    }

    return {
        "project_id": project_id,
        "counts": counts(reqs),
        "approved": len(approved),
        "ambiguity_flagged": ambiguity_flagged,
        "recall_vs_bar": recall,
        "review": await _decision_rates(repo, reqs),
        "time_saved": time_saved,
        "traceability_complete": traceability_complete,
    }
