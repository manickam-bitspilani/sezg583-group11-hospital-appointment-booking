#!/bin/bash
# Walks through the minikube deployment for the demo video and screenshots.
# Each command is printed before it runs, so the screen shows what was executed.
#
#   scripts/k8s/demo.sh             all steps, pausing for Enter between them
#   scripts/k8s/demo.sh selfheal    one step
#
# Steps: overview workflow tests secrets scale selfheal persist hpa
# Needs ./scripts/k8s/deploy.sh done and, for "workflow",
# ./scripts/k8s/port-forward.sh running in another terminal.
set -uo pipefail
cd "$(dirname "$0")/../.."

K="kubectl -n hospital-booking"
PSQL="$K exec postgres-0 -- psql -U postgres -d booking_db -Atc"

title() { printf '\033[1;36m# %s\033[0m\n' "$1"; }
run() {
    printf '\033[1;32m$ %s\033[0m\n' "$1"
    eval "$1"
    echo
}

overview() {
    title "Deployments, StatefulSet, Services (booking is a NodePort), volume and HPA"
    run "$K get deployments,statefulsets,services"
    run "$K get pods -o wide"
    run "$K get pvc,hpa"
}

workflow() {
    title "Main workflow against the cluster (via port-forward on localhost:8001-8004)"
    ./scripts/docker/demo.sh workflow
}

tests() {
    title "End-to-end tests against the cluster (the same suite as for Compose)"
    ./scripts/docker/demo.sh tests
}

secrets() {
    title "Passwords: one Secret, each pod mounts only its own key as a file"
    run "$K get secret db-passwords -o json | jq -c '.data | keys'"
    run "$K exec deploy/booking-service -c booking-service -- ls /run/secrets"
    run "$K exec deploy/booking-service -c booking-service -- env | grep DB_"
    run "$K exec deploy/booking-service -c booking-service -- id"
    run "$K get deployment booking-service -o jsonpath='{range .spec.template.spec.containers[0]}readiness: {.readinessProbe.httpGet.path}{\"\\n\"}liveness: tcp {.livenessProbe.tcpSocket.port}{\"\\n\"}resources: {.resources}{\"\\n\"}{end}'"
}

scale() {
    title "Scale patient-service from 1 to 3 replicas; the Service balances across them"
    run "$K scale deployment/patient-service --replicas=3"
    run "$K rollout status deployment/patient-service"
    run "$K get pods -l app=patient-service"
    run "$K get endpoints patient-service"
    title "And back to 1"
    run "$K scale deployment/patient-service --replicas=1"
}

selfheal() {
    local pod
    pod=$($K get pods -l app=slot-service -o jsonpath='{.items[0].metadata.name}')
    title "Self-healing: delete a slot-service pod; the Deployment replaces it"
    run "$K get pods -l app=slot-service"
    run "$K delete pod $pod --wait=false"
    run "$K get pods -l app=slot-service"
    $K wait --for=delete "pod/$pod" --timeout=60s >/dev/null
    run "$K wait --for=condition=Ready pods -l app=slot-service --timeout=90s"
    run "$K get pods -l app=slot-service"
}

persist() {
    title "Data persists: delete the postgres pod; the StatefulSet recreates it on the same volume"
    run "$PSQL 'select count(*) from bookings'"
    run "$K delete pod postgres-0"
    run "$K wait --for=condition=Ready pod/postgres-0 --timeout=120s"
    run "$K get pvc"
    run "$PSQL 'select count(*) from bookings'"
}

hpa() {
    title "Autoscaling: load booking-service and watch the HPA add pods (CPU target 50%)"
    run "$K get hpa booking-service"
    # /openapi.json is CPU work inside booking-service; /health mostly waits
    # on the database, which barely moves the pod's CPU.
    run "$K run load --image=httpd:alpine --restart=Never -- ab -t 90 -n 10000000 -c 20 http://booking-service:8003/openapi.json"
    watch_hpa 8
    run "$K get pods -l app=booking-service"
    title "Load stops after 90s; the HPA scales back down after its 60s stabilization window"
    watch_hpa 16
    run "$K logs load | grep -E 'Complete requests|Failed requests|Requests per second'"
    run "$K delete pod load"
}

# Prints the HPA's CPU and replica count every 15s, $1 times.
watch_hpa() {
    for _ in $(seq "$1"); do
        sleep 15
        printf '%s  ' "$(date +%T)"
        $K get hpa booking-service --no-headers | awk '{print $1, $4, "replicas=" $7}'
    done
    echo
}

STEPS="overview workflow tests secrets scale selfheal persist hpa"
if [ $# -gt 0 ]; then
    for step in "$@"; do "$step"; done
else
    for step in $STEPS; do
        clear
        "$step"
        read -rp "Enter for next step... "
    done
fi
