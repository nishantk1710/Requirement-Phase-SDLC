# Hard-case catalog — ELAMS corpus

Deliberately planted difficult cases, so later phases can be *measured* against known-hard items.
(Every `explicit` requirement not listed here is a straightforward single-span statement.)

## Multi-span (evidence spread across sentences)
| ID | Note |
|---|---|
| REQ-007 | Deduction rule split across two BRD sentences (deduct on approval + only after final approval). |
| REQ-037 | "corporate network only" + "no public internet" — two legacy sentences → one constraint. |

## Implicit (inferred; statement not stated verbatim, supporting span is)
| ID | Note |
|---|---|
| REQ-011 | "separate balance per employee/type" implied by the balances line. |
| REQ-025 | Audit-lookup capability implied by the "who approved last year" complaint. |
| REQ-028 | Team attendance view implied by "shared spreadsheet that breaks". |
| REQ-034 | Weekly pending-approvals digest implied by legacy "must preserve" behaviour. |
| REQ-039 | Working-day calendar implied by the "3 working days" notice rule (added at review). |
| REQ-040 | Leave-type set (casual/sick/earned only) preserved from legacy as-is (added at review). |
| REQ-041 | Nightly backup preserved from legacy as-is — reliability (added at review). |

## Ambiguous (vague / weak wording — should be flagged by A3 in P5)
| ID | Note |
|---|---|
| REQ-004 | "load quickly" — no measurable target. |
| REQ-017 | "flexible enough" — unmeasurable. |
| REQ-019 | "without any training" — weakly testable. |

## Duplicate (same obligation restated in another document)
| ID | Duplicate of | Note |
|---|---|---|
| REQ-021 | REQ-010 | Email restates the approval/rejection notification. |
| REQ-031 | REQ-001 | Jira ticket restates "submit a leave request". |

## Conflicting (contradicts another requirement — must surface at review)
| ID | Conflicts with | Note |
|---|---|---|
| REQ-016 | REQ-002 | Auto-approve 1-day casual vs. "route every request to the manager". |
| REQ-024 | REQ-038 | Casual leave lapses / no carry-over vs. carry over up to 5 days. |
| REQ-038 | REQ-024 | (other side of the same conflict) |
