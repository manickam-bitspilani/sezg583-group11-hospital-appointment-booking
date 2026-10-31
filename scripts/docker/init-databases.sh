#!/bin/bash
# Runs once, when the postgres container starts with an empty data volume.
# Creates one database and one login role per service (database-per-service).
# Passwords are read from Docker secrets, never from this file.
set -euo pipefail

for svc in patient slot booking notification; do
    password="$(cat "/run/secrets/${svc}_db_password")"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -v pw="$password" <<SQL
CREATE ROLE ${svc}_svc LOGIN PASSWORD :'pw';
CREATE DATABASE ${svc}_db OWNER ${svc}_svc;
REVOKE CONNECT ON DATABASE ${svc}_db FROM PUBLIC;
SQL
    echo "init-databases: created ${svc}_db / ${svc}_svc"
done
