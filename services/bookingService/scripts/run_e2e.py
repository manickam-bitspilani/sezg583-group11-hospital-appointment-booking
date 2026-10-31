"""
End-to-end test against a running Booking Service and the three services it
calls (Patient, Slot, Notification; all on real PostgreSQL).

Walks fresh bookings through BR3, BR4, BR5, BR10 and BR12 and checks every
response:
book -> replay -> reused key -> slot full -> unknown patient/slot -> blocked
patient -> notification retry -> notification failure -> cancel -> validation.

Usage (all four services already running, e.g. `python main.py` in each):
    python scripts/run_e2e.py [base_url]        default http://127.0.0.1:8003

The other services are found through the same variables Booking Service uses
(PATIENT_SERVICE_URL, SLOT_SERVICE_URL, NOTIFICATION_SERVICE_URL).

Each run creates its own patients and slots with fresh ids and keys, so it can
be repeated. It changes the notification provider simulation and resets it at
the end. Exit code is 0 only if every check passes.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "http://127.0.0.1:8003"
PATIENT = os.getenv("PATIENT_SERVICE_URL", "http://127.0.0.1:8002")
SLOT = os.getenv("SLOT_SERVICE_URL", "http://127.0.0.1:8001")
NOTIFICATION = os.getenv("NOTIFICATION_SERVICE_URL", "http://127.0.0.1:8004")
failures = 0


def call(method, path, body=None, key=None):
    """path is relative to Booking Service, or a full URL for the other services."""
    url = path if path.startswith("http") else BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read() or "null")
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), json.loads(err.read() or "null")


def check(title, method, path, body=None, key=None, status=200, expect=None, header=None):
    """Call the API and compare the status code, body fields and an optional header."""
    global failures
    code, headers, data = call(method, path, body, key)
    problems = []
    if code != status:
        problems.append(f"status {code}, expected {status}")
    for name, value in (expect or {}).items():
        actual = data.get("error", {}).get("code") if name == "error" else data.get(name)
        if actual != value:
            problems.append(f"{name}={actual!r}, expected {value!r}")
    if header and {k.lower(): v for k, v in headers.items()}.get(header[0].lower()) != header[1]:
        problems.append(f"header {header[0]} missing")

    failures += bool(problems)
    print(f"[{'PASS' if not problems else 'FAIL'}] {title}")
    print(f"       {method} {path} -> {code} {json.dumps(data)}")
    for p in problems:
        print(f"       !! {p}")
    return data


def failures_add(problem):
    global failures
    failures += 1
    print(f"       !! {problem}")


def simulation(fail_next):
    return {"failure_rate": 0.0, "latency_ms": 0, "fail_next": fail_next}


def main():
    """Run every check once; the module only defines helpers, so importing it does nothing."""
    global BASE
    if len(sys.argv) > 1:
        BASE = sys.argv[1].rstrip("/")

    print(f"Booking Service E2E test against {BASE}")
    print(f"Started : {datetime.now():%Y-%m-%d %H:%M:%S}\n")

    stamp = int(time.time())
    # Ids are PostgreSQL INTEGERs, so keep them below 2,147,483,647.
    doctor_id = stamp % 10**8
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)

    def key(name):
        return f"e2e-{stamp}-{name}"

    def new_patient(n):
        patient = check(f"Setup: register patient {n}", "POST", f"{PATIENT}/api/v1/patients",
                        {"full_name": f"Booking E2E Patient {n}", "age": 40,
                         "phone": f"+91{(stamp * 10 + n) % 10**10:010d}"},
                        status=201, expect={"status": "ACTIVE"})
        return patient.get("id")

    def new_slot(hour, capacity):
        slot = check(f"Setup: create slot (capacity {capacity})", "POST", f"{SLOT}/api/v1/slots",
                     {"doctor_id": doctor_id, "capacity": capacity,
                      "start_time": (start + timedelta(hours=hour)).isoformat(),
                      "end_time": (start + timedelta(hours=hour, minutes=30)).isoformat()},
                     status=201, expect={"reserved_count": 0})
        return slot.get("id")

    for name, url in (("Booking", BASE), ("Patient", PATIENT), ("Slot", SLOT), ("Notification", NOTIFICATION)):
        check(f"Health: {name} Service and database up", "GET", f"{url}/health",
              expect={"status": "healthy", "database": "up"})
    check("Setup: healthy notification provider", "PUT", f"{NOTIFICATION}/api/v1/simulation",
          simulation(0), expect={"fail_next": 0})

    p1, p2, p3 = new_patient(1), new_patient(2), new_patient(3)
    small_slot, big_slot = new_slot(0, 1), new_slot(1, 5)
    if None in (p1, p2, p3, small_slot, big_slot):
        sys.exit("Cannot continue without patients and slots.")

    # ---- BR3: one Idempotency-Key = one booking
    booking = check("Book a slot", "POST", "/api/v1/bookings",
                    {"patient_id": p1, "slot_id": small_slot}, key=key("book"), status=201,
                    expect={"status": "CONFIRMED", "notification_status": "SENT"})
    booking_id = booking.get("booking_id")
    check("BR3: same key is replayed, not booked again", "POST", "/api/v1/bookings",
          {"patient_id": p1, "slot_id": small_slot}, key=key("book"), status=201,
          expect={"booking_id": booking_id, "reservation_id": booking.get("reservation_id")},
          header=("Idempotent-Replayed", "true"))
    check("BR3: same key with a different payload is rejected", "POST", "/api/v1/bookings",
          {"patient_id": p2, "slot_id": small_slot}, key=key("book"),
          status=422, expect={"error": "IDEMPOTENCY_KEY_REUSED"})
    check("BR3: slot reserved only once", "GET", f"{SLOT}/api/v1/slots/{small_slot}",
          expect={"reserved_count": 1})
    check("Booking is stored as CONFIRMED", "GET", f"/api/v1/bookings/{booking_id}",
          expect={"status": "CONFIRMED", "notification_status": "SENT"})

    # ---- Rejected bookings
    check("Full slot is rejected", "POST", "/api/v1/bookings",
          {"patient_id": p2, "slot_id": small_slot}, key=key("full"),
          status=409, expect={"error": "SLOT_FULL"})
    check("BR4: unknown patient is rejected", "POST", "/api/v1/bookings",
          {"patient_id": 2_000_000_000, "slot_id": big_slot}, key=key("no-patient"),
          status=404, expect={"error": "PATIENT_NOT_FOUND"})
    check("Unknown slot is rejected", "POST", "/api/v1/bookings",
          {"patient_id": p2, "slot_id": 2_000_000_000}, key=key("no-slot"),
          status=404, expect={"error": "SLOT_NOT_FOUND"})

    for n in (1, 2, 3):
        check(f"Setup: record no-show {n} for patient 3", "POST", f"{PATIENT}/api/v1/patients/{p3}/no-shows",
              {"booking_id": (stamp % 10**8) * 10 + n}, status=201)
    check("BR12: blocked patient cannot book", "POST", "/api/v1/bookings",
          {"patient_id": p3, "slot_id": big_slot}, key=key("blocked"),
          status=409, expect={"error": "PATIENT_BLOCKED"})
    check("BR12: blocked booking reserved nothing", "GET", f"{SLOT}/api/v1/slots/{big_slot}",
          expect={"reserved_count": 0})

    # ---- Notification: retry with backoff, and BR5
    check("Simulation: fail the next send", "PUT", f"{NOTIFICATION}/api/v1/simulation",
          simulation(1), expect={"fail_next": 1})
    retried = check("Notification is retried and sent", "POST", "/api/v1/bookings",
                    {"patient_id": p1, "slot_id": big_slot}, key=key("retry"), status=201,
                    expect={"status": "CONFIRMED", "notification_status": "SENT"})
    sms = check("Retry sent one SMS, on attempt 2", "GET",
                f"{NOTIFICATION}/api/v1/notifications?booking_id={retried.get('booking_id')}")
    if not (isinstance(sms, list) and len(sms) == 1 and sms[0].get("attempts") == 2):
        failures_add("expected 1 notification with attempts 2")

    check("Simulation: fail the next 4 sends (all retries)", "PUT", f"{NOTIFICATION}/api/v1/simulation",
          simulation(4), expect={"fail_next": 4})
    check("BR5: booking stays CONFIRMED when notification fails", "POST", "/api/v1/bookings",
          {"patient_id": p2, "slot_id": big_slot}, key=key("no-sms"), status=201,
          expect={"status": "CONFIRMED", "notification_status": "FAILED"})
    check("Simulation: healthy provider", "PUT", f"{NOTIFICATION}/api/v1/simulation",
          simulation(0), expect={"fail_next": 0})

    # ---- BR10: cancellation
    check("BR10: cancel booking", "POST", f"/api/v1/bookings/{booking_id}/cancel",
          expect={"status": "CANCELLED", "cancel_notification_status": "SENT"})
    check("BR10: cancel releases the slot place", "GET", f"{SLOT}/api/v1/slots/{small_slot}",
          expect={"reserved_count": 0})
    check("BR10: cancelling again changes nothing", "POST", f"/api/v1/bookings/{booking_id}/cancel",
          expect={"status": "CANCELLED"}, header=("Idempotent-Replayed", "true"))
    check("Cancel unknown booking is 404", "POST", "/api/v1/bookings/999999999/cancel",
          status=404, expect={"error": "BOOKING_NOT_FOUND"})

    # ---- Validation and lookups
    check("Validation: missing Idempotency-Key is rejected", "POST", "/api/v1/bookings",
          {"patient_id": p1, "slot_id": big_slot}, status=422, expect={"error": "VALIDATION_ERROR"})
    check("Validation: patient_id above INT4 range is rejected", "POST", "/api/v1/bookings",
          {"patient_id": 99999999999, "slot_id": big_slot}, key=key("big"),
          status=422, expect={"error": "VALIDATION_ERROR"})
    check("Unknown booking is 404", "GET", "/api/v1/bookings/999999999",
          status=404, expect={"error": "BOOKING_NOT_FOUND"})
    check("Booking id above INT4 range is 422", "GET", "/api/v1/bookings/99999999999",
          status=422, expect={"error": "VALIDATION_ERROR"})

    print(f"\nFinished: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("RESULT  : ALL CHECKS PASSED" if failures == 0 else f"RESULT  : {failures} CHECK(S) FAILED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
