"""Buyer-side logic tests: OTP relay, cooldown, ordered suppression."""

from __future__ import annotations

import firstcry_buyer as b
import firstcry_monitor as m


class FakeResp:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.ok = ok
        self.status_code = 200 if ok else 500
        self.text = str(payload)

    def json(self):
        return self._payload


def setup_telegram(monkeypatch):
    monkeypatch.setattr(b, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(b, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(m, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(m, "TELEGRAM_CHAT_ID", "42")


def make_transport(get_queue):
    """get_queue: list of getUpdates payloads; first call is the watermark probe."""
    calls = {"get": 0, "post": 0}

    def fake_get(url, params=None, timeout=None):
        calls["get"] += 1
        if calls["get"] == 1:
            return FakeResp({"ok": True, "result": [{"update_id": 100}]})
        if get_queue:
            return FakeResp(get_queue.pop(0))
        return FakeResp({"ok": True, "result": []})

    def fake_post(url, data=None, timeout=None):
        calls["post"] += 1
        return FakeResp({"ok": True, "result": {}})

    return fake_get, fake_post, calls


def install(monkeypatch, fake_get, fake_post):
    monkeypatch.setattr(b.requests, "get", fake_get)
    monkeypatch.setattr(b.requests, "post", fake_post)
    monkeypatch.setattr(m.requests, "post", fake_post)


def test_request_otp_accepts_numeric_reply_from_our_chat(monkeypatch):
    setup_telegram(monkeypatch)
    get_queue = [
        {"ok": True, "result": [
            {"update_id": 101, "message": {"chat": {"id": 42}, "text": " 483920 "}},
        ]},
    ]
    fake_get, fake_post, calls = make_transport(get_queue)
    install(monkeypatch, fake_get, fake_post)

    assert b.request_otp("login", timeout_s=5) == "483920"
    assert calls["post"] == 1  # the "reply with OTP" prompt


def test_request_otp_ignores_wrong_chat_and_nonnumeric(monkeypatch):
    setup_telegram(monkeypatch)
    get_queue = [
        {"ok": True, "result": [
            {"update_id": 101, "message": {"chat": {"id": 999}, "text": "123456"}},
            {"update_id": 102, "message": {"chat": {"id": 42}, "text": "hello"}},
        ]},
        {"ok": True, "result": [
            {"update_id": 103, "message": {"chat": {"id": 42}, "text": "778811"}},
        ]},
    ]
    fake_get, fake_post, _ = make_transport(get_queue)
    install(monkeypatch, fake_get, fake_post)

    assert b.request_otp("order verification", timeout_s=5) == "778811"


def test_request_otp_polls_only_after_watermark(monkeypatch):
    setup_telegram(monkeypatch)
    seen_offsets = []

    def fake_get(url, params=None, timeout=None):
        seen_offsets.append((params or {}).get("offset"))
        if len(seen_offsets) == 1:
            return FakeResp({"ok": True, "result": [{"update_id": 100}]})
        return FakeResp({"ok": True, "result": [
            {"update_id": 101, "message": {"chat": {"id": 42}, "text": "246810"}},
        ]})

    fake_post = lambda *a, **k: FakeResp({"ok": True, "result": {}})
    install(monkeypatch, fake_get, fake_post)

    assert b.request_otp("login", timeout_s=5) == "246810"
    assert seen_offsets[0] == -1            # watermark probe
    assert all(o == 101 for o in seen_offsets[1:])  # only newer updates


def test_request_otp_times_out(monkeypatch):
    setup_telegram(monkeypatch)
    fake_get, fake_post, _ = make_transport([])
    install(monkeypatch, fake_get, fake_post)

    assert b.request_otp("login", timeout_s=1) is None


def test_request_otp_without_telegram_configured(monkeypatch):
    monkeypatch.setattr(b, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(b, "TELEGRAM_CHAT_ID", "")
    assert b.request_otp("login", timeout_s=1) is None


def test_buy_cooldown_roundtrip():
    entry = {}
    assert b.in_buy_cooldown(entry) is False
    b.set_buy_cooldown(entry)
    assert b.in_buy_cooldown(entry) is True
    entry["buy_retry_after"] = "2000-01-01T00:00:00+00:00"
    assert b.in_buy_cooldown(entry) is False


URLX = "https://www.firstcry.com/hot-wheels/x/23930405/product-detail"


def test_ordered_product_keeps_normal_alert_rules():
    items = {}
    result = m.CheckResult("available", "test")
    entry, _ = m.apply_result(items, "23930405", "Car", URLX, result, False, [10])
    entry["ordered"] = True
    entry["notified"] = True
    # availability feature is unchanged by purchases: restock still alerts
    _, msg = m.apply_result(items, "23930405", "Car", URLX, m.CheckResult("unavailable", "t"), False, [10])
    assert msg is None
    _, msg = m.apply_result(items, "23930405", "Car", URLX, m.CheckResult("available", "t"), False, [10])
    assert msg is not None
