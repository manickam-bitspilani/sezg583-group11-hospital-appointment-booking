"""
End-to-end test against a running Notification Service (real PostgreSQL).

Walks fresh notifications through BR6 and the mock provider's failure modes
and checks every response:
send -> replay -> reused key -> provider failure -> retry -> last_error -> validation.

Usage (service already running, e.g. `python main.py`):
    python scripts/run_e2e.py [base_url]        default http://127.0.0.1:8004

Each run uses fresh booking ids and Idempotency-Keys, so it can be repeated.
It changes the provider simulation and resets it at the end. Exit code is 0
only if every check passes.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE = "http://127.0.0.1:8004"
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


def check(title, method, path, body=None, key=None, status=200, expect=None, header=None, rows=None):
    """Call the API and compare the status code, body fields, an optional header
    and, for list responses, the number of rows."""
    global failures
    code, headers, data = call(method, path, body, key)
    problems = []
    if code != status:
        problems.append(f"status {code}, expected {status}")
    if rows is not None:
        if not isinstance(data, list) or len(data) != rows:
            problems.append(f"{len(data) if isinstance(data, list) else data!r} rows, expected {rows}")
    else:
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


def payload(booking_id, message="Appointment confirmed."):
    return {"patient_id": 1, "booking_id": booking_id, "message": message, "notification_type": "SMS"}


def main():
    """Run every check once; the module only defines helpers, so importing it does nothing."""
    global BASE, failures
    if len(sys.argv) > 1:
        BASE = sys.argv[1].rstrip("/")

    print(f"Notification Service E2E test against {BASE}")
    print(f"Started : {datetime.now():%Y-%m-%d %H:%M:%S}\n")

    stamp = int(time.time())
    # booking_id is a PostgreSQL INTEGER, so keep it below 2,147,483,647.
    sent_id, retry_id, failed_id = [(stamp % 10**8) * 10 + n for n in (1, 2, 3)]

    check("Health: service and database up", "GET", "/health",
          expect={"status": "healthy", "database": "up"})
    check("Simulation: healthy provider", "PUT", "/api/v1/simulation",
          {"failure_rate": 0.0, "latency_ms": 0, "fail_next": 0}, expect={"fail_next": 0})

    key = f"booking-{sent_id}-confirmation"
    sent = check("Send SMS", "POST", "/api/v1/notifications", payload(sent_id), key=key,
                 status=202, expect={"status": "SENT", "attempts": 1, "last_error": None})
    check("BR6: same key is replayed, not sent again", "POST", "/api/v1/notifications",
          payload(sent_id), key=key, expect={"id": sent.get("id"), "attempts": 1},
          header=("Idempotent-Replayed", "true"))
    check("BR6: same key with a different message is rejected", "POST", "/api/v1/notifications",
          payload(sent_id, "Something else"), key=key,
          status=422, expect={"error": "IDEMPOTENCY_KEY_REUSED"})
    check("BR6: one notification stored for the booking", "GET",
          f"/api/v1/notifications?booking_id={sent_id}", rows=1)

    key = f"booking-{retry_id}-confirmation"
    check("Simulation: fail the next send", "PUT", "/api/v1/simulation",
          {"failure_rate": 0.0, "latency_ms": 0, "fail_next": 1}, expect={"fail_next": 1})
    check("Provider failure is 503", "POST", "/api/v1/notifications", payload(retry_id), key=key,
          status=503, expect={"error": "PROVIDER_UNAVAILABLE"})
    check("Retry with the same key succeeds on attempt 2", "POST", "/api/v1/notifications",
          payload(retry_id), key=key, status=202,
          expect={"status": "SENT", "attempts": 2, "last_error": None})
    check("Retry did not create a second notification", "GET",
          f"/api/v1/notifications?booking_id={retry_id}", rows=1)

    key = f"booking-{failed_id}-confirmation"
    check("Simulation: fail the next send", "PUT", "/api/v1/simulation",
          {"failure_rate": 0.0, "latency_ms": 0, "fail_next": 1}, expect={"fail_next": 1})
    check("Provider failure is 503", "POST", "/api/v1/notifications", payload(failed_id), key=key,
          status=503, expect={"error": "PROVIDER_UNAVAILABLE"})
    failed = check("Failed notification is stored", "GET",
                   f"/api/v1/notifications?booking_id={failed_id}", rows=1)
    failed = failed[0] if isinstance(failed, list) and failed else {}
    stored = check("Failed notification keeps last_error", "GET",
                   f"/api/v1/notifications/{failed.get('id')}", expect={"status": "FAILED"})
    if "fail_next" not in (stored.get("last_error") or ""):
        failures += 1
        print("       !! last_error does not mention fail_next")

    check("Validation: missing Idempotency-Key is rejected", "POST", "/api/v1/notifications",
          payload(sent_id), status=422, expect={"error": "VALIDATION_ERROR"})
    check("Validation: unknown notification_type is rejected", "POST", "/api/v1/notifications",
          {**payload(sent_id), "notification_type": "FAX"}, key=f"e2e-{stamp}-fax",
          status=422, expect={"error": "VALIDATION_ERROR"})
    check("Validation: patient_id above INT4 range is rejected", "POST", "/api/v1/notifications",
          {**payload(sent_id), "patient_id": 99999999999}, key=f"e2e-{stamp}-big",
          status=422, expect={"error": "VALIDATION_ERROR"})
    check("Unknown notification is 404", "GET", "/api/v1/notifications/999999999",
          status=404, expect={"error": "NOTIFICATION_NOT_FOUND"})
    check("Notification id above INT4 range is 422", "GET", "/api/v1/notifications/99999999999",
          status=422, expect={"error": "VALIDATION_ERROR"})

    check("Simulation: reset to healthy provider", "PUT", "/api/v1/simulation",
          {"failure_rate": 0.0, "latency_ms": 0, "fail_next": 0}, expect={"fail_next": 0})

    print(f"\nFinished: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("RESULT  : ALL CHECKS PASSED" if failures == 0 else f"RESULT  : {failures} CHECK(S) FAILED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
