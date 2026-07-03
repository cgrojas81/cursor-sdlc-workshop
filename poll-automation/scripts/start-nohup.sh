#!/usr/bin/env bash
# Start poll bot in background on Mac (user must stay logged in; lid open or clamshell + external display).
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG="${POLL_CONFIG:-config.nohup.yaml}"
LOG="data/run.log"
PIDFILE="data/bot.pid"

mkdir -p data

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Bot already running (PID $(cat "$PIDFILE")). Run scripts/stop-nohup.sh first."
  exit 1
fi

# Quick headless smoke test optional — skip, go straight to start

{
  echo ""
  echo "========== BOT START $(date) =========="
  echo "Config: $CONFIG"
} >> "$LOG"

export PYTHONUNBUFFERED=1
nohup python3 -u run.py --config "$CONFIG" >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"

sleep 2
if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Started poll bot PID $(cat "$PIDFILE")"
  echo "Watch: tail -f $LOG"
  echo "Stop:  ./scripts/stop-nohup.sh"
else
  echo "Bot exited immediately — check $LOG"
  tail -20 "$LOG"
  rm -f "$PIDFILE"
  exit 1
fi
