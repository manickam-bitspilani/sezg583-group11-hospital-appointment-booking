#!/bin/bash
# Creates the Docker secret files in secrets/ with random passwords.
# Existing files are kept, so running it again is safe.
set -euo pipefail

cd "$(dirname "$0")/../.."
mkdir -p secrets

for name in postgres_password patient_db_password slot_db_password booking_db_password notification_db_password; do
    file="secrets/${name}.txt"
    if [ -f "$file" ]; then
        echo "kept    $file"
    else
        openssl rand -hex 16 > "$file"
        chmod 644 "$file"  # the containers' non-root users must be able to read it
        echo "created $file"
    fi
done
