"""Notification/state logic tests: python3 -m pytest test_monitor_logic.py"""

from __future__ import annotations

import firstcry_monitor as m

PID = "23930405"
URL = "https://www.firstcry.com/hot-wheels/some-car/23930405/product-detail"


def apply(items, availability, cold_start=False, budget=None, eta="", url=URL, send=True):
    """Runs one check outcome through the state machine, emulating a successful send."""
    result = m.CheckResult(availability, "test", eta)
    entry, message = m.apply_result(
        items, PID, "Some Car", url, result, cold_start, budget or [10]
    )
    if message and send:
        entry["notified"] = True
    return entry, message


def test_notifies_once_while_continuously_available():
    items = {}
    _, first = apply(items, "available")
    _, second = apply(items, "available")
    _, third = apply(items, "available")
    assert first and "Some Car" in first
    assert second is None and third is None


def test_reset_on_unavailable_then_notify_again():
    items = {}
    apply(items, "available")
    apply(items, "unavailable")
    assert items[PID]["notified"] is False
    _, message = apply(items, "available")
    assert message is not None


def test_unknown_never_resets_or_notifies():
    items = {}
    apply(items, "available")
    _, message = apply(items, "unknown")
    assert message is None
    assert items[PID]["notified"] is True
    _, message = apply(items, "available")
    assert message is None


def test_unknown_on_fresh_product_does_not_notify():
    items = {}
    _, message = apply(items, "unknown")
    assert message is None
    assert items[PID]["notified"] is False


def test_cold_start_records_without_alerting():
    items = {}
    _, message = apply(items, "available", cold_start=True)
    assert message is None
    assert items[PID]["notified"] is True


def test_failed_send_is_retried_next_run():
    items = {}
    _, message = apply(items, "available", send=False)
    assert message is not None
    assert items[PID]["notified"] is False
    _, message = apply(items, "available")
    assert message is not None


def test_budget_defers_alert_to_next_run():
    items = {}
    _, message = apply(items, "available", budget=[0])
    assert message is None
    assert items[PID]["notified"] is False
    _, message = apply(items, "available", budget=[1])
    assert message is not None


def test_state_key_is_product_id_so_url_variants_do_not_duplicate():
    items = {}
    apply(items, "available")
    _, message = apply(items, "available", url=URL + "?ref2=listing")
    assert message is None
    assert list(items) == [PID]


def test_legacy_state_migration(tmp_path, monkeypatch):
    legacy = tmp_path / "firstcry_state.json"
    legacy.write_text(
        '{"%s": {"status": "in_stock", "deliverable": true},'
        ' "https://www.firstcry.com/x/1/product-detail": "out_of_stock"}' % URL
    )
    monkeypatch.setattr(m, "STATE_FILE", str(legacy))
    state, cold_start = m.load_state()
    assert cold_start is False
    assert state["items"][PID]["notified"] is True
    assert state["items"]["1"]["notified"] is False


def test_delivery_payload_parsing():
    deliverable, eta = m.parse_delivery_payload(
        {"Result": {"IsServicable": 166, "ShippingDate": "Get it by <b>Tomorrow 8 PM </b>"}}
    )
    assert deliverable is True
    assert eta == "Get it by Tomorrow 8 PM"

    assert m.parse_delivery_payload({"Result": {"IsServicable": 0}}) == (False, "")
    assert m.parse_delivery_payload({"Result": {}}) == (None, "")
