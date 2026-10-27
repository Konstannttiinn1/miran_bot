# User payment history implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this task inline. Track verification with checkboxes.

**Goal:** Let an administrator open existing orders and saved receipts from a user's profile, including orders created before this update.

**Architecture:** Add a read-only child router to the current admin router and an «Оплаты» button to the user card. Query the existing `orders` table by the user's internal ID with deterministic pagination; display archived amounts and statuses without recalculating prices.

**Tech Stack:** Existing Python, aiogram, SQLAlchemy and pytest. No dependencies, schema migrations or new settings.

**Spec:** User request of 2026-10-27: expose transactions/payments for the user whose admin profile is open. A small history view should show date, product, amount and status; an individual order can show its already-saved receipt.

## Global constraints

- All new views are available only to `ADMIN_IDS` and use Russian copy.
- Read orders and receipts without changing statuses, dealer balances or VPN access.
- Pending and failed orders remain distinguishable from completed payments; free trials are excluded.
- For USD dealer orders, clearly label the archived dealer amount. The original retail Toman quote was not stored in `orders`; show the saved receipt rather than inventing an exchange-rate conversion.
- For crypto and Stars, preserve the stored amount and currency.
- Dates are displayed in Moscow time, explicitly labeled МСК.
- No live Telegram, payment provider, VPN panel or production database calls in verification.

## Approach

A profile button with a paginated list and one order card is sufficient. A new general ledger or payment-management dashboard would add unnecessary scope. Existing receipt notifications remain unchanged. Order cards validate that the requested order belongs to the profile in the callback.

## Review focus

- Historical dealer prices versus today's exchange rate and discount.
- Equal order timestamps, deleted/outdated pages and users with no orders.
- Pending payment, paid-but-activation-failed and unfamiliar historical statuses.
- An unauthorized or forwarded callback and an order from another user's profile.
- Missing/unavailable receipts and unknown legacy product identifiers.

## Task 1: Profile history and order receipts

**Files:** Create `app/handlers/admin_orders.py`, `tests/test_admin_payments.py`; modify `app/handlers/admin.py`, `app/repositories/db_repo.py`, `PROJECT_CONTEXT.md`.

**Interfaces:** `list_user_orders(user_id: int, page: int = 0, per_page: int = 5) -> tuple[list[Order], int, int]` returns records, total count and clamped page. `admin_payments:<telegram_id>:<page>` opens the list; `admin_order:<telegram_id>:<order_id>:<page>` opens one order and its receipt. The child router is registered through `admin.router` so the existing application entry point remains unchanged.

- [x] Write failing tests through the real admin router, local database and mocked outgoing Telegram message methods.
- [x] Observe the missing profile button/history/card behavior before implementing it.
- [x] Implement filtered read-only pagination, archived amount/status formatting, guarded history and order callbacks, and receipt fallback.
- [x] Run focused tests, full suite, compilation and whitespace checks.
- [x] Independently review the verified change. Publish to the established `feat/dealer-tools-promos` branch using the expected remote head.

## Execution record

- Base: `97d226a79318bba0d1798aa2c1a008e0031728af`, clean feature checkout; the previous verified suite had 181 passing tests.
- Production installation is performed by the user. Existing history is available after installation; a new payment is not required.
- RED: all 16 new cases failed for missing history/card behavior. GREEN: 24 history/receipt tests passed through the registered admin router and isolated database, with only Telegram transport mocked.
- Final suite: 197 passed; compilation and whitespace checks passed. Existing datetime.utcnow deprecation warnings remain. No live delivery or production installation has been tested here.
- Independent review found no blocking issues. Removed duplicate I18nMiddleware registration after verifying aiogram inherits parent middleware; reran the full suite on that final code.
- Archived product descriptions use the stored SKU and available catalog metadata; no historical product snapshot exists. Pagination may shift with new orders and clamps stale pages. Unknown statuses stay visible without being reclassified as paid. Existing unrelated admin actions remain outside this change.
