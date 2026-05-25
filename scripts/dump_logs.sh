#!/usr/bin/env bash
# Dump per-service compose logs to logs/<service>.log
# Usage: dump_logs.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

SERVICES=(relay mock-openai node-a node-b routstrd cli-runner)

for svc in "${SERVICES[@]}"; do
  outfile="$LOG_DIR/${svc}.log"
  echo "Dumping $svc → $outfile"
  docker compose -f "$ROOT/compose.yml" logs --no-log-prefix "$svc" > "$outfile" 2>&1 || true
done

echo "Logs written to $LOG_DIR/"
