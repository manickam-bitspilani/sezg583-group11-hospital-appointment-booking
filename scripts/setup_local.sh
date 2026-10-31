#!/bin/bash
# One-time setup for running the services locally (without Docker):
#   1. random passwords in secrets/*.txt (shared with docker compose, gitignored)
#   2. the four databases and roles in the local PostgreSQL
#   3. each service's .env, created from .env.example with its password filled in
# Existing secrets and .env files are kept. Pass psql connection flags if needed,
# e.g. ./scripts/setup_local.sh -h localhost -U postgres
set -euo pipefail

cd "$(dirname "$0")/.."
./scripts/docker/generate_secrets.sh

pw() { cat "secrets/$1_db_password.txt"; }

if [ $# -eq 0 ]; then set -- -U postgres; fi
if psql "$@" -d postgres -Atc "SELECT 1 FROM pg_roles WHERE rolname = 'booking_svc'" | grep -q 1; then
    # The roles were created earlier with passwords this script may not know,
    # so a new .env must not be given the secrets/*.txt password.
    echo "databases already exist, skipping scripts/setup_databases.sql"
    created_databases=false
else
    psql "$@" -d postgres \
        -v patient_pw="$(pw patient)" \
        -v slot_pw="$(pw slot)" \
        -v booking_pw="$(pw booking)" \
        -v notification_pw="$(pw notification)" \
        -f scripts/setup_databases.sql
    created_databases=true
fi

for svc in patient slot booking notification; do
    env_file="services/${svc}Service/.env"
    if [ -f "$env_file" ]; then
        echo "kept    $env_file"
    elif $created_databases; then
        sed "s/^DB_PASSWORD=.*/DB_PASSWORD=$(pw "$svc")/" "services/${svc}Service/.env.example" > "$env_file"
        echo "created $env_file"
    else
        cp "services/${svc}Service/.env.example" "$env_file"
        echo "created $env_file  -> set DB_PASSWORD to ${svc}_svc's existing password"
    fi
done
