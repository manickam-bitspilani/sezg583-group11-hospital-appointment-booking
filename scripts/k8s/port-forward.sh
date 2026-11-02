#!/bin/bash
# Forwards localhost:8001-8004 to the services in minikube, so the curl
# commands, Swagger pages and scripts/test_end_to_end.py work unchanged.
# Stop with Ctrl+C. (Stop docker compose first: it uses the same ports.)
set -uo pipefail
NS=hospital-booking

# A local service on 127.0.0.1:800x would shadow the forward, as in Task 3.
"$(dirname "$0")/../docker/check_ports.sh" || exit 1

# kubectl port-forward is tied to one pod: when that pod is replaced (rollout,
# self-healing demo) it keeps the port open but every request fails. So check
# every 2s that the port still answers, and restart the forward when it doesn't.
forward() {
    local svc=$1 port=$2 pid
    while true; do
        kubectl -n "$NS" port-forward "svc/$svc" "$port:$port" >/dev/null 2>&1 &
        pid=$!
        sleep 2
        while kill -0 "$pid" 2>/dev/null && curl -s -m 2 -o /dev/null "localhost:$port/health"; do
            sleep 2
        done
        kill "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
        echo "$(date +%T) $svc: pod changed, reconnecting"
    done
}

trap 'kill 0' EXIT
forward slot-service 8001 &
forward patient-service 8002 &
forward booking-service 8003 &
forward notification-service 8004 &
echo "Forwarding localhost:8001-8004 to namespace $NS (Ctrl+C to stop)"
wait
