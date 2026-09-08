"""Change/delta detection for the agile iteration loop.

A *baseline* is the approved requirement set frozen at generation time (stored per version). The
delta compares the CURRENT approved set against the latest baseline snapshot and classifies each
requirement as added / modified / removed / unchanged.

This works on the existing requirement id because the review `edit` action keeps the id STABLE (it
does not rehash on edit), so an edited requirement shows up as *modified* (same id, changed
statement), not as a remove+add. Purely a read-side projection — it never mutates anything.
"""

from __future__ import annotations

from ..generate.common import approved_sorted
from ..generate.srs_template import assign_srs_ids, section_for
from ..models import Requirement


def snapshot_approved(requirements: list[Requirement]) -> list[dict]:
    """A compact, JSON-serialisable snapshot of the approved set for a baseline — exactly the items
    that go into the SRS (same filter + ordering)."""
    approved = approved_sorted(requirements)
    id_map = assign_srs_ids(approved)
    return [
        {
            "id": r.id,
            "srs_id": id_map.get(r.id, "—"),
            "statement": r.statement,
            "rtype": r.rtype.value,
            "feature": r.feature,
            "priority": r.priority.value if r.priority else None,
            "section": section_for(r),
        }
        for r in approved
    ]


def compute_changes(current: list[Requirement], baseline_snapshot: list[dict] | None) -> dict:
    """Classify the current approved set against a baseline snapshot (added/modified/removed/
    unchanged). With no baseline, everything reads as 'added' relative to nothing — callers treat a
    missing baseline as 'no changes yet' (see the API endpoint)."""
    cur = snapshot_approved(current)
    cur_by_id = {c["id"]: c for c in cur}
    base_by_id = {b["id"]: b for b in (baseline_snapshot or [])}

    added: list[dict] = []
    modified: list[dict] = []
    removed: list[dict] = []
    unchanged = 0
    for cid, c in cur_by_id.items():
        b = base_by_id.get(cid)
        if b is None:
            added.append({"id": cid, "srs_id": c["srs_id"], "statement": c["statement"]})
        elif (b.get("statement") or "") != c["statement"]:
            modified.append({"id": cid, "srs_id": c["srs_id"], "statement": c["statement"],
                             "before": b.get("statement", "")})
        else:
            unchanged += 1
    for bid, b in base_by_id.items():
        if bid not in cur_by_id:
            removed.append({"id": bid, "srs_id": b.get("srs_id", "—"), "statement": b.get("statement", "")})

    summary = {"added": len(added), "modified": len(modified), "removed": len(removed), "unchanged": unchanged}
    return {"added": added, "modified": modified, "removed": removed, "unchanged": unchanged,
            "summary": summary, "total_changes": len(added) + len(modified) + len(removed)}


def delta_reason(delta: dict) -> str:
    """A one-line summary of a delta, for the SRS Revision History 'Reason for Changes' cell."""
    s = delta["summary"]
    parts = [f"{s[k]} {label}" for k, label in
             (("added", "added"), ("modified", "modified"), ("removed", "removed")) if s[k]]
    return "Revised — " + (", ".join(parts) if parts else "no requirement changes") + "."
