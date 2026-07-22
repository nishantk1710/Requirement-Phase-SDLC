# Project Horizon (Green Build) — NFRs & Legacy Notes

## Part 1 — As-is (the current system, for context)
- The current storefront is hand-coded: any product, price or banner change is an engineering ticket, so merchandising is slow.
- Customers have no self-service today — no order history, order tracking or returns — and there is no consolidated reporting.

## Part 2 — Non-functional requirements (all in-build / green)
- **NFR-PERF-01** — Product listing and detail pages should respond within ~2 seconds (p95) on a typical connection.
- **NFR-SEC-01** — Passwords are stored using a strong one-way hash (bcrypt/argon2); never in plaintext.
- **NFR-SEC-02** — Login and password-reset endpoints are rate-limited, and reset tokens expire.
- **NFR-SEC-03** — Role-based access control protects all staff/admin functions, and staff actions are audit-logged.
- **NFR-SEC-04** — No card data is stored by the platform; payment secrets are read from configuration, never hard-coded.
- **NFR-A11Y-01** — Key flows (browse, product detail, cart, checkout) meet basic WCAG AA (labels, contrast, keyboard).
- **NFR-OBS-01** — Application errors are logged so issues can be diagnosed.

## Part 3 — Build boundary reminder
The platform is built complete, including a provider-agnostic payment module with a working test-mode mock. Only live payment keys, the merchant account and real email/SMS delivery are supplied by Vantage after the build via configuration — none are required to build, run or test the system.
