#!/bin/bash
# Walks through the Docker Compose deployment for the demo video and screenshots.
# Each command is printed before it runs, so the screen shows what was executed.
#
#   scripts/docker/demo.sh            all steps, pausing for Enter between them
#   scripts/docker/demo.sh workflow   one step
#
# Steps: images ps health workflow trace tests logs secrets restart
# Needs the stack running (docker compose up -d --wait), curl and jq;
# "tests" needs a python with the requirements-dev packages.
set -uo pipefail
cd "$(dirname "$0")/../.."

JSON="-H 'content-type: application/json'"
STATUS="-w '  [HTTP %{http_code}]\n'"

title() { printf '\033[1;36m# %s\033[0m\n' "$1"; }
run() {
    printf '\033[1;32m$ %s\033[0m\n' "$1"
    OUT=$(eval "$1")
    printf '%s\n\n' "$OUT"
}
body() { echo "${OUT%  \[HTTP*}"; }   # response JSON without the status suffix

images() {
    title "Task 1: one image per service"
    run "docker image ls 'hospital-booking/*'"
    run "docker compose config --services"
}

ps() {
    title "Task 2: all containers running and healthy"
    run "docker compose ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'"
}

health() {
    title "Task 2: /health of every service (each checks its own database)"
    for port in 8001 8002 8003 8004; do
        run "curl -s -w '\n' localhost:$port/health"
    done
    run "docker inspect --format '{{.Name}} {{.State.Health.Status}}' \$(docker compose ps -q)"
}

workflow() {
    local phone doctor key patient slot booking
    phone=$(printf '9%09d' $(( (RANDOM * 32768 + RANDOM) % 1000000000 )))
    doctor=$(( (RANDOM * 32768 + RANDOM) % 1000000 + 1000 ))
    key="demo-$doctor"
    title "Task 2: main workflow through the containers"
    title "1. Register a patient (patient-service)"
    run "curl -s $STATUS -X POST localhost:8002/api/v1/patients $JSON -d '{\"full_name\":\"Asha Rao\",\"age\":30,\"phone\":\"$phone\"}'"
    patient=$(body | jq .id)
    title "2. Create a doctor slot with capacity 2 (slot-service)"
    run "curl -s $STATUS -X POST localhost:8001/api/v1/slots $JSON -d '{\"doctor_id\":$doctor,\"start_time\":\"2027-06-01T10:00:00Z\",\"end_time\":\"2027-06-01T10:30:00Z\",\"capacity\":2}'"
    slot=$(body | jq .id)
    title "3. Book: booking-service -> patient-service -> slot-service -> notification-service"
    run "curl -s $STATUS -X POST localhost:8003/api/v1/bookings $JSON -H 'Idempotency-Key: $key' -d '{\"patient_id\":$patient,\"slot_id\":$slot}'"
    booking=$(body | jq .booking_id)
    title "4. Same request again: replayed, not booked twice"
    run "curl -s -D - $JSON -X POST localhost:8003/api/v1/bookings -H 'Idempotency-Key: $key' -d '{\"patient_id\":$patient,\"slot_id\":$slot}' | grep -iE '^HTTP|^idempotent|^\{'"
    title "5. Slot shows one place taken"
    run "curl -s -w '\n' localhost:8001/api/v1/slots/$slot"
    title "6. Unknown patient is rejected; nothing reserved"
    run "curl -s $STATUS -X POST localhost:8003/api/v1/bookings $JSON -H 'Idempotency-Key: $key-x' -d '{\"patient_id\":999999,\"slot_id\":$slot}'"
    title "7. Cancel frees the place"
    run "curl -s $STATUS -X POST localhost:8003/api/v1/bookings/$booking/cancel"
    run "curl -s -w '\n' localhost:8001/api/v1/slots/$slot"
}

trace() {
    local cid
    cid=$(docker compose exec -T postgres psql -U postgres -d booking_db -Atc \
        "select correlation_id from bookings where status = 'CANCELLED' order by id desc limit 1")
    title "Task 2: the latest booking's correlation ID in all four containers' logs"
    run "docker compose logs --no-log-prefix | grep $cid | jq -c '{timestamp, service, message, path, status_code}' | sort"
}

tests() {
    title "Task 2: end-to-end tests against the containers"
    run "python -m pytest -v --no-header -p no:cacheprovider scripts/test_end_to_end.py"
}

logs() {
    title "Task 2: container logs (JSON, one line per event)"
    run "docker compose logs --no-log-prefix --tail 6 booking-service | jq -c '{timestamp, level, service, correlation_id, message, status_code}'"
}

secrets() {
    title "Task 1: passwords are Docker secrets, not in images or git"
    run "docker inspect hospital-booking-booking-service-1 --format '{{json .Config.Env}}' | tr ',' '\n' | grep DB_"
    run "docker compose exec booking-service ls -l /run/secrets/"
    run "for s in slot patient booking notification; do echo \"\$s: \$(docker history --no-trunc hospital-booking/\$s-service:1.0 | grep -ciE 'password|secret') matches\"; done"
    run "docker run --rm --entrypoint ls hospital-booking/booking-service:1.0 -a /app"
    run "git check-ignore -v secrets/booking_db_password.txt services/bookingService/.env"
}

restart() {
    title "Task 2: stop and restart, data persists"
    run "docker compose exec -T postgres psql -U postgres -d booking_db -Atc 'select count(*) from bookings'"
    run "docker compose down 2>&1 | grep -E 'Removed'"
    run "docker compose ps -a"
    run "docker volume ls --filter name=hospital-booking"
    run "docker compose up -d --wait 2>&1 | grep Healthy | sort -u"
    run "docker compose logs postgres | grep 'Skipping initialization'"
    run "docker compose exec -T postgres psql -U postgres -d booking_db -Atc 'select count(*) from bookings'"
}

STEPS="images ps health workflow trace tests logs secrets restart"
if [ $# -gt 0 ]; then
    for step in "$@"; do "$step"; done
else
    for step in $STEPS; do
        clear
        "$step"
        read -rp "Enter for next step... "
    done
fi
