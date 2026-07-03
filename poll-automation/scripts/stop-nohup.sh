#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PIDFILE="data/bot.pid"

if [[ ! -f "$PIDFILE" ]]; then
  echo "No PID file — bot may not be running."
  pkill -f "run.py --config config.nohup.yaml" 2>/dev/null || true
  exit 0
fi

PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "Stopped bot (PID $PID)"
else
  echo "PID $PID not running"
fi
rm -f "$PIDFILE"
