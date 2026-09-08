"""
FirstCry restock monitor -> Telegram alert.

WHAT THIS DOES
  Polls one or more FirstCry product pages. The moment a page flips from
  "Notify Me" (out of stock) to "Add to Cart" (in stock), it pings you on
  Telegram. It does NOT add to cart or check out for you -- you still buy
  manually, just seconds after it goes live instead of whenever you next
  happen to refresh.

SETUP
  1. pip install requests
  2. Create a Telegram bot: message @BotFather on Telegram, send /newbot,
     copy the token it gives you.
  3. Get your chat_id: send your new bot any message, then open
     https://api.telegram.org/bot<TOKEN>/getUpdates in a browser and read
     the "chat":{"id": ...} value from the JSON.
  4. Fill in TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID and PRODUCT_URLS below
     (or set the first two as environment variables of the same name so
     you don't have to hardcode secrets in the file).
  5. Run it yourself with: python3 firstcry_monitor.py
     ...or let GitHub Actions run it on a schedule with no server of your
     own -- see monitor.yml (it sets RUN_ONCE=1 so each run checks once
     and exits, instead of looping forever the way it does on a VM).

PRODUCT_URLS is a list of {"label": ..., "url": ...} dicts, one per item you
want watched, mixing as many brands as you like. The label is just what
shows up in the Telegram message. Grab a url from the product's own page
(pattern: .../product-name/12345678/product-detail). Browse a brand's full
range to find things worth watching:
  Hot Wheels : https://www.firstcry.com/hot-wheels/toy-cars,-trains-and-vehicles/5/94/113
  Matchbox   : https://www.firstcry.com/matchbox/toy-cars,-trains-and-vehicles/5/94/161
  Majorette  : https://www.firstcry.com/majorette/toy-cars,-trains-and-vehicles/5/94/1335
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time

import requests

# ---------------- CONFIG ----------------
PRODUCT_URLS = [
    {"label": "TOYOTA SPRINTER", "url": "https://www.firstcry.com/majorette/majorette-toyota-ae86-gt-apex-jdm-legends-premium-die-cast-model-car-with-detailed-design-white/24178920/product-detail"},
    # Examples -- delete the line above and uncomment/edit these once you've
    # picked real products from the brand links in the docstring above:
    {"label": "Hot Wheels Silver Series ZAMAC", "url": "https://www.firstcry.com/hot-wheels/hot-wheels-silver-series-zamac-die-cast-free-wheel-toy-car-silver/22912948/product-detail"},
    {"label": "Majorette Mitsubishi Lancer Evolution 9 JDM Legends", "url": "https://www.firstcry.com/majorette/majorette-mitsubishi-lancer-evolution-9-jdm-legends-premium-die-cast-car-off-white/24178926/product-detail"},
    {"label": "Hotwheels civic Team transport", "url": "https://www.firstcry.com/hot-wheels/hot-wheels-premium-legends-tour-vehcile-die-cast-free-wheel-car-pack-of-2-white/22912945/product-detail"},
]

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PASTE_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "PASTE_CHAT_ID_HERE")

CHECK_INTERVAL_SECONDS = 45   # base delay between full passes over all URLs
JITTER_SECONDS = 15           # +/- randomness so requests aren't robotically regular
STATE_FILE = "firstcry_state.json"
REQUEST_TIMEOUT = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
LOW_STOCK_RE = re.compile(r"\b(\d+)\s+Left\b", re.IGNORECASE)
# -----------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("firstcry-monitor")

_warned_ambiguous: set[str] = set()


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        log.error("Failed to save state: %s", e)


def notify(message: str) -> None:
    log.info(message)
    if "PASTE_" in TELEGRAM_BOT_TOKEN or "PASTE_" in TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured yet -- skipping push notification.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        log.error("Telegram send failed: %s", e)


def check_stock(session: requests.Session, url: str) -> tuple[str, str | None]:
    """Returns (status, low_stock_count). status is 'in_stock', 'out_of_stock' or 'unknown'."""
    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Fetch failed for %s: %s", url, e)
        return "unknown", None

    page = resp.text
    has_cart = "ADD TO CART" in page.upper()
    has_notify = "NOTIFY ME" in page.upper()
    low_stock_match = LOW_STOCK_RE.search(page)

    if has_cart and not has_notify:
        return "in_stock", (low_stock_match.group(1) if low_stock_match else None)
    if has_notify and not has_cart:
        return "out_of_stock", None

    # Ambiguous: both markers present (a "related products" carousel can
    # carry its own Add to Cart buttons) or neither (page may be rendered
    # client-side by JS, in which case plain requests won't see it --
    # switch this fetch to Playwright/Selenium instead). Dump HTML once
    # per URL so you can inspect what the script actually received.
    if url not in _warned_ambiguous:
        safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", url)[-60:]
        debug_file = f"debug_{safe_name}.html"
        try:
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(page)
            log.warning("Ambiguous stock status for %s -- wrote %s for inspection.", url, debug_file)
        except OSError as e:
            log.error("Could not write debug file: %s", e)
        _warned_ambiguous.add(url)
    return "unknown", None


def main() -> None:
    if not PRODUCT_URLS or "REPLACE-ME" in PRODUCT_URLS[0]["url"]:
        log.error("Edit PRODUCT_URLS with real FirstCry product-detail links first.")
        return

    # RUN_ONCE=1 -> check every URL one time and exit. Use this when something
    # else (like GitHub Actions' cron schedule) is responsible for the
    # repeating, rather than this process looping forever like it does on a VM.
    run_once = os.environ.get("RUN_ONCE") == "1"

    state = load_state()
    log.info("Watching %d product(s).%s", len(PRODUCT_URLS), "" if run_once else " Ctrl+C to stop.")

    session = requests.Session()  # reused across checks: connection keep-alive, fewer handshakes

    while True:
        for item in PRODUCT_URLS:
            url = item["url"]
            label = item.get("label", url)
            status, low_stock = check_stock(session, url)

            url_state = state.get(url, {})
            if isinstance(url_state, str):
                # migrating from an older state file that stored a bare status string
                url_state = {"status": url_state, "last_low_stock": None}
            prev_status = url_state.get("status")
            prev_low_stock = url_state.get("last_low_stock")

            if status == "in_stock":
                if prev_status != "in_stock":
                    notify(f"\U0001F680 IN STOCK: {label}\n{url}")
                # Only re-alert on low stock if the count actually changed,
                # so it doesn't ping you every single poll while it sits at "2 left".
                if low_stock and int(low_stock) <= 3 and low_stock != prev_low_stock:
                    notify(f"\u26A0\uFE0F Only {low_stock} left: {label}\n{url}")
                    url_state["last_low_stock"] = low_stock
            elif status == "out_of_stock":
                # Clear this so a future restock at the same low count still alerts fresh.
                url_state["last_low_stock"] = None

            if status != "unknown":
                url_state["status"] = status
                state[url] = url_state

        save_state(state)
        if run_once:
            break
        sleep_time = max(5.0, CHECK_INTERVAL_SECONDS + random.uniform(-JITTER_SECONDS, JITTER_SECONDS))
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
PYEOF
python3 -m py_compile /mnt/user-data/outputs/firstcry_monitor.py && echo "OK: compiles cleanly"
