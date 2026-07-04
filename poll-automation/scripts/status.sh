#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Daemon ==="
if [[ -f data/bot.pid ]] && kill -0 "$(cat data/bot.pid)" 2>/dev/null; then
  echo "RUNNING  PID $(cat data/bot.pid)"
else
  echo "NOT RUNNING"
fi

echo ""
echo "=== Votes ==="
SUCCESS=$(grep -c "Results loaded" data/run.log 2>/dev/null || echo 0)
CYCLES=$(grep -c "Cycle start" data/run.log 2>/dev/null || echo 0)
ERRORS=$(grep -c "Cycle failed" data/run.log 2>/dev/null || echo 0)
echo "Successful results: $SUCCESS"
echo "Cycle starts:         $CYCLES"
echo "Cycle failures:       $ERRORS"

echo ""
echo "=== State ==="
cat data/state.json 2>/dev/null || echo "(none)"

echo ""
echo "=== Scheduler ==="
cat data/scheduler.json 2>/dev/null || echo "(none)"

echo ""
echo "=== Heartbeat ==="
cat data/heartbeat.json 2>/dev/null || echo "(none)"

echo ""
echo "=== Last 10 log lines ==="
tail -10 data/run.log 2>/dev/null || echo "(no log)"
