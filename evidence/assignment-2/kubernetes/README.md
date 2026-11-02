# Part B: Kubernetes on minikube

The whole system deployed on minikube (Kubernetes v1.31, docker driver,
4 CPUs, 3 GB) with the manifests in [`k8s/`](../../../k8s) and
[`scripts/k8s/deploy.sh`](../../../scripts/k8s/deploy.sh). Every log starts with
the command and UTC time, followed by the unedited output (colour codes
removed). Each `NN-<step>.log` is the output of `scripts/k8s/demo.sh <step>`,
and the screenshot with the same number is that run in a real macOS Terminal
(`screencapture` of the window), so they show the same IDs and counts.

## Claims and evidence

| # | Claim | Result | Log | Screenshot |
|---|---|---|---|---|
| 00 | Images build inside minikube's Docker; manifests apply; every rollout completes | 4 images built, 6 pods `1/1 Running` | [00](00-deploy.log) | |
| 01 | Deployments, StatefulSet, Services, PVC and HPA are in place | slot-service 2/2; booking-service is a NodePort `8003:30083`; PVC `data-postgres-0` 1Gi `Bound`; HPA 1–4 at 50% CPU | [01](01-overview.log) | [01](screenshots/01-overview.png) |
| 02 | The main workflow works through the cluster (port-forward on localhost:8001–8004) | patient 201 → slot 201 → booking 201 `CONFIRMED`/`SENT` → same key replayed (`idempotent-replayed: true`, same body) → unknown patient 404 → cancel 200 frees the place (`reserved_count` 1 → 0) | [02](02-workflow.log) | [02](screenshots/02-workflow.png) |
| 03 | The end-to-end suite used for Compose passes against the cluster | 7 passed | [03](03-tests.log) | [03](screenshots/03-tests.png) |
| 04 | Passwords are a Secret mounted as files; each pod sees only its own; containers run as non-root | Secret has 5 keys; booking-service sees only `booking_db_password`; env has `DB_PASSWORD_FILE`, no password; `uid=10001(app)`; readiness `/health`, liveness TCP, CPU/memory limits | [04](04-secrets.log) | [04](screenshots/04-secrets.png) |
| 05 | Manual scaling | patient-service 1 → 3 pods; the Service has 3 endpoints; back to 1 | [05](05-scale.log) | [05](screenshots/05-scale.png) |
| 06 | Self-healing | A deleted slot-service pod is replaced at once (`Init:0/1` → `Running` in 5 s) | [06](06-selfheal.log) | [06](screenshots/06-selfheal.png) |
| 07 | Data persists when the database pod is deleted | `postgres-0` deleted and recreated on the same PVC; booking count 22 → 22 | [07](07-persist.log) | [07](screenshots/07-persist.png) |
| 08 | The HPA scales booking-service with load and back | Load on `/openapi.json` (20 concurrent, 90 s): CPU 500% of target → 1 → 4 pods; after the load 4 → 2 → 1; 68,872 requests, 0 failed | [08](08-hpa.log) | [08](screenshots/08-hpa.png) |

In 08 the HPA shows `<unknown>` for the first minute: the pods had been
replaced shortly before (probe change below), and metrics-server needs about a
minute of samples for a new pod.

## Problems found while deploying, and fixes

| Problem | Cause | Fix |
|---|---|---|
| postgres failed to start: `mkdir .../run/secrets/kubernetes.io: read-only file system` | The read-only Secret mount at `/run/secrets` blocked Kubernetes from mounting the service-account token below it | `automountServiceAccountToken: false` (no pod calls the Kubernetes API) |
| Services: `container has runAsNonRoot and image has non-numeric user (app)` | `runAsNonRoot` cannot check a user name | `runAsUser: 10001`, the uid the Dockerfiles create; the init container runs as postgres's uid 70 |
| `deploy.sh` never returned | `minikube service --url` opens a tunnel and blocks with the docker driver on macOS | Print the access commands instead |
| HPA did not scale under load | Load on `/health` mostly waits on the database; the pod's CPU stayed below 40% | Load `/openapi.json`, which is CPU work in the pod |
| postgres restarted by its liveness probe with no load | Default probe timeout is 1 s; with the 8 GB laptop swapping, `pg_isready` sometimes took longer | Probe timeouts 3–5 s, postgres liveness `failureThreshold: 6` |
| Tests after a rollout failed to connect | `kubectl port-forward` is tied to one pod and keeps the port open after that pod is replaced | `port-forward.sh` checks each port every 2 s and reconnects |
| Tests created data in the wrong place | Local `python main.py` processes on `127.0.0.1:8001/8002/8004` shadowed the forward (the Task 3 problem again) | Stopped them; `port-forward.sh` runs `check_ports.sh` first |

## Reproduce

```bash
minikube start --driver=docker --cpus=4 --memory=3072
./scripts/k8s/deploy.sh
./scripts/k8s/port-forward.sh        # in another terminal; stop docker compose first
./scripts/k8s/demo.sh                # all steps, or e.g. ./scripts/k8s/demo.sh hpa
```
