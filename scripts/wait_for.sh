#!/usr/bin/env bash
# Poll an HTTP endpoint until it responds 200 or timeout is reached.
# Usage: wait_for.sh <name> <url> [timeout_seconds]

set -euo pipefail

NAME="${1:?service name required}"
URL="${2:?URL required}"
TIMEOUT="${3:-60}"

start=$(date +%s)
echo "Waiting for $NAME at $URL (timeout: ${TIMEOUT}s)..."

while true; do
  now=$(date +%s)
  elapsed=$(( now - start ))

  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    echo "TIMEOUT: $NAME not ready after ${TIMEOUT}s"
    exit 1
  fi

  if curl -sf --max-time 2 "$URL" >/dev/null 2>&1; then
    echo "$NAME is ready (${elapsed}s)"
    exit 0
  fi

  sleep 2
done
