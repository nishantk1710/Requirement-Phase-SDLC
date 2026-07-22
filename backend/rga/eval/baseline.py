"""Human baseline + the data-driven go/no-go bar (evaluation-framework.md §5-§6).

The PoC's success is measured RELATIVE TO A HUMAN, not against an arbitrary number:
the tool must reach >= BAR_RATIO of the human's recall on the sealed test set, and be
no slower than manual. The human numbers come from a short BA session (a dependency);
this module records/loads them and computes the verdict.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

BAR_RATIO = 0.90  # tool must recover >= 90% of what a human finds


class HumanBaseline(BaseModel):
    recall: float = Field(ge=0.0, le=1.0)  # fraction of gold a human found unaided
    minutes: float = Field(ge=0.0)  # wall-clock time to produce the list manually
    reviewer: str = ""
    notes: str = ""
    pending: bool = False  # True until a real BA session fills it


def load_baseline(path: str | Path) -> HumanBaseline | None:
    p = Path(path)
    if not p.exists():
        return None
    return HumanBaseline.model_validate_json(p.read_text(encoding="utf-8"))


def save_baseline(path: str | Path, hb: HumanBaseline) -> None:
    Path(path).write_text(hb.model_dump_json(indent=2), encoding="utf-8")


def go_no_go(
    tool_recall: float,
    tool_minutes: float | None,
    baseline: HumanBaseline | None,
    *,
    ratio: float = BAR_RATIO,
) -> dict:
    """Return the PoC verdict. Without a (non-pending) human baseline we cannot pass —
    we report 'pending' so the gate is honest rather than green-by-default."""
    if baseline is None or baseline.pending:
        return {
            "verdict": "pending_human_baseline",
            "tool_recall": tool_recall,
            "reason": "human baseline not yet recorded (BA session dependency)",
            "required_recall": None,
        }

    required = round(ratio * baseline.recall, 3)
    recall_ok = tool_recall >= required
    time_ok = tool_minutes is None or tool_minutes <= baseline.minutes
    if recall_ok and time_ok:
        verdict = "pass"
    elif tool_recall >= 0.75 * required:
        verdict = "marginal"
    else:
        verdict = "fail"
    return {
        "verdict": verdict,
        "tool_recall": tool_recall,
        "human_recall": baseline.recall,
        "required_recall": required,
        "recall_ok": recall_ok,
        "time_ok": time_ok,
        "tool_minutes": tool_minutes,
        "human_minutes": baseline.minutes,
    }
