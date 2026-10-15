#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$PROJECT_ROOT/.yolov9_dashboard.pid"
if [[ ! -f "$PID_FILE" ]]; then
  echo "No dashboard PID file found."
  exit 0
fi
PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in $(seq 1 20); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.5
  done
fi
rm -f "$PID_FILE"
echo "Dashboard stopped. Training children are managed by the dashboard and should be checked separately before stopping the Brev instance."
