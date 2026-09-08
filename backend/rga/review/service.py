"""Applying a human review decision to the store, with a full audit record.

Every decision writes a `ReviewDecision` (action + before/after snapshots + actor +
timestamp) BEFORE returning, so the decision log is the authoritative history of what a
human did and why — this is what powers the audit trail and the time-saved metric (P8).

Design choices:
  * accept  -> status = approved (statement unchanged).
  * edit    -> apply the given field changes, then status = approved. The requirement
               `id` is a content hash assigned at extraction; we deliberately DO NOT
               rehash on edit, so source-refs and prior decisions stay linked to it.
  * reject  -> status = rejected (kept for audit; invisible to generators).

Change-control on baselined requirements: editing the STATEMENT of an already-approved
requirement that is part of a frozen baseline re-opens it for re-approval (status ->
needs_review), because the prior sign-off was for the OLD wording. This is scoped to
projects that already have a baseline, so the first-pass review flow (edit -> approved)
is byte-for-byte unchanged until the first SRS has been generated. Metadata-only edits
(priority/feature/etc. with the same statement) keep the approval.
"""

from __future__ import annotations

from ..models import Requirement, ReviewAction, ReviewDecision, RType, Status

# Fields a reviewer may edit inline. `id`, `project_id`, `source_refs`, and provenance are
# NOT editable — traceability and identity must survive a human edit unchanged.
EDITABLE_FIELDS = frozenset(
    {"statement", "rtype", "nfr_category", "feature", "priority", "rationale"}
)

_ACTION_STATUS = {
    ReviewAction.accept: Status.approved,
    ReviewAction.edit: Status.approved,
    ReviewAction.reject: Status.rejected,
}


class ReviewError(ValueError):
    """A review decision that cannot be applied (unknown requirement / bad edit)."""


def _apply_edits(r: Requirement, edits: dict) -> None:
    for key, value in edits.items():
        if key not in EDITABLE_FIELDS:
            raise ReviewError(f"field '{key}' is not editable")
        if value is None:
            continue
        if key == "rtype":
            r.rtype = RType(value)  # validates the enum
        else:
            setattr(r, key, value)
    if not r.statement.strip():
        raise ReviewError("statement cannot be empty after edit")


async def apply_decision(
    repo,
    requirement_id: str,
    action: ReviewAction,
    *,
    edits: dict | None = None,
    actor: str = "reviewer",
) -> tuple[Requirement, ReviewDecision]:
    """Apply accept/edit/reject to a stored requirement and log the decision.

    Returns the updated requirement and the decision record. Raises `ReviewError`
    if the requirement does not exist or an edit is invalid.
    """
    r = await repo.get_requirement(requirement_id)
    if r is None:
        raise ReviewError(f"unknown requirement '{requirement_id}'")

    before = r.model_dump(mode="json")
    new_status = _ACTION_STATUS[action]
    if action == ReviewAction.edit:
        was_approved = r.status == Status.approved
        old_statement = r.statement
        _apply_edits(r, edits or {})
        # Re-open a baselined, approved requirement whose STATEMENT changed: it must be
        # re-approved before it can land in the next SRS version (the gate treats needs_review
        # as pending). Scoped to projects with a baseline, so pre-generation edits are unchanged.
        if (
            was_approved
            and r.statement.strip() != old_statement.strip()
            and await repo.latest_baseline(r.project_id) is not None
        ):
            new_status = Status.needs_review
    r.status = new_status
    after = r.model_dump(mode="json")

    await repo.save_requirement(r)
    decision = ReviewDecision(
        requirement_id=requirement_id,
        action=action,
        before=before,
        after=after,
        actor=actor,
    )
    await repo.log_review_decision(decision)
    return r, decision
