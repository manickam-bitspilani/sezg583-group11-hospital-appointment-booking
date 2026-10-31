# Tasks 1 and 2: Docker images and the Docker Compose deployment

Captured from a clean start (`docker compose down -v`: no containers, no data).
Every log starts with the exact command that produced it, followed by the
unedited output.

| Claim (brief) | Evidence | Log |
|---|---|---|
| **T1** A separate image is built for each service from its own Dockerfile | `docker compose build --no-cache` builds 4 images; `docker image ls` lists them | [01](01-build-images.log) |
| **T1** Containers are created and started | `docker compose up -d --wait`; `docker compose ps` | [02](02-up-containers-started.log) |
| **T1** Database runs as a container; one database and role per service | `postgres` container log: `init-databases: created patient_db / patient_svc` ... | [02](02-up-containers-started.log) |
| **T1/T2** All services running and healthy | `/health` of each service (`database: up`); `docker inspect` health status of all 5 containers | [03](03-health.log) |
| **T2** Main application workflow | Register patient → create slot → book (201 `CONFIRMED`, SMS `SENT`) → repeat is replayed → unknown patient rejected (404) → cancel frees the place and sends a cancellation SMS | [04](04-workflow-curl.log) |
| **T2** Communication between microservices | One booking's `X-Correlation-ID` found in the logs of all four service containers, in call order: patient eligibility → slot reservation → notification → booking | [05](05-inter-service-trace.log) |
| **T2** Workflow, failure cases included | `scripts/test_end_to_end.py`: 7 passed against the containers; resulting rows in the container's `booking_db` | [06](06-e2e-tests.log) |
| **T2** Container logs | `docker compose logs` (JSON, one line per request, with correlation IDs) | [07](07-container-logs.log) |
| **T1** Configuration from environment, no secrets in images or git | `docker inspect` env holds only `DB_PASSWORD_FILE`; secret mounted at `/run/secrets/`; `docker history` and image contents show no password, `.env` or tests; `git check-ignore` for `secrets/` and `.env` | [08](08-secrets-not-in-images.log) |
| **T2** Stop and restart, works again | `docker compose down` → `up`: Postgres reuses the volume (`Skipping initialization`), bookings count unchanged, booking 1 still readable, a new booking succeeds | [09](09-restart-data-persists.log) |

Reproduce: see [README section 6](../../../readme.md#6-run-with-docker-compose).
The deployment problem we hit (Task 3) is in
[../task3-port-conflict/](../task3-port-conflict/).
