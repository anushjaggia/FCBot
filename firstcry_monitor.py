from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from playwright.async_api import (
    BrowserContext,
    Page,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

# The products to watch. Add or remove entries here; nothing is discovered
# automatically, so a run only costs as much as this list.
PRODUCTS = [
    {
        "label": "Majorette Toyota Sprinter AE86 GT Apex JDM Legends",
        "url": "https://www.firstcry.com/majorette/majorette-toyota-ae86-gt-apex-jdm-legends-premium-die-cast-model-car-with-detailed-design-white/24178920/product-detail",
    },
    {
        "label": "Majorette Mitsubishi Lancer Evolution 9 JDM Legends",
        "url": "https://www.firstcry.com/majorette/majorette-mitsubishi-lancer-evolution-9-jdm-legends-premium-die-cast-car-off-white/24178926/product-detail",
    },
    {
        "label": "Majorette Mercedes-AMG GT63 Deluxe Die-Cast - Grey",
        "url": "https://www.firstcry.com/majorette/majorette-mercedes-amg-gt63-deluxe-die-cast-toy-car-grey/22063529/product-detail",
    },
    {
        "label": "Hot Wheels Pagani Utopia 1/5 Die-Cast - Red",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-pagani-utopia-1-5-die-cast-red/24342822/product-detail",
    },
    {
        "label": "Hot Wheels Premium Fast & Furious Toyota Supra - Orange",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-cars-die-cast-free-wheel-premium-fast-and-furious-toyota-supra-car-for-adult-collectors-orange/24390965/product-detail",
    },
    {
        "label": "Hot Wheels 1995 Mitsubishi Eclipse - Grey",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-die-cast-free-wheel-1995-mitsubishi-eclipse-car-grey/24342826/product-detail",
    },
    {
        "label": "Hot Wheels Premium Collector Display Set, 3 Cars & 1 Transporter",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-premium-collector-display-set-3-cars-and-1-transporter-sky-blue/24323023/product-detail",
    },
    {
        "label": "Hot Wheels Lamborghini Veneno - Black",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-lamborghini-veneno-die-cast-model-car-black/24342823/product-detail",
    },
    {
        "label": "Hot Wheels Euro Style Die Cast Pack of 6",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-euro-style-die-cast-free-wheel-toy-car-pack-of-6-multicolor/23700381/product-detail",
    },
    {
        "label": "Hot Wheels Silver Series 1/5 Lamborghini Countach LP 500 QV - White",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-1-5-silver-series-vintage-club-lamborghini-countach-lp-500-qv-die-cast-car-white/24390971/product-detail",
    },
    {
        "label": "Hot Wheels Street Shaker (202/250) - Blue",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-die-cast-street-shaker-toy-car-202-250-with-free-wheel-feature-blue/24246594/product-detail",
    },
    {
        "label": "Hot Wheels '20 Dodge Charger Hellcat (134/250) - Grey",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-20-dodge-charger-hellcat-134-250-die-cast-toy-car-grey/22548066/product-detail",
    },
    {
        "label": "Hot Wheels '07 Honda Civic Type R Kousoku Hauler - White",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-die-cast-free-wheels-07-honda-civic-type-r-kousoku-hauler-camion-de-transport-silver-car-transport-truck-white/22848387/product-detail",
    },
    {
        "label": "Hot Wheels Color Shifters Nissan Skyline GT-R R32 - Red",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-color-shifters-nissan-skyline-gt-r-r32-car-toy-red/21252872/product-detail",
    },
    {
        "label": "Hot Wheels Premium Fast & Furious Lexus LFA - Grey",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-cars-die-cast-models-premium-fast-and-furious-lexus-lfa-car-for-adult-collectors-grey/24390966/product-detail",
    },
    {
        "label": "Hot Wheels Premium Fast & Furious Mercedes-Benz SLS AMG Coupe Black Series - White",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-cars-premium-fast-and-furious-mercedes-benz-sls-amg-coupe-black-series-serie-car-for-adult-collectors-white/24390963/product-detail",
    },
    {
        "label": "Hot Wheels 1970 Dodge Charger R/T - Grey",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-die-cast-free-wheel-1970-dodge-charger-r-t-l-grey/24342828/product-detail",
    },
    {
        "label": "Hot Wheels Silver Series Zamac - Silver",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-silver-series-zamac-die-cast-free-wheel-toy-car-silver/22912948/product-detail",
    },
    {
        "label": "Hot Wheels Ferrari LaFerrari 5/5 - Yellow",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-ferrari-laferrari-5-5-die-cast-model-car-yellow/24342821/product-detail",
    },
    {
        "label": "Matchbox 2023 Toyota GR Supra - Grey",
        "url": "https://www.firstcry.com/matchbox/match-box-2023-toyota-gr-supra-car-grey/24382820/product-detail",
    },
    {
        "label": "Matchbox 1968 Ford Mustang Fastback - Green",
        "url": "https://www.firstcry.com/matchbox/match-box-1968-ford-mustang-fastback-car-green/24382815/product-detail",
    },
    {
        "label": "Hot Wheels Honda Odyssey (149/250) - Blue",
        "url": "https://www.firstcry.com/hot-wheels/hot-wheels-149-250-honda-odyssey-die-cat-free-wheel-toy-car-blue/23348662/product-detail",
    },
    {
    "name": "Hot Wheels Formula 1 Toy Cars 10 Pack - Multicolor",
    "url": "https://www.firstcry.com/hot-wheels/hot-wheels-formula-1-toy-cars-10-pack-1-64-scale-die-cast-free-wheel-race-cars-multicolor/24194173/product-detail",
    },
    {
    "name": "Hot Wheels 1989 Mercedes-Benz 560 SEC AMG - White",
    "url": "https://www.firstcry.com/hot-wheels/hot-wheels-1989-mercedes-benz-560-sec-amg-die-cast-free-wheel-toy-car-white/24390955/product-detail",
    },
    {
    "name": "Hot Wheels Scuderia Ferrari HP (120/250) - Red & Black",
    "url": "https://www.firstcry.com/hot-wheels/hot-wheels-scuderia-ferrarii-hp-120-250-die-cast-toy-car-red-and-black/22548067/product-detail",
    },
]

PINCODE = os.environ.get("PINCODE", "201012")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "6"))
MAX_NOTIFICATIONS_PER_RUN = int(os.environ.get("MAX_NOTIFICATIONS_PER_RUN", "10"))
STATE_FILE = os.environ.get("STATE_FILE", "firstcry_state.json")
RUN_ONCE = os.environ.get("RUN_ONCE") == "1"
LOOP_INTERVAL_S = int(os.environ.get("LOOP_INTERVAL_S", "300"))
STATE_VERSION = 2

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

REQUEST_TIMEOUT = 15
NAV_TIMEOUT_MS = 40000
PRODUCT_DEADLINE_S = float(os.environ.get("PRODUCT_DEADLINE_S", "18"))
POLL_INTERVAL_S = 0.4

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
BLOCKED_URL_PATTERNS = (
    "google-analytics.com",
    "analytics.google.com",
    "googletagmanager.com",
    "doubleclick.net",
    "facebook.net",
    "facebook.com",
    "criteo.com",
    "go-mpulse.net",
    "clarity.ms",
    "hotjar.com",
)

PRODUCT_ID_RE = re.compile(r"/(\d+)/product-detail")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger("firstcry-monitor")


@dataclass
class CheckResult:
    availability: str  # "available" | "unavailable" | "unknown"
    reason: str
    eta: str = ""
    title: str = ""


def product_id(url: str) -> str | None:
    match = PRODUCT_ID_RE.search(url)
    return match.group(1) if match else None


def canonical_url(url: str) -> str:
    return url.split("?", 1)[0].split("#", 1)[0]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> tuple[dict, bool]:
    """Returns (state, is_cold_start)."""
    if not os.path.exists(STATE_FILE):
        return {"version": STATE_VERSION, "items": {}}, True

    try:
        with open(STATE_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read %s (%s); starting from empty state.", STATE_FILE, e)
        return {"version": STATE_VERSION, "items": {}}, True

    if isinstance(raw, dict) and raw.get("version") == STATE_VERSION:
        raw.setdefault("items", {})
        return raw, not raw["items"]

    # Migrate the legacy {url: "in_stock"} / {url: {...}} format, keyed by product id.
    items: dict[str, dict] = {}
    if isinstance(raw, dict):
        for url, value in raw.items():
            pid = product_id(str(url))
            if not pid:
                continue
            if isinstance(value, dict):
                was_available = value.get("status") == "in_stock" and value.get("deliverable")
            else:
                was_available = value == "in_stock"
            items[pid] = {
                "url": canonical_url(str(url)),
                "label": "",
                "notified": bool(was_available),
                "last_availability": "available" if was_available else "unavailable",
                "last_checked": "",
            }
    log.info("Migrated %d legacy state entries.", len(items))
    return {"version": STATE_VERSION, "items": items}, False


def save_state(state: dict) -> None:
    try:
        tmp = f"{STATE_FILE}.tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        log.error("Failed to save state: %s", e)


def send_telegram(message: str) -> bool:
    log.info("NOTIFY: %s", message.replace("\n", " | "))

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram is not configured; message not sent.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if not response.ok:
            log.error("Telegram returned HTTP %s: %s", response.status_code, response.text)
            return False
        return True
    except requests.RequestException as e:
        log.error("Telegram send failed: %s", e)
        return False


async def block_heavy_resources(context: BrowserContext) -> None:
    async def route_handler(route):
        request = route.request
        if request.resource_type in BLOCKED_RESOURCE_TYPES:
            await route.abort()
            return
        if any(pattern in request.url for pattern in BLOCKED_URL_PATTERNS):
            await route.abort()
            return
        await route.continue_()

    await context.route("**/*", route_handler)


def parse_delivery_payload(payload: dict) -> tuple[bool | None, str]:
    """FirstCry's own serviceability API: IsServicable > 0 means orderable for the pincode."""
    result = payload.get("Result")
    if not isinstance(result, dict) or "IsServicable" not in result:
        return None, ""

    try:
        servicable = int(result.get("IsServicable") or 0)
    except (TypeError, ValueError):
        return None, ""

    eta = re.sub(r"<[^>]+>", "", str(result.get("ShippingDate") or "")).strip()
    return servicable > 0, eta


DELIVERABLE_TEXT_RE = re.compile(
    r"GET IT BY|DELIVERY BY|DELIVERED BY|SAME DAY DELIVERY|NEXT DAY DELIVERY", re.I
)
UNDELIVERABLE_TEXT_RE = re.compile(
    r"CAN.?T BE DELIVERED|CANNOT BE DELIVERED|NOT DELIVERABLE|NOT SERVICEABLE"
    r"|NOT AVAILABLE FOR DELIVERY|DELIVERY NOT AVAILABLE",
    re.I,
)


async def delivery_from_dom(page: Page) -> tuple[bool | None, str]:
    """Fallback when the serviceability API response was not observed."""
    section = page.locator("section.th-pincod")
    try:
        if await section.count() == 0 or not await section.first.is_visible():
            return None, ""

        pin_input = page.locator("section.th-pincod input.changepincode")
        if await pin_input.count() > 0:
            applied = (await pin_input.first.input_value()).strip()
            if applied and applied != PINCODE:
                log.warning("Page shows pincode %s instead of %s.", applied, PINCODE)
                return None, ""

        shipping = page.locator("section.th-pincod .shipping")
        if await shipping.count() == 0 or not await shipping.first.is_visible():
            return None, ""

        text = (await shipping.first.inner_text()).strip()
    except PlaywrightTimeoutError:
        return None, ""

    if UNDELIVERABLE_TEXT_RE.search(text):
        return False, ""
    if DELIVERABLE_TEXT_RE.search(text):
        return True, text.split("\n")[0]
    return None, ""


PAGE_SIGNALS_JS = """() => {
    const visible = (el) => !!(el && (el.offsetParent !== null || el.getClientRects().length > 0));
    const heading = document.querySelector('h1');
    return {
        addToCart: visible(document.querySelector('.acartGcartBtn .add_to_cart')),
        notifyMe: visible(document.querySelector('.notifymeBtn')),
        soldOut: visible(document.querySelector('.oosbg')),
        title: heading ? heading.innerText.trim().slice(0, 120) : '',
    };
}"""


async def read_page_signals(page: Page) -> dict:
    """Stock is read from the buy box: ADD TO CART vs the NOTIFY ME button."""
    try:
        signals = await page.evaluate(PAGE_SIGNALS_JS)
    except Exception:
        return {"in_stock": None, "title": ""}

    if signals.get("addToCart"):
        in_stock: bool | None = True
    elif signals.get("notifyMe") or signals.get("soldOut"):
        in_stock = False
    else:
        in_stock = None
    return {"in_stock": in_stock, "title": signals.get("title") or ""}


async def check_product(context: BrowserContext, url: str, label: str) -> CheckResult:
    pid = product_id(url)
    page = await context.new_page()
    delivery_seen = asyncio.Event()
    delivery: dict = {}

    async def on_response(response: Response) -> None:
        if "checkdeliveryinfo" not in response.url:
            return
        post_data = response.request.post_data or ""
        if PINCODE not in post_data:
            return
        if pid and f'"{pid}"' not in post_data and pid not in post_data:
            return
        try:
            payload = await response.json()
        except Exception:
            return
        deliverable, eta = parse_delivery_payload(payload)
        if deliverable is None:
            return
        delivery["deliverable"] = deliverable
        delivery["eta"] = eta
        delivery_seen.set()

    page.on("response", on_response)

    try:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            log.warning("Navigation timed out: %s", url)

        # Stop as soon as the page has answered both questions instead of waiting
        # a fixed amount of time on every product.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PRODUCT_DEADLINE_S
        in_stock: bool | None = None
        title = ""
        while True:
            signals = await read_page_signals(page)
            in_stock = signals["in_stock"]
            title = signals["title"] or title
            if in_stock is False:
                break  # out of stock: delivery is irrelevant
            if in_stock is True and delivery_seen.is_set():
                break
            if loop.time() >= deadline:
                break
            await asyncio.sleep(POLL_INTERVAL_S)

        if "deliverable" in delivery:
            deliverable, eta = delivery["deliverable"], delivery.get("eta", "")
        else:
            deliverable, eta = await delivery_from_dom(page)
    except Exception as e:  # navigation/browser level failure
        log.error("Check failed for %s: %s", label or url, e)
        return CheckResult("unknown", f"error: {e}")
    finally:
        try:
            await page.close()
        except Exception:
            pass

    if in_stock is False:
        return CheckResult("unavailable", "out of stock", title=title)
    if deliverable is False:
        reason = (
            f"in stock, not deliverable to {PINCODE}"
            if in_stock is True
            else f"not orderable for {PINCODE} (out of stock or not serviceable)"
        )
        return CheckResult("unavailable", reason, title=title)
    if in_stock is True and deliverable is True:
        return CheckResult("available", "in stock and deliverable", eta, title)
    if in_stock is True:
        return CheckResult("unknown", "in stock, delivery undetermined", title=title)
    return CheckResult("unknown", "stock undetermined", title=title)


def build_message(label: str, url: str, eta: str) -> str:
    lines = [f"🚀 IN STOCK + DELIVERABLE: {label}", f"Pincode: {PINCODE}"]
    if eta:
        lines.append(eta)
    lines.append(url)
    return "\n".join(lines)


def apply_result(
    state_items: dict,
    pid: str,
    label: str,
    url: str,
    result: CheckResult,
    cold_start: bool,
    budget: list[int],
) -> tuple[dict, str | None]:
    """Updates state and returns (state entry, message to send if any)."""
    url = canonical_url(url)
    entry = state_items.setdefault(
        pid,
        {"url": url, "label": label, "notified": False, "last_availability": "", "last_checked": ""},
    )
    entry["url"] = url
    if result.title:
        entry["label"] = result.title
    elif label and not entry.get("label"):
        entry["label"] = label
    entry["last_checked"] = now_iso()

    if result.availability == "unknown":
        # Never let a failed/ambiguous check reset the notification memory.
        entry["last_availability"] = entry.get("last_availability", "")
        return entry, None

    entry["last_availability"] = result.availability

    if result.availability == "unavailable":
        entry["notified"] = False
        return entry, None

    if entry.get("notified"):
        return entry, None

    if cold_start:
        # First run with no memory: record availability without a burst of alerts.
        entry["notified"] = True
        entry["last_notified"] = now_iso()
        return entry, None

    if budget[0] <= 0:
        log.warning("Notification budget exhausted; %s will be reported next run.", label)
        return entry, None

    budget[0] -= 1
    return entry, build_message(entry.get("label") or label, url, result.eta)


async def run_once(context: BrowserContext, state: dict, cold_start: bool) -> None:
    seen: dict[str, dict] = {}
    for item in PRODUCTS:
        pid = product_id(item["url"])
        if not pid:
            log.warning("Skipping URL without a product id: %s", item["url"])
            continue
        if pid not in seen:
            seen[pid] = {"label": item["label"], "url": canonical_url(item["url"])}

    log.info("Checking %d product(s) for pincode %s.", len(seen), PINCODE)

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def worker(pid: str, item: dict) -> tuple[str, dict, CheckResult]:
        async with semaphore:
            result = await check_product(context, item["url"], item["label"])
            log.info(
                "%s -> %s (%s)",
                result.title or item["label"],
                result.availability.upper(),
                result.reason,
            )
            return pid, item, result

    results = await asyncio.gather(*(worker(pid, item) for pid, item in seen.items()))

    budget = [MAX_NOTIFICATIONS_PER_RUN]
    pending: list[tuple[dict, str]] = []
    counts = {"available": 0, "unavailable": 0, "unknown": 0}

    for pid, item, result in results:
        counts[result.availability] += 1
        entry, message = apply_result(
            state["items"], pid, item["label"], item["url"], result, cold_start, budget
        )
        if message:
            pending.append((entry, message))

    sent = 0
    for entry, message in pending:
        if not send_telegram(message):
            # Keep `notified` false so the alert is retried on the next run.
            break
        entry["notified"] = True
        entry["last_notified"] = now_iso()
        sent += 1

    if cold_start and counts["available"]:
        send_telegram(
            f"FirstCry monitor started tracking {len(seen)} product(s) for pincode {PINCODE}. "
            f"{counts['available']} are already in stock and deliverable; "
            "you will be alerted when anything new becomes available."
        )

    log.info(
        "Run summary: %d available, %d unavailable, %d undetermined, %d alert(s) sent.",
        counts["available"],
        counts["unavailable"],
        counts["unknown"],
        sent,
    )


async def main() -> None:
    state, cold_start = load_state()
    if cold_start:
        log.info("No previous state found; this run establishes the baseline.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=USER_AGENT,
        )
        context.set_default_timeout(20000)
        await block_heavy_resources(context)
        await context.add_cookies(
            [{"name": "globalPincode", "value": PINCODE, "domain": ".firstcry.com", "path": "/"}]
        )

        try:
            while True:
                await run_once(context, state, cold_start)
                save_state(state)
                cold_start = False
                if RUN_ONCE:
                    break
                await asyncio.sleep(LOOP_INTERVAL_S)
        finally:
            save_state(state)
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
