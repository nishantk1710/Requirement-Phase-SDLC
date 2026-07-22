# Legacy Leave Tool — As-Is Notes

Background notes on the current ("as-is") leave tool that ELAMS will replace. Captured
from the outgoing system's admin and a few long-time users. Not everything here carries
forward, but the replacement is expected to preserve the behaviour teams rely on.

## As-Is Behaviour
- The old system emailed a weekly pending-approvals digest to managers every Monday at 9 AM.
- Half-day leave is supported and counted as 0.5 days.
- Leave types were casual, sick, and earned; there was never a bereavement category.
- Approvals were done inside the tool; there was no mobile access at all.

## Deployment
- The system runs on the corporate network only.
- There is no plan to expose it to the public internet.
- It is a single-region on-prem install with a nightly backup.

## Assumptions
- Public holidays are maintained in a separate calendar module that we will keep using.
- User accounts come from corporate SSO; the legacy tool never managed passwords itself.
