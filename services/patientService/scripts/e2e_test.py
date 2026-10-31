"""
End-to-end test against a running Patient Service (real PostgreSQL).

Walks one new patient through BR11 and BR12 and checks every response:
register -> duplicate phone -> 3 no-shows (blocked) -> replay -> unblock.

Usage (service already running, e.g. `python main.py`):
    python scripts/e2e_test.py [base_url]        default http://127.0.0.1:8002

Each run creates one patient with a fresh phone number and booking ids, so it
can be repeated. Exit code is 0 only if every check passes.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002").rstrip("/")
failures = 0


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read() or "null")
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), json.loads(err.read() or "null")


def check(title, method, path, body=None, status=200, expect=None, header=None):
    """Call the API and compare the status code, body fields and an optional header."""
    global failures
    code, headers, data = call(method, path, body)
    problems = []
    if code != status:
        problems.append(f"status {code}, expected {status}")
    for key, value in (expect or {}).items():
        actual = data.get("error", {}).get("code") if key == "error" else data.get(key)
        if actual != value:
            problems.append(f"{key}={actual!r}, expected {value!r}")
    if header and {k.lower(): v for k, v in headers.items()}.get(header[0].lower()) != header[1]:
        problems.append(f"header {header[0]} missing")

    failures += bool(problems)
    print(f"[{'PASS' if not problems else 'FAIL'}] {title}")
    print(f"       {method} {path} -> {code} {json.dumps(data)}")
    for p in problems:
        print(f"       !! {p}")
    return data


print(f"Patient Service E2E test against {BASE}")
print(f"Started : {datetime.now():%Y-%m-%d %H:%M:%S}\n")

stamp = int(time.time())
phone = f"+91{stamp % 10**10:010d}"
# booking_id is a PostgreSQL INTEGER, so keep it below 2,147,483,647.
bookings = [(stamp % 10**8) * 10 + n for n in (1, 2, 3)]

check("Health: service and database up", "GET", "/health",
      expect={"status": "healthy", "database": "up"})

patient = check("Register patient", "POST", "/api/v1/patients",
                {"full_name": "E2E Test Patient", "age": 34, "phone": phone},
                status=201, expect={"status": "ACTIVE", "no_show_count": 0})
pid = patient.get("id")
if pid is None:
    sys.exit("Cannot continue without a patient id.")

check("BR11: same phone number is rejected", "POST", "/api/v1/patients",
      {"full_name": "Someone Else", "age": 50, "phone": phone},
      status=409, expect={"error": "PATIENT_PHONE_EXISTS"})
check("Validation: age above 120 is rejected", "POST", "/api/v1/patients",
      {"full_name": "Bad Age", "age": 130, "phone": "9123456789"},
      status=422, expect={"error": "VALIDATION_ERROR"})
check("Get patient by id", "GET", f"/api/v1/patients/{pid}", expect={"id": pid})
check("BR12: new patient is eligible", "GET", f"/api/v1/patients/{pid}/eligibility",
      expect={"eligible": True})

for n, booking_id in enumerate(bookings, start=1):
    check(f"BR12: no-show {n} recorded", "POST", f"/api/v1/patients/{pid}/no-shows",
          {"booking_id": booking_id}, status=201,
          expect={"no_show_count": n, "status": "BLOCKED" if n == 3 else "ACTIVE"})

check("BR12: same no-show again counts once", "POST", f"/api/v1/patients/{pid}/no-shows",
      {"booking_id": bookings[-1]}, expect={"no_show_count": 3},
      header=("Idempotent-Replayed", "true"))
check("BR12: blocked patient is not eligible", "GET", f"/api/v1/patients/{pid}/eligibility",
      expect={"eligible": False, "reason": "PATIENT_BLOCKED"})
check("Unblock: patient ACTIVE, counter reset", "POST", f"/api/v1/patients/{pid}/unblock",
      expect={"status": "ACTIVE", "no_show_count": 0})
check("Unblock an ACTIVE patient is rejected", "POST", f"/api/v1/patients/{pid}/unblock",
      status=409, expect={"error": "INVALID_STATE_TRANSITION"})
check("BR12: unblocked patient is eligible again", "GET", f"/api/v1/patients/{pid}/eligibility",
      expect={"eligible": True})
check("Unknown patient is 404", "GET", "/api/v1/patients/999999999",
      status=404, expect={"error": "PATIENT_NOT_FOUND"})

print(f"\nFinished: {datetime.now():%Y-%m-%d %H:%M:%S}")
print("RESULT  : ALL CHECKS PASSED" if failures == 0 else f"RESULT  : {failures} CHECK(S) FAILED")
sys.exit(1 if failures else 0)
