# FCBot

FirstCry watcher for Hot Wheels and Majorette die-cast cars. It sends a
Telegram alert when a product is **in stock and deliverable to the configured pincode**
(default `201012`). When `BUY_ENABLED=1`, it also logs in with `FIRSTCRY_PHONE` and
places a separate order for each available product.

## What it checks

- A fixed list of product pages, `PRODUCTS` at the top of `firstcry_monitor.py`
  (2 Majorette + 9 Hot Wheels today). Add or remove entries there; nothing is
  discovered automatically.
- Stock comes from the buy box: a visible `ADD TO CART` button means in stock, a visible
  `NOTIFY ME` button (or the sold-out overlay) means out of stock.
- Delivery comes from FirstCry's own serviceability endpoint (`checkdeliveryinfo`), which
  the product page calls for the pincode cookie the bot sets. `Result.IsServicable > 0`
  means orderable for that pincode. The visible delivery block is only a fallback.

A product is `available` only when both signals are positive. When a signal cannot be read
the check is `unknown`, which never changes the notification memory.

## Notification rules

- Alert once when a product becomes available.
- No further alerts while it stays available.
- Going unavailable clears the flag, so the next transition to available alerts again.
- `unknown` checks never clear the flag, so a flaky page load cannot cause a repeat alert.
- An entry is only marked as notified after Telegram accepts the message, so failed sends
  are retried on the next run.
- The first run with no state file records the baseline instead of alerting for everything
  already in stock.
- Once an order is placed for a product it is marked `ordered` and is never purchased
  again; availability alerts for it continue to follow the rules above.

State lives in `firstcry_state.json`, keyed by FirstCry product id (so URL variants of the
same product cannot alert twice).

## Auto-purchase mode

With `BUY_ENABLED=1` (set in the workflow), each run:

1. When a product is available, it logs in via `https://www.firstcry.com/m/login`
   using `FIRSTCRY_PHONE`. The login session is saved to `firstcry_session.json`, so
   OTP login is only needed again if the session expires.
2. For every available, not-yet-ordered product — one order per product, never
   combined — it empties the cart, adds the product, opens
   `checkout.firstcry.com/checkout`, selects the saved address, leaves the default
   (saved card) payment untouched, and places the order.
3. Purchases run as background tasks in separate tabs, so availability checks keep
   running meanwhile. Orders execute one at a time (`BUY_LOCK`) because the cart is
   account-side — parallel checkouts could merge items into a single order.
4. Whenever an OTP screen appears (login or card/order verification), it sends a
   Telegram message asking for the code and polls your reply (up to `OTP_WAIT_S`,
   default 300s). OTP prompts never name the product.
5. A failed attempt (e.g. OTP not answered in time) waits `BUY_COOLDOWN_MINUTES`
   (default 30) before retrying, so your phone isn't spammed every 5 minutes.

The session file is encrypted with `SESSION_SECRET` (`openssl aes-256-cbc`) into
`firstcry_session.json.enc`, which the workflow keeps in the Actions cache alongside
the state file. On checkout failure a `buy_<pid>.png` screenshot is uploaded as the
`checkout-debug` artifact.

Required secrets in GitHub: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`FIRSTCRY_PHONE`, `SESSION_SECRET` (any random string).

**Note:** `empty_cart` removes anything already sitting in the cart before ordering —
don't keep items you care about in the FirstCry cart. Overlapping runs are prevented by
the workflow's `concurrency` group.

## Running locally

```bash
pip install requests playwright pytest
python -m playwright install --with-deps chromium
RUN_ONCE=1 python firstcry_monitor.py     # one pass; omit RUN_ONCE to loop
python -m pytest -q                        # state-machine and API-parsing tests
```

Without `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` the bot logs the alerts it would send.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | – | Telegram delivery (GitHub Secrets in CI) |
| `PINCODE` | `201012` | Delivery pincode to test |
| `RUN_ONCE` | – | `1` for a single pass (used by the workflow) |
| `LOOP_INTERVAL_S` | `300` | Sleep between passes when looping |
| `CONCURRENCY` | `6` | Product pages checked in parallel |
| `PRODUCT_DEADLINE_S` | `18` | Max wait per product for stock + delivery signals |
| `MAX_NOTIFICATIONS_PER_RUN` | `10` | Guard against alert storms |
| `BUY_ENABLED` | – | `1` turns on auto-purchase (set in the workflow) |
| `FIRSTCRY_PHONE` | – | Mobile number used for OTP login (GitHub Secret) |
| `SESSION_SECRET` | – | Passphrase that encrypts the cached login session |
| `SESSION_FILE` | `firstcry_session.json` | Playwright `storage_state` location |
| `OTP_WAIT_S` | `300` | Max wait for a Telegram OTP reply |
| `BUY_COOLDOWN_MINUTES` | `30` | Delay before retrying a failed purchase |

A pass over the current 11 products takes about 25 seconds, well inside the 5-minute
schedule in `.github/workflows/monitor.yml`.
