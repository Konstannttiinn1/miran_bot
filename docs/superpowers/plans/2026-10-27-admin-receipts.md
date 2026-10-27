# Admin receipt notifications implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this single task inline. Steps use checkbox syntax for tracking.

**Goal:** Let an administrator understand a bank order and inspect its receipt directly from the notification.

**Architecture:** Extend the existing admin notification sender to support an optional photo and keyboard. Reuse the order's product, saved dealer debit and original bank quote; preserve dealer confirmation as the source of payment approval.

**Tech Stack:** Existing Python 3.12, aiogram 3, SQLAlchemy and pytest; no new dependencies or database migration.

**Spec:** User request of 2026-10-27 and screenshot: the admin receives only an order number and buyer identifier. Show the receipt, product/term/devices, client amount, dealer debit and buyer identity; link the existing admin user card. Existing ID lookup remains Users → Search by ID / @username.

## Global constraints

- Russian admin copy; dealer copy remains in the dealer's selected language.
- Receipt upload means awaiting dealer review, not confirmed payment.
- Use the original `retail_toman` quote; an unavailable historical quote must not be recalculated.
- Dealer debit comes from `Order.amount` in USD, separately labeled from retail.
- No live Telegram, panel or production database calls during verification.

## Approach

Extend the shared sender instead of duplicating the admin-recipient loop in the client handler. Send the photo with its summary as caption; fall back to the same summary as text if photo delivery fails. The user-card button uses `admin_user:<telegram_id>` with an explicit administrator check on its receiving handler.

## Review focus

- Customer without a username: keep name and Telegram ID visible.
- HTML in Telegram display names: escape customer text.
- Quote missing or exchange rate changed: preserve known original amounts and identify unknown ones.
- One admin cannot receive a photo: retain the summary and continue to other admins.
- Forwarded admin-user button: non-admin recipients cannot open the card.

## Task 1: Receipt with order context and user-card access

**Files:** Modify `app/utils/notifications.py`, `app/handlers/client.py`, `app/handlers/admin.py`; create `tests/test_admin_receipts.py`; update `PROJECT_CONTEXT.md`.

**Interfaces:** `notify_admins(text: str, *, photo: str | None = None, reply_markup: InlineKeyboardMarkup | None = None) -> None`. Existing text-only callers remain compatible. `receive_receipt` keeps the existing order validation and dealer delivery; `open_user` accepts only configured admin Telegram IDs.

- [x] Write integration tests with real order persistence and receipt handling; replace only outbound Telegram methods.
- [x] Run `python -m pytest -q tests/test_admin_receipts.py`; observe missing admin photos/context and missing card authorization.
- [x] Implement the optional photo sender, detailed Russian receipt caption, buyer button and card authorization.
- [x] Run focused tests, then `python -m pytest -q`, `python -m compileall -q app main.py` and `git diff --check`.
- [x] Independently review the final diff and document limits. Publish the verified tree as a fast-forward commit to `feat/dealer-tools-promos` with the expected-head check.

## Execution record

- Base: `2b67ccec0696e4e3f343d47c4d46b58999117b70`; clean feature checkout. Existing authorization is to modify and publish this project branch; production installation is performed by the user.
- Baseline: full suite, 173 passed. Red: 6 failed / 2 passed in the new file, reproducing missing admin photos, unsupported photo delivery and unrestricted user-card access. Green: receipt and pricing tests, 29 passed.
- All tests use an isolated local database and mocked Telegram transport; no production payment, Telegram delivery or server deployment is implied by these results.
- Original order #129 is not re-notified automatically. Existing user cards show account/subscription information; this change does not add an order-history screen.
- Final verification: 181 passed; compilation and whitespace checks passed. Existing `datetime.utcnow` deprecation warnings remain. Independent read-only review: no blocking or actionable findings.
- Review boundaries: real Telegram delivery awaits the next production receipt; no automatic historical re-notification; authorization of existing unrelated admin routes is outside this change. The new receipt user-card entry point is explicitly guarded and tested.
