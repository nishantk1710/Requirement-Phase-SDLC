"""Fix 6 — NFR (and any-requirement) folding is LOSSLESS and traceable.

The concern: an NFR count dropping after consolidation (e.g. 20 -> 17) could be legitimate merging
OR silent over-compression that quietly loses a security/availability requirement. This proves it
is the former: every merged requirement is recorded in the survivor's `absorbed_statements` audit
trail, so `unaccounted_requirements` (the recall guard) returns nothing — a dropped-without-record
requirement would be surfaced, never vanish.
"""

from __future__ import annotations

from rga.agents.pipeline import dedupe_requirements, unaccounted_requirements
from rga.models import Requirement, RType, SourceRef


def _nfr(statement: str, *, category: str = "security", source: str = "brd") -> Requirement:
    return Requirement(
        id="EX-" + str(abs(hash(statement)) % 10**8),
        project_id="P",
        statement=statement,
        rtype=RType.non_functional,
        nfr_category=category,
        source_refs=[SourceRef(doc_id="d", source_type=source, location="1",
                               raw_quote=statement, start=0, end=len(statement))],
    )


def test_nfr_folding_is_lossless_and_traceable():
    a = _nfr("The system shall encrypt all customer data in transit using TLS.")
    b = _nfr("The system shall encrypt all customer data in transit via TLS.")   # near-dup of a -> merges
    c = _nfr("The system shall rate-limit login attempts.")                       # distinct -> survives
    original = [a, b, c]

    survivors, merged = dedupe_requirements(list(original))

    assert merged == 1                                    # a/b folded into one
    assert len(survivors) == 2                            # 3 in -> 2 survive (a|b, c)
    # count identity: nothing evaporated — in == survived + absorbed
    absorbed = sum(len(s.provenance.get("absorbed_statements") or []) for s in survivors)
    assert len(original) == len(survivors) + absorbed
    # the recall guard confirms EVERY input NFR is accounted for (survivor or recorded merge)
    assert unaccounted_requirements(original, survivors) == []


def test_recall_guard_flags_a_silently_dropped_requirement():
    """If a requirement were dropped WITHOUT being recorded as an absorbed merge, the guard must
    surface it (this is what protects against silent over-compression)."""
    a = _nfr("The system shall encrypt data at rest.")
    b = _nfr("The system shall provide an audit log of admin actions.")
    # simulate a bad fold: `b` disappeared but was never recorded on the survivor's trail
    survivors = [a]
    missing = unaccounted_requirements([a, b], survivors)
    assert missing == ["The system shall provide an audit log of admin actions."]


def test_distinct_nfrs_are_never_merged():
    """Recall-safety at the merge boundary: genuinely different NFRs must both survive."""
    reqs = [
        _nfr("The system shall store passwords using bcrypt or argon2.", category="security"),
        _nfr("Product listing pages shall respond within 2 seconds at p95.", category="performance"),
        _nfr("Key user flows shall meet WCAG AA accessibility.", category="usability"),
    ]
    survivors, merged = dedupe_requirements(list(reqs))
    assert merged == 0 and len(survivors) == 3
    assert unaccounted_requirements(reqs, survivors) == []
