"""
FirstCry restock monitor -> Telegram alert.
Uses Playwright/Chromium so dynamically rendered FirstCry pages can be checked.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ---------------- CONFIG ----------------

PRODUCT_URLS = [
    {
        "label": "TOYOTA SPRINTER",
        "url": "https://www.firstcry.com/majorette/majorette-toyota-ae86-gt-apex-jdm-legends-premium-die-cast-model-car-with-detailed-design-white/24178920/product-detail",
    },
    {
        "label": "Hot Wheels Silver Series ZAMAC",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-silver-series-zamac-die-cast-free-wheel-toy-car-silver/22912948/product-detail",
    },
    {
        "label": "Majorette Mitsubishi Lancer Evolution 9 JDM Legends",
        "url": "https://www.firstcry.com/majorette/majorette-mitsubishi-lancer-evolution-9-jdm-legends-premium-die-cast-car-off-white/24178926/product-detail",
    },
    {
        "label": "Hot Wheels Premium Legends Tour Vehicle 2-Pack",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-premium-legends-tour-vehcile-die-cast-free-wheel-car-pack-of-2-white/22912945/product-detail",
    },
]

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", "PASTE_TOKEN_HERE"
)
TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID", "PASTE_CHAT_ID_HERE"
)

STATE_FILE = "firstcry_state.json"
REQUEST_TIMEOUT = 15

# -----------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
)
log = logging.getLogger("firstcry-monitor")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
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

    if (
        "PASTE_" in TELEGRAM_BOT_TOKEN
        or "PASTE_" in TELEGRAM_CHAT_ID
    ):
        log.warning("Telegram is not configured.")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:
        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            },
            timeout=REQUEST_TIMEOUT,
        )

        if not response.ok:
            log.error(
                "Telegram returned HTTP %s: %s",
                response.status_code,
                response.text,
            )

    except requests.RequestException as e:
        log.error("Telegram send failed: %s", e)


def check_stock(page, url: str, label: str):
    """
    Load the product in Chromium and determine whether the product
    appears to be in stock.

    Returns:
        ("in_stock", low_stock_count)
        ("out_of_stock", None)
        ("unknown", None)
    """

    log.info("Checking: %s", label)

    try:
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        # Give FirstCry's JavaScript time to render the product area.
        page.wait_for_timeout(5000)

    except PlaywrightTimeoutError:
        log.warning("Page load timed out: %s", url)
        # Continue anyway; the page may have loaded enough.

    except Exception as e:
        log.error("Browser failed for %s: %s", url, e)
        return "unknown", None

    # Get only visible text from the rendered page.
    try:
        body_text = page.locator("body").inner_text(timeout=10000)
        upper_text = body_text.upper()
    except Exception as e:
        log.error("Could not read rendered page: %s", e)
        return "unknown", None

    # ------------------------------------------------------------
    # DEBUG INFORMATION
    # ------------------------------------------------------------

    add_visible = "ADD TO CART" in upper_text
    notify_visible = "NOTIFY ME" in upper_text

    log.info(
        "%s -> visible ADD TO CART=%s, visible NOTIFY ME=%s",
        label,
        add_visible,
        notify_visible,
    )

    # Print visible buttons containing useful stock-related words.
    try:
        buttons = page.locator("button:visible").all_inner_texts()

        useful_buttons = [
            b.strip()
            for b in buttons
            if any(
                word in b.upper()
                for word in [
                    "CART",
                    "BUY",
                    "NOTIFY",
                    "AVAILABLE",
                    "STOCK",
                ]
            )
        ]

        if useful_buttons:
            log.info(
                "%s -> visible relevant buttons: %s",
                label,
                useful_buttons,
            )
        else:
            log.info(
                "%s -> no obvious stock buttons found",
                label,
            )

    except Exception as e:
        log.warning("Could not inspect buttons: %s", e)

    # ------------------------------------------------------------
    # STOCK DETECTION
    # ------------------------------------------------------------

    # Look for low-stock messages.
    low_stock = None

    match = re.search(
        r"\b(\d+)\s+LEFT\b",
        upper_text,
        re.IGNORECASE,
    )

    if match:
        low_stock = match.group(1)

    # If only Notify Me is visible, treat as out of stock.
    if notify_visible and not add_visible:
        log.info("%s -> OUT OF STOCK", label)
        return "out_of_stock", None

    # If Add to Cart is visible, treat as in stock.
    if add_visible:
        log.info("%s -> IN STOCK", label)
        return "in_stock", low_stock

    # Neither was found.
    log.warning(
        "%s -> Could not determine stock status",
        label,
    )

    return "unknown", None


def main() -> None:

    run_once = os.environ.get("RUN_ONCE") == "1"

    state = load_state()

    log.info(
        "Watching %d product(s).",
        len(PRODUCT_URLS),
    )

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        context = browser.new_context(
            viewport={
                "width": 1280,
                "height": 900,
            },
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        for item in PRODUCT_URLS:

            url = item["url"]
            label = item["label"]

            status, low_stock = check_stock(
                page,
                url,
                label,
            )

            url_state = state.get(url, {})

            if isinstance(url_state, str):
                url_state = {
                    "status": url_state,
                    "last_low_stock": None,
                }

            previous_status = url_state.get("status")
            previous_low_stock = url_state.get(
                "last_low_stock"
            )

            # ------------------------------------------------
            # IN STOCK
            # ------------------------------------------------

            if status == "in_stock":

                # First time seeing an item OR transition
                # from out-of-stock to in-stock.
                if previous_status != "in_stock":
                    notify(
                        f"🚀 IN STOCK: {label}\n{url}"
                    )

                # Low-stock notification.
                if (
                    low_stock
                    and int(low_stock) <= 3
                    and low_stock != previous_low_stock
                ):
                    notify(
                        f"⚠️ Only {low_stock} left: "
                        f"{label}\n{url}"
                    )

                    url_state["last_low_stock"] = low_stock

            # ------------------------------------------------
            # OUT OF STOCK
            # ------------------------------------------------

            elif status == "out_of_stock":

                url_state["last_low_stock"] = None

            # ------------------------------------------------
            # SAVE STATE
            # ------------------------------------------------

            if status != "unknown":

                url_state["status"] = status
                state[url] = url_state

        save_state(state)

        browser.close()

    if not run_once:

        while True:
            time.sleep(60)


if __name__ == "__main__":
    main()
