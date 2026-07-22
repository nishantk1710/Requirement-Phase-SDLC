"""The downstream-visibility gate (risk-review fix #6/#11: human-gated throughout).

These are PURE functions over a list of requirements — no I/O, fully deterministic, and
therefore trivially testable. The generators (P7) and the end-to-end run (P8) MUST route
through `approved_only()` so that a rejected or not-yet-reviewed requirement can never
reach an SRS/RTM. `confidence` is deliberately NOT consulted here — in Wave 1 every
requirement is reviewed by a human; confidence is recorded for later calibration only.
"""

from __future__ import annotations

from ..models import Requirement, Status

# Requirements a human has not yet triaged. While any remain, the batch is not ready
# to generate from — generation off a half-reviewed set would silently drop work.
PENDING: frozenset[Status] = frozenset({Status.candidate, Status.needs_review})


def approved_only(reqs: list[Requirement]) -> list[Requirement]:
    """The ONLY requirements visible to generators: those a human approved."""
    return [r for r in reqs if r.status == Status.approved]


def counts(reqs: list[Requirement]) -> dict[str, int]:
    """A small status tally for the review dashboard / run report."""
    out = {s.value: 0 for s in Status}
    for r in reqs:
        out[r.status.value] += 1
    return out


def ready_for_generation(reqs: list[Requirement]) -> tuple[bool, str]:
    """May the generators run? Returns (ok, reason).

    Ready iff (a) at least one requirement is approved, and (b) NOTHING is still
    pending review. This is the hard gate enforced before any SRS/RTM generation.
    """
    if not reqs:
        return (False, "no requirements to generate from")
    pending = [r for r in reqs if r.status in PENDING]
    approved = approved_only(reqs)
    if pending:
        return (
            False,
            f"{len(pending)} requirement(s) still awaiting human review; "
            "approve, edit, or reject them before generating",
        )
    if not approved:
        return (False, "no approved requirements (all were rejected)")
    return (True, f"{len(approved)} approved requirement(s) ready for generation")
