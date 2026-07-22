# Project Horizon (Green Build, Lite) — NFRs & Legacy Notes

## As-is (context)
The current storefront is hand-coded: every catalogue/price change is an engineering ticket, there is no
customer order self-service, and reporting is manual spreadsheet work.

## Non-functional requirements
- **NFR-1** — Product listing pages respond within ~2 seconds (p95).
- **NFR-2** — Large product lists paginate rather than returning everything at once.
- **NFR-3** — Passwords are stored using a strong one-way hash (bcrypt/argon2).
- **NFR-4** — Login and password-reset endpoints are rate-limited and reset tokens expire.
- **NFR-5** — Staff/admin functions use role-based access control and are audit-logged.
- **NFR-6** — No card data is stored; secrets are read from environment configuration and are not required to run in test mode.
- **NFR-7** — Browse, product, cart and checkout meet basic WCAG AA (labels, contrast, keyboard).
- **NFR-8** — Stock reservations are released automatically after a timeout to prevent phantom out-of-stock.

## Build boundary (so implementation is unblocked)
The platform is built complete, including a provider-agnostic payment module with a working test-mode
mock. Only supplied by Vantage after the build (via configuration, not code): live payment keys, merchant
account, KYC, PCI onboarding, real email/SMS delivery and live carrier tracking.
