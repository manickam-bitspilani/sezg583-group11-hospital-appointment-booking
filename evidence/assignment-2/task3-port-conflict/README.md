# Task 3: Deployment troubleshooting — containers healthy, but traffic never reaches them

A genuine problem we hit while deploying with Docker Compose. Every log starts
with the exact command that produced it, followed by the unedited output.

## Symptom

`docker compose up --build` succeeded and `docker compose ps` showed all five
containers `(healthy)` ([01](01-compose-up-and-ps.log)). The end-to-end test
against `localhost:8001–8004` passed, 7 of 7. Yet the container's `booking_db`
had **the same number of bookings before and after the test** (10 → 10,
[02](02-symptom-tests-pass-but-db-unchanged.log)). The deployment looked
correct and the application "worked", but not the deployed one.

## Diagnosis

| Step | Tool | Finding | Log |
|---|---|---|---|
| 1 | `docker compose exec postgres psql` | Test bookings are not in the container's database | [02](02-symptom-tests-pass-but-db-unchanged.log) |
| 2 | `docker compose logs booking-service` + `X-Correlation-ID` | The correlation ID of a request to `127.0.0.1:8003` never appears in the container log; the container log has 0 booking requests, only healthchecks | [03](03-diagnosis-correlation-id-not-in-container-logs.log) |
| 3 | `lsof -iTCP -sTCP:LISTEN`, `docker inspect` | **Two listeners per port**: Docker on `*:800x` (`HostIp 0.0.0.0`) and a `Python main.py` on `127.0.0.1:800x` | [04](04-diagnosis-two-listeners-per-port.log) |
| 4 | `curl -g http://[::1]:8003/health` | A request over IPv6 loopback, which only Docker listens on, does show up in the container log | [05](05-diagnosis-confirm-via-ipv6.log) |
| 5 | `scripts/docker/check_ports.sh` | The new pre-flight check reports the conflict on all four ports (exit code 1) | [06](06-diagnosis-preflight-check.log) |

**Root cause.** The Assignment 1 services, started earlier with
`python main.py`, were still running and bound to `127.0.0.1:8001–8004`. Docker
publishes the containers on the wildcard address `*:800x`. On macOS the more
specific `127.0.0.1` binding wins for connections to `localhost`/`127.0.0.1`, so
both programs could listen on the same port without a "port already in use"
error, and every request from the host went to the old local processes (which
use the local PostgreSQL), never to the containers.

## Solution

1. Stopped the four local processes (`kill <pid>`; [07](07-fix-stop-local-services.log)).
   Afterwards only Docker listens on 8001–8004 and the pre-flight check passes.
2. Prevention: added [`scripts/docker/check_ports.sh`](../../../scripts/docker/check_ports.sh)
   and a step in the README (section 6) to run it before using the containers.

## Validation

[08](08-validation-workflow-reaches-containers.log): the same test with the same
URLs passes, 7 of 7, and the container's `booking_db` now grows (10 → 20). The
container log shows the booking requests, and one booking's correlation ID is
traced through all four containers:

```
patient-service       GET  /api/v1/patients/16/eligibility  200
slot-service          POST /api/v1/reservations             201  Reservation confirmed
booking-service       POST /api/v1/bookings                 201  Booking completed
notification-service  POST /api/v1/notifications            202  Mock SMS sent
```

## Learning

- **"Healthy" and "tests pass" do not prove the deployment is serving traffic.**
  The healthchecks run *inside* each container, so they were green even though
  nothing from the host reached it. Verify where requests land: in the
  container's logs and its database.
- **Correlation IDs pay off in deployment, not just in debugging code.** Looking
  for one request's ID in `docker compose logs` answered "did it reach the
  container?" in a single command.
- **Port conflicts do not always fail loudly.** A wildcard bind and a loopback
  bind can coexist, so there is no "address already in use" error. Check the
  host's listeners (`lsof`) as well as the containers (`docker compose ps`,
  `docker inspect`).
- **Clean up the previous environment before deploying.** Moving from
  "run locally" to "run in containers" means stopping the local run; the
  pre-flight script makes that an explicit step instead of something to remember.
