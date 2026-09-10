"""Buy-side automation for the FirstCry monitor.

When a watched product is in stock and deliverable, this module logs in with the
configured mobile number (OTP relayed through Telegram), empties the cart, adds
the single product, picks the saved address, pays (COD preferred), and places
the order. OTP screens at any step are answered by asking on Telegram and
waiting for a reply.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

log = logging.getLogger("firstcry-buyer")

SESSION_FILE = os.environ.get("SESSION_FILE", "firstcry_session.json")
FIRSTCRY_PHONE = os.environ.get("FIRSTCRY_PHONE", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# How long to wait for the user to reply with an OTP on Telegram.
OTP_WAIT_S = int(os.environ.get("OTP_WAIT_S", "300"))
OTP_RE = re.compile(r"^\s*(\d{4,8})\s*$")
MAX_OTP_ROUNDS = 4
BUY_COOLDOWN_MINUTES = int(os.environ.get("BUY_COOLDOWN_MINUTES", "30"))

CHECKOUT_URL = "https://checkout.firstcry.com/checkout"
LOGIN_URL = "https://www.firstcry.com/m/login"
CART_URL = "https://www.firstcry.com/cart"
HOME_URL = "https://www.firstcry.com/"
REQUEST_TIMEOUT = 20


class BuyError(Exception):
    pass


def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def notify(text: str) -> bool:
    """Send a Telegram message without product-identifying content."""
    from firstcry_monitor import send_telegram

    return send_telegram(text)


def _latest_update_id() -> int:
    try:
        r = requests.get(
            _tg_api("getUpdates"),
            params={"offset": -1, "limit": 1, "timeout": 0},
            timeout=REQUEST_TIMEOUT,
        )
        data = r.json()
        if data.get("ok") and data.get("result"):
            return int(data["result"][-1]["update_id"])
    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        log.warning("Could not get Telegram update watermark: %s", e)
    return 0


def request_otp(reason: str, timeout_s: int = OTP_WAIT_S) -> str | None:
    """Ask for an OTP on Telegram and poll getUpdates for a numeric reply."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram is not configured; cannot request OTP.")
        return None

    watermark = _latest_update_id()
    notify(
        "FirstCry is asking for an OTP"
        + (f" ({reason})" if reason else "")
        + ". Reply to this message with the code."
    )

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = requests.get(
                _tg_api("getUpdates"),
                params={
                    "offset": watermark + 1,
                    "timeout": 25,
                    "allowed_updates": json.dumps(["message"]),
                },
                timeout=40,
            )
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("Telegram getUpdates failed: %s", e)
            time.sleep(3)
            continue

        if not data.get("ok"):
            log.warning("Telegram getUpdates not ok: %s", data)
            time.sleep(3)
            continue

        for update in data.get("result", []):
            watermark = max(watermark, int(update.get("update_id", 0)))
            msg = update.get("message") or update.get("edited_message") or {}
            if str((msg.get("chat") or {}).get("id")) != str(TELEGRAM_CHAT_ID):
                continue
            match = OTP_RE.match(msg.get("text") or "")
            if match:
                log.info("Received OTP via Telegram.")
                return match.group(1)
    log.warning("Timed out waiting for OTP (%s).", reason or "no reason")
    return None


# --- session ---------------------------------------------------------------


def session_available() -> bool:
    return os.path.exists(SESSION_FILE) and os.path.getsize(SESSION_FILE) > 50


async def save_session(context: BrowserContext, logged_in: bool = False) -> None:
    try:
        await context.storage_state(path=SESSION_FILE)
        log.info(
            "Saved %s to %s.",
            "login session" if logged_in else "anonymous browser state",
            SESSION_FILE,
        )
    except Exception as e:
        log.warning("Could not save session: %s", e)


async def is_logged_in(page: Page) -> bool:
    try:
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(3000)
        login = page.locator(".poplogin_main, .poplogin").first
        return await login.count() == 0 or not await login.is_visible()
    except Exception as e:
        log.warning("Login check failed: %s", e)
        return False


OTP_INPUT_JS = """() => [...document.querySelectorAll('input')]
    .filter(e => e.offsetParent !== null && !e.disabled)
    .filter(e => /otp|verification|passcode|one.?time/i.test(
        (e.id + ' ' + e.name + ' ' + e.className + ' ' + (e.placeholder || '')))
        || (e.type === 'tel' && e.maxLength > 0 && e.maxLength <= 8))
    .length"""


async def _otp_input_count(page: Page) -> int:
    try:
        return await page.evaluate(OTP_INPUT_JS)
    except Exception:
        return 0


async def handle_otp_step(page: Page, reason: str) -> bool:
    """If an OTP form is showing, fetch a code via Telegram and submit it."""
    count = await _otp_input_count(page)
    if count == 0:
        return False

    code = await asyncio.to_thread(request_otp, reason)
    if not code:
        return False

    inputs = page.locator("input:visible")
    n = await inputs.count()
    filled = 0
    if count > 1 and len(code) >= count:
        boxes = page.locator("input[type='tel']:visible, input.otp-input:visible")
        nb = await boxes.count()
        for i in range(min(nb, len(code))):
            try:
                await boxes.nth(i).fill(code[i])
                filled += 1
            except Exception:
                pass
    if filled == 0:
        # Single-field OTP (or distribution failed): fill the first matching input.
        for sel in [
            "input[id*='otp' i]:visible",
            "input[name*='otp' i]:visible",
            "input[class*='otp' i]:visible",
            "input[type='tel']:visible",
        ]:
            loc = page.locator(sel)
            if await loc.count():
                try:
                    await loc.first.fill(code)
                    filled = 1
                    break
                except Exception:
                    continue

    if not filled:
        log.warning("OTP inputs seen but none could be filled.")
        return False

    for sel in [
        "text=/^\\s*(submit|verify|validate|confirm|continue)\\s*$/i",
        "input[type='submit']:visible",
    ]:
        loc = page.locator(sel)
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click()
                await page.wait_for_timeout(3000)
                return True
        except Exception:
            continue
    try:
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3000)
    except Exception:
        pass
    return True


async def login_with_otp(page: Page) -> bool:
    """Mobile-number login; the OTP is requested from the user via Telegram."""
    if not FIRSTCRY_PHONE:
        log.error("FIRSTCRY_PHONE is not set; cannot log in.")
        notify(
            "FirstCry purchase needs the FIRSTCRY_PHONE secret "
            "(repo Settings -> Secrets and variables -> Actions)."
        )
        return False

    log.info("Logging in with mobile number ending %s.", FIRSTCRY_PHONE[-4:])
    try:
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(3000)
        mobile = page.locator("#lemail")
        await mobile.fill(FIRSTCRY_PHONE)
        await page.locator("text=/^\\s*continue\\s*$/i").first.click()
        await page.wait_for_timeout(4000)

        for _ in range(MAX_OTP_ROUNDS):
            if await _otp_input_count(page):
                if not await handle_otp_step(page, "login"):
                    return False
                await page.wait_for_timeout(4000)
                continue
            break

        logged_in = await is_logged_in(page)
        if logged_in:
            await save_session(page.context, logged_in=True)
        else:
            log.warning("Login flow finished but session does not look logged in.")
        return logged_in
    except Exception as e:
        log.error("Login failed: %s", e)
        return False


async def ensure_logged_in(context: BrowserContext) -> bool:
    page = await context.new_page()
    try:
        if await is_logged_in(page):
            return True
        log.info("Session missing or expired; starting OTP login.")
        notify("FirstCry login needed. A login OTP is on its way to your phone.")
        return await login_with_otp(page)
    finally:
        await page.close()


# --- cart + checkout ---------------------------------------------------------


async def empty_cart(page: Page) -> bool:
    """Remove every line item so only the target product is ordered."""
    for _ in range(15):
        try:
            await page.goto(CART_URL, wait_until="domcontentloaded", timeout=40000)
            await page.wait_for_timeout(4000)
            clicked = False
            for sel in [
                "a[class*='remove' i]",
                "span[class*='remove' i]",
                "div[class*='remove' i]",
                "text=/^\\s*(remove|delete)\\s*$/i",
            ]:
                loc = page.locator(sel)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.click()
                    clicked = True
                    await page.wait_for_timeout(2500)
                    break
            if not clicked:
                return True
        except PlaywrightTimeoutError:
            return False
        except Exception as e:
            log.warning("Cart clear pass failed: %s", e)
            return False
    return False


async def _select_saved_address(page: Page) -> bool:
    # With a single saved address the first matching control selects it.
    for sel in [
        "text=/deliver (to|here|this|at)/i",
        "div[class*='address' i] input[type='radio']",
        "input[type='radio']:visible",
    ]:
        loc = page.locator(sel)
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click()
                await page.wait_for_timeout(3000)
                break
        except Exception:
            continue
    # After selecting, continue/proceed to the payment step.
    for sel in [
        "text=/^\\s*(continue|proceed to payment|proceed|next)\\s*$/i",
        "input[type='submit']:visible",
    ]:
        loc = page.locator(sel)
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click()
                await page.wait_for_timeout(4000)
                return True
        except Exception:
            continue
    # Single saved address may already be selected with no extra step.
    return await page.locator("text=/payment|pay now|place/i").count() > 0


async def _pay(page: Page) -> None:
    """Trigger the order with the account's default (saved card) payment method.

    Payment method selection is deliberately left untouched: the card that is
    already default stays selected and the bank/card OTP is relayed via Telegram.
    """
    for sel in [
        "#BtnCardPayNow",
        "#cdPaynow",
        "text=/^\\s*(pay now|place order|order now)/i",
    ]:
        loc = page.locator(sel)
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click()
                return
        except Exception:
            continue
    raise BuyError("No payment button found on the checkout page.")


ORDER_ID_RE = re.compile(
    r"order\s*(id|number|no\.?)\s*[:#]?\s*([A-Z0-9-]{5,})", re.I
)
CONFIRM_RE = re.compile(
    r"thank you|order (has been )?(placed|confirmed|successful)|order id|order number",
    re.I,
)


async def _wait_for_confirmation(page: Page, timeout_s: int = 90) -> str | None:
    """Wait for the order-confirmation screen; return the order id if seen."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        try:
            url = page.url
            if re.search(r"thank|success|confirm|orderstatus|ordersummary", url, re.I):
                pass  # likely confirmed; still read the body below
            text = await page.evaluate("() => document.body.innerText.slice(0, 6000)")
        except Exception:
            text = ""
        match = ORDER_ID_RE.search(text or "")
        if match:
            return match.group(2)
        if text and CONFIRM_RE.search(text):
            return ""
        await asyncio.sleep(3)
    return None


async def buy_product(context: BrowserContext, url: str, pid: str) -> tuple[bool, str]:
    """Purchase a single product as its own order. Returns (ok, order_id_or_reason)."""
    page = await context.new_page()
    try:
        if not os.environ.get("SESSION_SECRET") and not session_available():
            notify(
                "SESSION_SECRET is not set, so the login session cannot persist "
                "between runs - the bot would need a fresh OTP every run."
            )
        # The account cart only exists after login, so authenticate before
        # touching it; the OTP is relayed via Telegram.
        if not await is_logged_in(page):
            log.info("Not logged in; starting OTP login before purchase.")
            if not await login_with_otp(page):
                raise BuyError("login failed")

        if not await empty_cart(page):
            raise BuyError("could not empty the cart")

        await page.goto(url, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(4000)
        atc = page.locator(".acartGcartBtn .add_to_cart").first
        if not await atc.count() or not await atc.is_visible():
            raise BuyError("add-to-cart button not found")
        await atc.click()
        await page.wait_for_timeout(4000)

        await page.goto(CHECKOUT_URL, wait_until="domcontentloaded", timeout=40000)
        await page.wait_for_timeout(5000)

        address_done = paid = False
        for _ in range(10):
            # A login wall can appear anywhere in checkout.
            if await page.locator("#lemail:visible").count():
                if not await login_with_otp(page):
                    raise BuyError("login failed during checkout")
                continue
            if await _otp_input_count(page):
                if not await handle_otp_step(page, "order verification"):
                    raise BuyError("OTP step timed out")
                continue

            text = (await page.evaluate(
                "() => document.body.innerText.slice(0, 4000)"
            )) or ""
            if CONFIRM_RE.search(text):
                match = ORDER_ID_RE.search(text)
                return True, match.group(2) if match else "confirmed"

            if not address_done:
                if not await _select_saved_address(page):
                    raise BuyError("no saved address could be selected")
                address_done = True
                continue
            if not paid:
                await _pay(page)
                paid = True
                await page.wait_for_timeout(6000)
                continue
            await page.wait_for_timeout(4000)

        # Final confirmation wait (payment page may take a while).
        result = await _wait_for_confirmation(page)
        if result is None:
            raise BuyError("no order confirmation seen")
        return True, result
    except BuyError as e:
        return False, str(e)
    except Exception as e:
        return False, f"error: {e}"
    finally:
        try:
            await page.screenshot(path=f"buy_{pid}.png", full_page=False)
        except Exception:
            pass
        await page.close()


def in_buy_cooldown(entry: dict) -> bool:
    until = entry.get("buy_retry_after", "")
    if not until:
        return False
    try:
        return datetime.now(timezone.utc) < datetime.fromisoformat(until)
    except ValueError:
        return False


def set_buy_cooldown(entry: dict) -> None:
    entry["buy_retry_after"] = (
        datetime.now(timezone.utc) + timedelta(minutes=BUY_COOLDOWN_MINUTES)
    ).isoformat(timespec="seconds")
