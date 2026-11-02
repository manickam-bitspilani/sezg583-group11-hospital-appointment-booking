"""
End-to-end test against a running Slot Service (real PostgreSQL).

Creates fresh slots and walks them through BR1, BR2, BR3, BR7, BR8, BR9 and
BR10, checking every response:
create -> invalid slots -> overlap -> reserve -> replay -> reused key ->
duplicate patient -> slot full -> cancel -> cancel again -> rebook -> 404s.

Usage (service already running, e.g. `python main.py`):
    python scripts/run_e2e.py [base_url]        default http://127.0.0.1:8001

Each run uses a random doctor id, patient ids and keys, so it can be repeated.
Exit code is 0 only if every check passes.
"""

import json
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "http://127.0.0.1:8001"
failures = 0


def call(method, path, body=None, key=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
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


def slot_body(doctor_id, start, minutes=30, capacity=2):
    return {
        "doctor_id": doctor_id,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=minutes)).isoformat(),
        "capacity": capacity,
    }


def main():
    """Run every check once; the module only defines helpers, so importing it does nothing."""
    global BASE
    if len(sys.argv) > 1:
        BASE = sys.argv[1].rstrip("/")

    print(f"Slot Service E2E test against {BASE}")
    print(f"Started : {datetime.now():%Y-%m-%d %H:%M:%S}\n")

    run = random.randint(1_000, 99_999)
    doctor = random.randint(1_000, 1_000_000_000)
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=random.randint(1, 365))
    alice, bob, carol = run * 10 + 1, run * 10 + 2, run * 10 + 3

    check("Health: service and database up", "GET", "/health",
          expect={"status": "healthy", "database": "up"})

    slot = check("Create slot (capacity 2)", "POST", "/api/v1/slots", slot_body(doctor, start),
                 status=201, expect={"capacity": 2, "reserved_count": 0})
    sid = slot.get("id")
    if sid is None:
        sys.exit("Cannot continue without a slot id.")

    check("BR7: capacity 0 is rejected", "POST", "/api/v1/slots",
          slot_body(doctor, start + timedelta(days=1), capacity=0),
          status=422, expect={"error": "VALIDATION_ERROR"})
    check("BR7: end before start is rejected", "POST", "/api/v1/slots",
          slot_body(doctor, start + timedelta(days=1), minutes=-30),
          status=422, expect={"error": "VALIDATION_ERROR"})
    check("BR8: slot in the past is rejected", "POST", "/api/v1/slots",
          slot_body(doctor, datetime.now(timezone.utc) - timedelta(days=1)),
          status=422, expect={"error": "SLOT_IN_PAST"})
    check("BR9: overlapping slot for the same doctor is rejected", "POST", "/api/v1/slots",
          slot_body(doctor, start + timedelta(minutes=15)),
          status=409, expect={"error": "SLOT_OVERLAP"})
    check("BR9: back-to-back slot is allowed", "POST", "/api/v1/slots",
          slot_body(doctor, start + timedelta(minutes=30)), status=201)
    check("Validation: doctor_id beyond INTEGER range is a 422, not a 500", "POST", "/api/v1/slots",
          slot_body(2_147_483_648, start + timedelta(days=2)),
          status=422, expect={"error": "VALIDATION_ERROR"})

    first = check("Reserve a place", "POST", "/api/v1/reservations",
                  {"slot_id": sid, "patient_id": alice}, key=f"e2e-{run}-alice",
                  status=201, expect={"status": "CONFIRMED", "patient_id": alice})
    check("BR3: same key returns the original reservation", "POST", "/api/v1/reservations",
          {"slot_id": sid, "patient_id": alice}, key=f"e2e-{run}-alice",
          status=201, expect={"id": first.get("id")})
    check("BR3: same key with a different payload is rejected", "POST", "/api/v1/reservations",
          {"slot_id": sid, "patient_id": bob}, key=f"e2e-{run}-alice",
          status=422, expect={"error": "IDEMPOTENCY_KEY_REUSED"})
    check("BR2: same patient, new key, same slot is rejected", "POST", "/api/v1/reservations",
          {"slot_id": sid, "patient_id": alice}, key=f"e2e-{run}-alice-2",
          status=409, expect={"error": "DUPLICATE_BOOKING"})
    check("Replay and duplicate did not use capacity", "GET", f"/api/v1/slots/{sid}",
          expect={"reserved_count": 1})

    check("Reserve the last place", "POST", "/api/v1/reservations",
          {"slot_id": sid, "patient_id": bob}, key=f"e2e-{run}-bob", status=201)
    check("BR1: a full slot rejects the next patient", "POST", "/api/v1/reservations",
          {"slot_id": sid, "patient_id": carol}, key=f"e2e-{run}-carol",
          status=409, expect={"error": "SLOT_FULL"})
    check("BR1: reserved_count equals capacity", "GET", f"/api/v1/slots/{sid}",
          expect={"reserved_count": 2, "capacity": 2})

    rid = first.get("id")
    check("BR10: cancel frees the place", "POST", f"/api/v1/reservations/{rid}/cancel",
          expect={"status": "CANCELLED"})
    check("BR10: cancelling again changes nothing", "POST", f"/api/v1/reservations/{rid}/cancel",
          expect={"status": "CANCELLED"}, header=("Idempotent-Replayed", "true"))
    check("BR10: the place is freed exactly once", "GET", f"/api/v1/slots/{sid}",
          expect={"reserved_count": 1})
    check("BR10: a cancelled patient can book the slot again", "POST", "/api/v1/reservations",
          {"slot_id": sid, "patient_id": alice}, key=f"e2e-{run}-alice-3",
          status=201, expect={"status": "CONFIRMED"})

    check("Unknown slot is 404", "POST", "/api/v1/reservations",
          {"slot_id": 2_147_483_000, "patient_id": carol}, key=f"e2e-{run}-unknown",
          status=404, expect={"error": "SLOT_NOT_FOUND"})
    check("Unknown reservation is 404", "GET", "/api/v1/reservations/2147483000",
          status=404, expect={"error": "RESERVATION_NOT_FOUND"})
    check("Validation: slot_id beyond INTEGER range is a 422, not a 500", "POST", "/api/v1/reservations",
          {"slot_id": 2_147_483_648, "patient_id": carol}, key=f"e2e-{run}-big",
          status=422, expect={"error": "VALIDATION_ERROR"})

    print(f"\nFinished: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("RESULT  : ALL CHECKS PASSED" if failures == 0 else f"RESULT  : {failures} CHECK(S) FAILED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
