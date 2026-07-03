#!/usr/bin/env bash
# Verify the background bot is recording votes.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Process ==="
if [[ -f data/bot.pid ]] && kill -0 "$(cat data/bot.pid)" 2>/dev/null; then
  echo "RUNNING  PID $(cat data/bot.pid)"
else
  echo "NOT RUNNING"
  pgrep -fl "run.py" || true
fi

echo ""
echo "=== Vote successes in log ==="
SUCCESS=$(grep -c "Results loaded" data/run.log 2>/dev/null || echo 0)
ERRORS=$(grep -c "Cycle error" data/run.log 2>/dev/null || echo 0)
echo "Results loaded: $SUCCESS"
echo "Cycle errors:   $ERRORS"

echo ""
echo "=== State ==="
cat data/state.json 2>/dev/null || echo "(no state file)"

echo ""
echo "=== Last 8 log lines ==="
tail -8 data/run.log 2>/dev/null || echo "(no log)"
