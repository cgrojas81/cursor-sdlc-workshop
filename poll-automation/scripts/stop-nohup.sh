#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PIDFILE="data/bot.pid"

stop_pid() {
  local pid="$1"
  # Kill child processes (caffeinate / bash loop / python)
  pkill -P "$pid" 2>/dev/null || true
  kill "$pid" 2>/dev/null || true
}

if [[ -f "$PIDFILE" ]]; then
  PID="$(cat "$PIDFILE")"
  if kill -0 "$PID" 2>/dev/null; then
    stop_pid "$PID"
    echo "Stopped daemon PID $PID"
  fi
  rm -f "$PIDFILE"
fi

pkill -f "run.py --config config.nohup.yaml" 2>/dev/null || true
pkill -f "run.py --once --config config.nohup.yaml" 2>/dev/null || true
echo "Done."
