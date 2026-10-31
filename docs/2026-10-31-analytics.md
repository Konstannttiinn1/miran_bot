# ORION analytics Mini App

## User flow

An administrator or dealer opens **Analytics** from their bot menu. Three tabs:

- **Overview:** confirmed subscription purchases/renewals, traffic add-ons, buyers and native-currency sales. Periods are Today, Yesterday, Last 7/30 days, This/Previous calendar month and All time, in Moscow time.
- **Users:** paying/unpaid/all, search by username/Telegram ID, current expiry and last purchased subscription term/device count. Dealer-managed subscriptions use a separate selector because they may have no Telegram account in the bot.
- **Campaigns:** create a named campaign, copy its `https://t.me/<bot>?start=ad_<code>` link, see unique launches, attributed buyers and conversion, and open the campaign's users. Campaign metrics cover its lifetime.

No click counter, landing page, ad bonus, ad budget or referral payout was added. The UI supports Russian, English and Persian/RTL; administrators use Russian and dealers their saved bot language.

## Counting rules

Repeat starts count once per campaign/user. The first valid campaign remains the acquisition source. A subsequent link can record a visit but cannot steal that source. Purchases before the first attribution are never retrospectively credited. The campaign and the actual processing dealer are separate dimensions.

Only confirmed sales count. Free trials, pending/rejected invoices, refundable reservations and balance top-ups are excluded. Stars/crypto already received count even if automatic VPN provisioning fails. Dealer sales count after successful completion; managed purchases, renewals and traffic add-ons are included.

USD is the amount charged to the dealer's prepaid balance, not client retail and not a fresh cash deposit. USDT and XTR remain separate currencies; original bank retail Toman quotes are shown separately where preserved. These are sales amounts, not net profit. No current exchange-rate conversion is used to reconstruct old retail amounts.

New money events use their confirmation time and a unique immutable source key. Historical successful orders and dealer logs are imported idempotently. Bank confirmation logs have a known time; old crypto/Stars and dealer renewal reservations may have only an approximate date. The overview discloses the number of these records. Old deleted operational history cannot be reconstructed.

Live first attribution and payment recording serialize on the same customer row in PostgreSQL. Historical imports do not hold those live customer locks across a batch. Log event keys and managed customer identities include creation time so SQLite ID reuse after deletion cannot silently drop a new sale or inherit another customer's paid flag.

Five additive tables are created by `init_db`: `advertising_campaigns`, `campaign_visits`, `campaign_attributions`, `order_price_snapshots`, `analytics_sales`. No existing table is altered and no legacy order price/status/balance is rewritten. Ledger/source identifiers are independent of deletable operational users/orders so existing deletion continues to work.

## Access

The backend validates the HMAC and auth_date from Telegram `initData`, checks ADMIN_IDS or the current dealer role/blocked state, and verifies scope for every API call. Ordinary customers cannot open analytics. An administrator can select a dealer; a dealer sees their own sales/campaigns and related users. Campaign visitor lists include whether a purchase was attributed to this campaign.

The production frontend contains no demo login/data. Static files may be loaded publicly, but all `/api/*` data requires the signed Telegram session. Sessions expire after `ANALYTICS_INIT_DATA_MAX_AGE` seconds (default 3600); reopen the Mini App to renew. Tokens/initData must not be placed in URLs, logs or frontend configuration. No permissive CORS or third-party analytics is used.

## VPS installation

Keep the bot in `/root/miran_bot`, branch `feat/dealer-tools-promos`, service `miran-bot`. Use the same `.env` and Python venv for the new web service. The service listens only on `127.0.0.1:8091`; an existing HTTPS reverse proxy forwards to it. Do not replace a working Caddy/Nginx configuration without first checking what hosts it serves.

1. Fast-forward to the reviewed commit with a backup branch, preserving local/untracked files. Install missing dependencies with `venv/bin/python -m pip install -r requirements.txt` (no `--upgrade`).
2. Initialize additive tables and historical data before restarting either process:

   ```bash
   venv/bin/python - <<'PY'
   import asyncio
   from app.database.engine import init_db
   asyncio.run(init_db())
   PY
   ```

3. Install `deploy/miran-analytics.service` into `/etc/systemd/system/`, run `systemctl daemon-reload`, then `systemctl enable --now miran-analytics`.
4. Verify `curl --fail http://127.0.0.1:8091/health`. An unauthenticated request to `/api/me` must return 401.
5. Point an available HTTPS domain at this VPS and proxy it to `127.0.0.1:8091`. Example Caddy host, using the actual chosen domain:

   ```caddy
   analytics.example.com {
       encode zstd gzip
       reverse_proxy 127.0.0.1:8091
   }
   ```

   An existing host can alternatively use a path. Strip that prefix in the proxy (Caddy `handle_path /analytics/*`) and set the URL to `https://actual-host/analytics/`. Assets and API requests use paths relative to the Mini App URL.

6. After HTTPS works, run `venv/bin/python scripts/configure_analytics.py --url https://ACTUAL-HOST/`. It changes only `ANALYTICS_WEBAPP_URL`, backs up `.env` and does not print other settings. Restart `miran-bot` and `miran-analytics`; reopen `/start` to see **Analytics**. No extra BotFather Main Mini App setup is required for the menu's inline Web App button.
7. Open it from Telegram as administrator; verify a normal customer is denied. Create a campaign, launch its link from a separate real customer account, and make one controlled payment to verify production attribution. Local tests do not prove live Telegram delivery or real payment/provider behavior.

Without `ANALYTICS_WEBAPP_URL` the launch button stays hidden. Do not point the real bot at the isolated browser QA fixture used during development. Stop/disable `miran-analytics` and clear only this URL to remove the Mini App entry point; retained analytical history need not be deleted.

## Verification

Run `venv/bin/python -m pytest -q`, `venv/bin/python -m compileall -q app main.py scripts/configure_analytics.py`, `node --check app/web_static/analytics.js` and `git diff --check`. New tests cover HMAC tampering/expiry, role boundaries, currency units, immutable prices, unique visits, first attribution, duplicate payment callbacks, dealer purchase/renewal/add-ons, backfill and retained user deletion.

Reference patterns: BEDOLAGA-DEV's campaign registration/conversion schemas and duplicate attribution tests; Telegram's official Mini App HMAC validation documentation. No Bedolaga code was copied.

Browser QA used synthetic data in a separate fixture, including the Russian and Persian mobile layouts, real UI create/copy/filter/search actions and retry after a simulated initial API failure. An isolated PostgreSQL daemon could not be started in the managed environment; the lock/query contract is checked against PostgreSQL compilation, and live two-session PostgreSQL/Telegram/payment verification remains part of VPS rollout. No production traffic was generated during development.
