# FCBot

Monitor-only FirstCry watcher for Hot Wheels and Majorette die-cast cars. It sends a
Telegram alert when a product is **in stock and deliverable to the configured pincode**
(default `201012`). It never adds anything to a cart and never buys anything.

## What it checks

- Two fixed Majorette product pages plus every Hot Wheels product discovered on the
  Hot Wheels brand listing page.
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

State lives in `firstcry_state.json`, keyed by FirstCry product id (so URL variants of the
same product cannot alert twice).

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
| `HOT_WHEELS_MAX_SCROLLS` | `8` | Listing scroll/Load More passes |
| `SKIP_HOT_WHEELS_DISCOVERY`, `MAX_PRODUCTS` | – | Debugging shortcuts |

A full pass over ~215 products takes roughly 1.5 minutes, which fits the 5-minute schedule
in `.github/workflows/monitor.yml`.
