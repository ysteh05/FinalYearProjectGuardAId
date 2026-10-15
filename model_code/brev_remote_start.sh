#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
PID_FILE="$PROJECT_ROOT/.yolov9_dashboard.pid"
LOG_FILE="$PROJECT_ROOT/model_runs/yolov9_full_dataset_v1/brev_dashboard.log"
PORT="${YOLO_DASHBOARD_PORT:-8767}"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing virtual environment: $PYTHON" >&2
  exit 2
fi
mkdir -p "$(dirname "$LOG_FILE")"
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Dashboard already running (PID $(cat "$PID_FILE")) on port $PORT"
  exit 0
fi
if command -v ss >/dev/null && ss -ltn | awk '{print $4}' | grep -Eq ":${PORT}$"; then
  echo "Port $PORT is already in use; refusing to start a second dashboard." >&2
  exit 3
fi
nohup "$PYTHON" -u "$PROJECT_ROOT/model_code/yolov9_weapon_dashboard.py" \
  --host 127.0.0.1 --port "$PORT" --no-browser \
  </dev/null >>"$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/full-v1/health" >/dev/null; then
    echo "Dashboard ready at http://127.0.0.1:${PORT}/full-dataset-v1 (PID $(cat "$PID_FILE"))"
    exit 0
  fi
  sleep 1
done
echo "Dashboard did not become healthy. Recent log:" >&2
tail -n 60 "$LOG_FILE" >&2
exit 4
