#!/bin/bash
# Deploys the hospital booking system to minikube.
#   minikube start --driver=docker --cpus=4 --memory=3072
#   ./scripts/k8s/deploy.sh
# Builds the images inside minikube's Docker (no registry needed), creates the
# password Secret from secrets/*.txt and applies k8s/.
set -euo pipefail
cd "$(dirname "$0")/../.."

NS=hospital-booking
PASSWORDS="postgres_password patient_db_password slot_db_password booking_db_password notification_db_password"

[ -f secrets/postgres_password.txt ] || ./scripts/docker/generate_secrets.sh
minikube addons enable metrics-server >/dev/null

echo "== Building images in minikube's Docker"
eval "$(minikube docker-env)"
docker compose build

echo "== Applying manifests"
kubectl apply -f k8s/00-namespace.yaml
args=()
for name in $PASSWORDS; do args+=(--from-file="$name=secrets/$name.txt"); done
# --dry-run | apply: create or update, and the passwords are never written to a file
kubectl -n "$NS" create secret generic db-passwords "${args[@]}" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create configmap postgres-init \
    --from-file=10-init-databases.sh=scripts/docker/init-databases.sh --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f k8s/

echo "== Waiting for the rollout"
kubectl -n "$NS" rollout status statefulset/postgres --timeout=180s
for d in slot-service patient-service notification-service booking-service; do
    kubectl -n "$NS" rollout status "deployment/$d" --timeout=180s
done
kubectl -n "$NS" get pods -o wide
# Not `minikube service --url` here: with the docker driver on macOS it opens
# a tunnel and blocks until Ctrl+C.
echo "Reach the services with ./scripts/k8s/port-forward.sh (localhost:8001-8004),"
echo "or booking-service's NodePort with: minikube service booking-service -n $NS"
