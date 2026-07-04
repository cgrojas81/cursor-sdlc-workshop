#!/usr/bin/env bash
# Long-running daemon: one fresh browser per vote, survives crashes, prevents Mac sleep.
set -euo pipefail
cd "$(dirname "$0")/.."

LOG="data/run.log"
PIDFILE="data/bot.pid"

mkdir -p data

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Already running (PID $(cat "$PIDFILE")). Run ./scripts/stop-nohup.sh first."
  exit 1
fi

{
  echo ""
  echo "========== DAEMON START $(date) =========="
} >> "$LOG"

export PYTHONUNBUFFERED=1
LOOP="$(pwd)/scripts/daemon-loop.sh"
chmod +x "$LOOP"

if command -v caffeinate >/dev/null 2>&1; then
  nohup caffeinate -dimsu -i "$LOOP" >> "$LOG" 2>&1 &
else
  nohup "$LOOP" >> "$LOG" 2>&1 &
fi

echo $! > "$PIDFILE"
sleep 2

if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Daemon started PID $(cat "$PIDFILE")"
  echo "Status: ./scripts/status.sh"
  echo "Stop:   ./scripts/stop-nohup.sh"
else
  echo "Daemon failed — check $LOG"
  tail -30 "$LOG"
  rm -f "$PIDFILE"
  exit 1
fi
