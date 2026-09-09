from __future__ import annotations

import json
import logging
import os
import re

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

MAJORETTE_PRODUCT_URLS = [
    {
        "label": "TOYOTA SPRINTER",
        "url": "https://www.firstcry.com/majorette/majorette-toyota-ae86-gt-apex-jdm-legends-premium-die-cast-model-car-with-detailed-design-white/24178920/product-detail",
    },
    {
        "label": "Majorette Mitsubishi Lancer Evolution 9 JDM Legends",
        "url": "https://www.firstcry.com/majorette/majorette-mitsubishi-lancer-evolution-9-jdm-legends-premium-die-cast-car-off-white/24178926/product-detail",
    },
]

HOT_WHEELS_CATEGORY_URL = (
    "https://www.firstcry.com/toy-cars,-trains-and-vehicles/cars-and-jeeps/"
    "hot-wheels?cid=5&scid=94&type=t1-7973&brand=113"
)

HOT_WHEELS_MAX_SCROLLS = 6
PINCODE = "201012"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PASTE_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "PASTE_CHAT_ID_HERE")

STATE_FILE = "firstcry_state.json"
REQUEST_TIMEOUT = 15

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("firstcry-monitor")


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
        log.warning("Telegram is not configured.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        response = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
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


def check_delivery(page) -> bool:
    try:
        inputs = page.locator("input").all()
        pin_input = None

        for inp in inputs:
            try:
                placeholder = (inp.get_attribute("placeholder") or "").lower()
                value = (inp.get_attribute("value") or "").lower()
                name = (inp.get_attribute("name") or "").lower()

                if (
                    "pin" in placeholder
                    or "pincode" in placeholder
                    or "pin" in name
                    or "pincode" in name
                    or "enter pin code" in value
                ):
                    if inp.is_visible():
                        pin_input = inp
                        break
            except Exception:
                continue

        if pin_input is None:
            pin_input = page.get_by_placeholder(
                "Enter Pin Code",
                exact=False,
            ).first

        if not pin_input.is_visible():
            log.warning("Could not find visible pincode input.")
            return False

        pin_input.fill(PINCODE)

        check_button = pin_input.locator(
            "xpath=following::button[normalize-space()='CHECK'][1]"
        )

        if check_button.count() == 0 or not check_button.first.is_visible():
            check_button = page.get_by_text("CHECK", exact=True).last

        check_button.click(timeout=5000)

        page.wait_for_timeout(2500)

        text = page.locator("body").inner_text(timeout=10000).upper()

        unavailable_phrases = [
            "NOT DELIVERABLE",
            "NOT AVAILABLE FOR DELIVERY",
            "DELIVERY NOT AVAILABLE",
            "CANNOT BE DELIVERED",
            "UNABLE TO DELIVER",
            "NOT SERVICEABLE",
        ]

        available_phrases = [
            "DELIVERY BY",
            "GET IT BY",
            "DELIVERED BY",
            "DELIVERY AVAILABLE",
        ]

        if any(phrase in text for phrase in unavailable_phrases):
            log.info("Pincode %s -> NOT DELIVERABLE", PINCODE)
            return False

        if any(phrase in text for phrase in available_phrases):
            log.info("Pincode %s -> DELIVERABLE", PINCODE)
            return True

        log.warning(
            "Could not determine delivery status for pincode %s.",
            PINCODE,
        )
        return False

    except Exception as e:
        log.error("Pincode check failed: %s", e)
        return False


def check_stock_and_delivery(
    page,
    url: str,
    label: str,
) -> tuple[str, bool]:
    try:
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        page.wait_for_timeout(5000)

    except PlaywrightTimeoutError:
        log.warning("Page load timed out: %s", url)

    except Exception as e:
        log.error("Browser failed for %s: %s", url, e)
        return "unknown", False

    try:
        upper_text = page.locator("body").inner_text(timeout=10000).upper()

    except Exception as e:
        log.error("Could not read page for %s: %s", label, e)
        return "unknown", False

    add_visible = "ADD TO CART" in upper_text
    notify_visible = "NOTIFY ME" in upper_text

    if notify_visible and not add_visible:
        log.info("%s -> OUT OF STOCK", label)
        return "out_of_stock", False

    if not add_visible:
        log.warning("%s -> could not determine stock status", label)
        return "unknown", False

    log.info("%s -> IN STOCK", label)

    deliverable = check_delivery(page)

    if not deliverable:
        log.info(
            "%s -> IN STOCK but not deliverable to %s",
            label,
            PINCODE,
        )

    return "in_stock", deliverable


def discover_hotwheels_products(page) -> list[dict]:
    try:
        page.goto(
            HOT_WHEELS_CATEGORY_URL,
            wait_until="domcontentloaded",
            timeout=30000,
        )
        page.wait_for_timeout(3000)

    except Exception as e:
        log.error("Could not load Hot Wheels category page: %s", e)
        return []

    for _ in range(HOT_WHEELS_MAX_SCROLLS):
        try:
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)

            for text in ("Load More", "Show More", "View More"):
                btn = page.get_by_text(text, exact=False)

                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(1500)

        except Exception:
            pass

    try:
        raw_links = page.eval_on_selector_all(
            "a[href*='/product-detail']",
            "els => els.map(el => ({href: el.href, text: el.innerText}))",
        )

    except Exception as e:
        log.error("Could not read product links: %s", e)
        return []

    discovered = {}

    for entry in raw_links:
        href = entry.get("href") or ""

        if "/hot-wheels/" not in href:
            continue

        if href in discovered:
            continue

        text = (entry.get("text") or "").strip().split("\n")[0]

        discovered[href] = (
            text[:80]
            if text
            else href.rsplit("/", 2)[-2].replace("-", " ")
        )

    log.info(
        "Discovered %d Hot Wheels product page(s).",
        len(discovered),
    )

    return [
        {"label": label, "url": url}
        for url, label in discovered.items()
    ]


def process_item(
    page,
    state: dict,
    url: str,
    label: str,
) -> None:
    status, deliverable = check_stock_and_delivery(
        page,
        url,
        label,
    )

    if status == "unknown":
        return

    previous = state.get(url)

    if isinstance(previous, dict):
        previous_status = previous.get("status")
        previous_deliverable = previous.get("deliverable", False)
    else:
        previous_status = previous
        previous_deliverable = False

    current_available = (
        status == "in_stock"
        and deliverable
    )

    previous_available = (
        previous_status == "in_stock"
        and previous_deliverable
    )

    if current_available and not previous_available:
        notify(
            f"🚀 IN STOCK + DELIVERABLE: {label}\n"
            f"Pincode: {PINCODE}\n"
            f"{url}"
        )

    state[url] = {
        "status": status,
        "deliverable": deliverable,
    }


def main() -> None:
    run_once = os.environ.get("RUN_ONCE") == "1"
    state = load_state()

    log.info(
        "Watching %d Majorette item(s) and auto-discovered Hot Wheels listings.",
        len(MAJORETTE_PRODUCT_URLS),
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        while True:
            for item in MAJORETTE_PRODUCT_URLS:
                process_item(
                    page,
                    state,
                    item["url"],
                    item["label"],
                )

            for item in discover_hotwheels_products(page):
                process_item(
                    page,
                    state,
                    item["url"],
                    item["label"],
                )

            save_state(state)

            if run_once:
                break

        browser.close()


if __name__ == "__main__":
    main()