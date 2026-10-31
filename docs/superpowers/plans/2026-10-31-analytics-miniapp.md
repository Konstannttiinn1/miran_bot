# ORION analytics Mini App — design and implementation plan

> **For agentic workers:** Execute inline with superpowers:executing-plans and TDD; perform one independent final review. The user chose a small Telegram Mini App and campaign links on 2026-10-31.

**Goal:** A small mobile dashboard showing confirmed sales, paying users and subscription terms, plus advertising campaigns connecting a bot launch to later purchases.

**Architecture:** Buildless HTML/CSS/JS served by the existing FastAPI/uvicorn dependencies on the bot's VPS. Telegram-signed initData authorizes each API request. New additive tables hold campaigns, unique campaign visits, first attribution, bank price snapshots and immutable sale records; current order processing remains authoritative.

**Spec:** The user's request in this conversation: no raw click counter, no large CRM, no crowded dashboard. Create a campaign, copy its Telegram link, see unique users who launched the bot and subsequently purchased. Administrator sees the whole project and dealer scopes; dealers see their own sales/campaigns and related customers.

## Product scope

- Three tabs: Overview / Users / Campaigns; responsive phone layout, quiet slate/blue palette, no decorative charts.
- Overview: confirmed subscription sales (purchases + renewals), traffic add-ons, distinct buying identities, native-currency totals; Today / 7 days / 30 days / This month / Previous month / All time. Dates use Moscow time.
- Users: paid / unpaid / all, search, pagination; name or Telegram ID, current expiry and most recent paid product (term/devices). Dealer-managed subscriptions are explicitly labeled and kept distinct from Telegram users.
- Campaigns: name, unique bot launches, attributed buyers, conversion and link; create, copy, open its visitor list. No ad budget, bonuses, raw-click endpoint or referral payouts.
- User-facing admin copy is Russian. Dealer UI follows the saved fa/en/ru language; Persian supports RTL and Persian digits.
- Empty/loading/error/auth-expired states must work. Production never serves demo user data or accepts a preview login.

## Data and accounting rules

- `/start ad_<random-code>` records unique `(campaign, telegram_id)` visits. Repeats do not inflate people counts. The first valid campaign remains the user's acquisition source; a later link records a visit but cannot steal attribution.
- Purchases prior to attribution never become campaign conversions. Source and actual processing dealer remain different dimensions.
- A paid customer has a confirmed monetary purchase, including Stars/crypto received before a provisioning failure. Trial/free grants and pending/rejected invoices are excluded.
- Dealer orders become sales only on successful completion, after the refundable reservation. Managed dealer purchase/renewal/top-up flows also enter the ledger.
- Each financial event has a unique source key and is recorded atomically with its authoritative transition. Duplicate callbacks/startup/backfill cannot duplicate sales.
- Currency amounts are never added across currencies. USD dealer debit, client retail Toman quote, USDT and Stars remain distinct. Prepaid balance top-ups are not counted again as tariff sales; dashboard sums describe sales, not net profit or cash settlement.
- Bank quotes are persisted at order creation; existing FSM quotes may be preserved when an old pending receipt arrives. Missing historical retail prices are never reconstructed from current rates/discounts.
- New records use the actual confirmation time. Historical Orders and completed DealerLog actions are backfilled idempotently; fallback creation/reservation times are marked estimated and disclosed in the period summary.
- Analytics identifiers do not prevent existing user deletion: history uses stable Telegram IDs and source identifiers without new foreign keys to deletable orders/users.

## API contract

All `/api/*` requests require `Authorization: tma <Telegram.WebApp.initData>`. Validate HMAC using the bot token, reject duplicate query fields, stale/future auth_date and missing user; load roles/blocked state from the database. No credentials in URLs, frontend, localStorage or logs; no permissive CORS.

- `GET /api/me`: viewer `{telegram_id,name,role,lang}`, dealer selector choices for admin.
- `GET /api/overview?period=30d&dealer=<optional Telegram ID>`: counts, exact native-currency totals, retail Toman where preserved, number of estimated historical times and period boundaries.
- `GET /api/users?paid=all|paid|unpaid&q=...&page=0&campaign=<optional id>&dealer=...`: paginated Telegram customers and separately labeled managed subscriptions, expiry/term/payment state. Campaign buyer state refers to purchases attributed to that campaign.
- `GET /api/campaigns?dealer=...`: lifetime visitor/buyer/conversion metrics for visible campaigns, with bot links; currency totals stay on Overview.
- `POST /api/campaigns`: `{name}`; owner is the authenticated actor, never client supplied. Administrator may choose an actual dealer owner through the validated scope.

Unauthenticated requests return 401; clients/blocked dealers or foreign dealer/campaign scopes return 403. Static assets are public but contain no data. APIs disable caching. Bind the service to loopback and publish behind HTTPS on the user's server; `ANALYTICS_WEBAPP_URL` enables bot launch buttons. No separate Sites-hosted database or ChatGPT login is introduced.

## File map

- Models and authoritative hooks: `app/database/models.py`, `app/database/engine.py`, `app/repositories/db_repo.py`, `app/handlers/client.py`, `app/handlers/start.py`.
- Data operations/backfill: `app/repositories/analytics_repo.py`.
- Read models/permissions/report periods: `app/services/analytics.py`.
- Telegram validation: `app/utils/telegram_auth.py`; HTTP app: `app/webapp.py`.
- Frontend: `app/web_static/index.html`, `analytics.css`, `analytics.js`.
- Bot launch button/settings/locales: `app/keyboards/builders.py`, `app/config.py`, `app/locales/{ru,en,fa}.json`.
- Tests: `tests/test_analytics.py`, `tests/test_analytics_api.py`, existing payment/dealer/localization suites.
- Deployment/docs: `deploy/miran-analytics.service`, `docs/2026-10-31-analytics.md`, `PROJECT_CONTEXT.md`.

## Task 1 — attribution and immutable sales

- [x] Write and observe failing tests for unique visitors, first-source protection, purchases before/after visits, idempotent sales, quote snapshots and managed dealer flows.
- [x] Implement additive models/repository and atomic hooks without changing tariff amounts or fulfillment behavior.
- [x] Backfill successful historical transactions with explicit uncertainty, and verify current user deletion still works.
- [x] Run new data tests and existing payment/trial/dealer suites.

## Task 2 — authenticated reporting API

- [x] Write and observe failing tests for valid/tampered/stale Telegram authentication, role boundaries, foreign scopes, metrics/currencies, paid filters and history.
- [x] Implement the shared read model, FastAPI endpoints and bot launch buttons; no live provider calls for reports.
- [x] Verify strict authorization, input bounds, period boundaries, campaign conversion timing and secret-free responses.

## Task 3 — small Mini App and installation

- [x] Implement the three-tab frontend with a documented API contract, RU/FA/EN, RTL, real create/copy/filter/paging actions and no production mock mode.
- [x] Validate the browser UI via managed Sites preview when available; use an isolated QA fixture, never a production auth bypass.
- [x] Add loopback systemd service and HTTPS deployment instructions for the existing VPS; preserve the existing bot service/environment and secrets.
- [x] Run full Python tests, JS syntax/build checks, compilation and git whitespace checks; obtain independent code review.
- [x] Prepare the verified tree for publication to `feat/dealer-tools-promos` with expected-head protection. Production domain/service installation is performed by the user.

## References and execution record

- Base commit: `9c5590a4c224b04bd3ea88680415095aeb7f6101`.
- Telegram validation: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
- Pattern reference (not copied code): BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot campaign schemas and attribution tests — campaign registrations, conversions, duplicate protection.
- Frontend follows Sites local/VPS implementation guidance. Managed preview must use `sites-preview`; no separate preview server/browser workaround.
- Baseline 197 passed. Initial data tests RED 8, API tests RED 11; both reached GREEN. Existing payment/trial/pricing subset: 107 passed; first whole suite 217 passed.
- Independent read-only review found an attribution/payment race, SQLite log/subscription ID reuse and a stale dealer selector. Reproduced SQLite loss/false-paid-state and PostgreSQL lock-query omissions with failing tests, then fixed immutable keys, managed creation identity, common customer locks for live events and an always-available managed selector.
- Added a failing boundary test to keep historical import free of multi-customer live row locks; import uses unlocked historical reads while authoritative payment/attribution transactions share the customer lock.
- Browser QA: desktop and 375px viewport; campaign creation/copy, user search/filter, RU and FA RTL/digits, no horizontal overflow; initial request failure recovers through Retry. All browser figures were synthetic, with the fixture outside the repository. Production has no demo login/transport.
- Isolated PostgreSQL execution could not start in this managed root namespace: runuser cannot set groups and dropping UID is not permitted/mapped. PostgreSQL row-lock SQL is explicitly compiled/asserted; real two-session PostgreSQL verification and live Telegram/payment checks remain VPS validation steps. No production database was contacted.
- Scope ruling: campaign cards expose visitor/buyer/conversion counts, not per-campaign currency totals; this matches the user's minimal funnel request. Native-currency totals stay on Overview. The operational setup helper preserves other .env values and a private backup in a temporary-file smoke check.
- Final verification: 223 tests passed, Python compilation and JS syntax passed, RU/EN/FA frontend translation keys match, git whitespace check passed. Independent review findings were fixed with reproducing regression tests; browser retry and copy/create interactions were checked on synthetic data.
