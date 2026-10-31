#!/bin/bash
# Pre-flight check before `docker compose up`: fails if anything other than
# Docker already listens on a published port. A local service bound to
# 127.0.0.1:800x takes priority over Docker's *:800x on macOS, so requests to
# localhost silently reach it instead of the container.
set -uo pipefail

status=0
for port in 8001 8002 8003 8004; do
    others=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR > 1 && $1 !~ /^com\.docke/ {print $1 " (pid " $2 ") " $9}')
    if [ -n "$others" ]; then
        echo "CONFLICT port $port is also served by: $others"
        status=1
    else
        echo "ok       port $port"
    fi
done
[ $status -eq 0 ] || echo "Stop the processes above (e.g. local 'python main.py') before using the containers."
exit $status
