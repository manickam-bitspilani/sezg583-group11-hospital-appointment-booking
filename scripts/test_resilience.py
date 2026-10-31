"""
Resilience of the notification step: timeout, retry with exponential backoff
and jitter, and graceful failure. Retries must never send a second SMS.

Run with all services up:   pytest -s scripts/test_resilience.py

The "service down" scenario needs Notification Service stopped first:
    (stop notification-service)   NOTIFICATION_DOWN=1 pytest -s scripts/test_resilience.py -k down
"""

import os

import pytest

from conftest import (
    book,
    create_patient,
    create_slot,
    get_slot,
    notifications_for,
    set_notification_simulation,
)

NOTIFICATION_DOWN = os.getenv("NOTIFICATION_DOWN") == "1"
up_only = pytest.mark.skipif(NOTIFICATION_DOWN, reason="Notification Service is stopped")


@pytest.fixture
def provider():
    yield set_notification_simulation
    if not NOTIFICATION_DOWN:
        set_notification_simulation(failure_rate=0.0, latency_ms=0)


# Booking makes this many attempts per notification (its NOTIFICATION_MAX_ATTEMPTS).
MAX_ATTEMPTS = int(os.getenv("NOTIFICATION_MAX_ATTEMPTS", "4"))

# (provider failures before the booking, expected notification_status, expected attempts)
# fail_next makes the provider fail an exact number of times, so every run
# prints the same table.
SCENARIOS = [
    (0, "SENT", 1),                                # healthy provider
    (2, "SENT", 3),                                # 2 failures, retried, sent on attempt 3
    (MAX_ATTEMPTS - 1, "SENT", MAX_ATTEMPTS),      # sent on the very last attempt
    (MAX_ATTEMPTS, "FAILED", MAX_ATTEMPTS),        # every attempt fails: graceful failure
]


@up_only
def test_failing_provider_is_retried_without_duplicate_sms(provider):
    slot = create_slot(capacity=len(SCENARIOS))

    results = []
    for fail_next, expected_status, expected_attempts in SCENARIOS:
        provider(fail_next=fail_next)
        response = book(create_patient()["id"], slot["id"])
        assert response.status_code == 201, response.text
        booking = response.json()
        results.append((fail_next, booking, notifications_for(booking["booking_id"])))

    print(f"\nProvider fails exactly fail_next times; Booking makes up to {MAX_ATTEMPTS} attempts")
    print(f"  {'fail_next':>9}  {'booking':>7}  {'status':<9} {'notification':<12} {'sms rows':>8}  {'attempts':>8}  last_error")
    for fail_next, booking, rows in results:
        row = rows[0] if rows else {}
        print(f"  {fail_next:>9}  {booking['booking_id']:>7}  {booking['status']:<9} {booking['notification_status']:<12} "
              f"{len(rows):>8}  {row.get('attempts', '-'):>8}  {row.get('last_error') or '-'}")
    print(f"  slot reserved_count: {get_slot(slot['id'])['reserved_count']} (every booking CONFIRMED)")

    for (fail_next, expected_status, expected_attempts), (_, booking, rows) in zip(SCENARIOS, results):
        assert booking["status"] == "CONFIRMED"                   # BR5: never undone
        assert booking["notification_status"] == expected_status
        assert len(rows) == 1                                     # never a second SMS (BR6)
        assert rows[0]["status"] == expected_status
        assert rows[0]["attempts"] == expected_attempts
        if expected_status == "FAILED":
            assert "fail_next" in rows[0]["last_error"]
        else:
            assert rows[0]["last_error"] is None
    assert get_slot(slot["id"])["reserved_count"] == len(SCENARIOS)


@up_only
def test_slow_provider_times_out_and_retry_returns_original_result(provider):
    # Booking gives up on each attempt after NOTIFICATION_TIMEOUT_SECONDS (2s);
    # the provider takes 2.5s. The first attempt times out on the caller's
    # side but still completes; the retry gets that result instead of a new SMS.
    provider(failure_rate=0.0, latency_ms=2500)

    response = book(create_patient()["id"], create_slot(capacity=1)["id"])
    booking = response.json()
    rows = notifications_for(booking["booking_id"])

    print(f"\nProvider latency 2500ms > caller timeout 2s")
    print(f"  booking: {response.status_code} {booking}")
    print(f"  sms rows: {[(r['status'], r['attempts']) for r in rows]}")

    assert response.status_code == 201
    assert booking["notification_status"] == "SENT"
    assert len(rows) == 1
    assert rows[0]["attempts"] == 1            # delivered once, retry was a replay


@pytest.mark.skipif(not NOTIFICATION_DOWN, reason="set NOTIFICATION_DOWN=1 after stopping Notification Service")
def test_notification_service_down_booking_still_confirmed():
    slot = create_slot(capacity=1)

    response = book(create_patient()["id"], slot["id"])
    booking = response.json()

    print(f"\nNotification Service stopped")
    print(f"  booking: {response.status_code} {booking}")
    print(f"  slot reserved_count: {get_slot(slot['id'])['reserved_count']}")

    assert response.status_code == 201
    assert booking["status"] == "CONFIRMED"
    assert booking["notification_status"] == "FAILED"
    assert get_slot(slot["id"])["reserved_count"] == 1
