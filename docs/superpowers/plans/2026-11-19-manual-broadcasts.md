# Manual Broadcasts Implementation Plan

**Goal:** Let both the configured bot administrator and dealers send a one-time text or photo with caption to the whole Telegram user base or its active/inactive segment.

**Architecture:** One private-chat flow for both menus, with a preview of the original message and a one-use confirmation tied to the draft. Select recipients by Telegram user ID using an existence check over personal subscriptions. Deliver with bounded pacing and a per-recipient result, then notify the admin of dealer sends.

**Tech Stack:** Python, aiogram 3, SQLAlchemy, pytest, SQLite and PostgreSQL.

**Scope:** No additional permissions, schedules, external messaging, deployment, or DB migration. Do not contact production users in tests.

- [x] Write SQLite/PostgreSQL tests for `db_repo.list_broadcast_recipients(audience, *, now=None)`. All non-banned users are eligible. Active means at least one locally active, non-expired personal VPN. Inactive is the complement and includes users who never bought, expired accounts, and users with pending purchases only.
- [x] Write Telegram-flow tests for both privileged roles, denial for regular clients, exact photo/text preview, cancellation and one-time confirmation. The final audience must not expand past the preview. A received Telegram rejection must not stop the job. Test the admin alert and summary.
- [x] Implement the recipient query in `app/repositories/db_repo.py`, content delivery in `app/services/broadcast.py`, and the draft flow in `app/handlers/broadcast.py`. Register the router, add both menu entries and localize controls to FA/RU/EN.
- [x] Run focused and full suites and check the diff. Submit an isolated branch based on the current multi-subscription branch.
