#!/usr/bin/env bash
# Inner loop invoked by start-nohup.sh (optionally under caffeinate).
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG="${POLL_CONFIG:-config.aggressive.yaml}"
LOG="data/run.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

proxy_count() {
  local n=0
  [[ -f proxies.txt ]] && n=$(grep -cve '^\s*$' -e '^\s*#' proxies.txt || true)
  if [[ -n "${PROXY_LIST:-}" ]]; then
    n=$((n + $(echo "$PROXY_LIST" | tr ',' '\n' | grep -c . || true)))
  fi
  echo "$n"
}

log "Daemon loop config=$CONFIG proxies=$(proxy_count)"
if [[ "$(proxy_count)" -eq 0 ]]; then
  log "WARNING: no proxies configured — aggressive mode uses a single IP (high block risk)"
fi

while true; do
  if ! python3 - "$CONFIG" <<'PY'
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml
cfg = yaml.safe_load(Path(sys.argv[1]).read_text())
deadline = datetime.fromisoformat(cfg["poll"]["deadline"]).astimezone(timezone.utc)
raise SystemExit(0 if datetime.now(timezone.utc) < deadline else 1)
PY
  then
    log "Poll deadline reached — daemon exiting"
    exit 0
  fi

  log "--- invoking vote cycle ---"
  if python3 -u run.py --once --config "$CONFIG" >> "$LOG" 2>&1; then
    log "Cycle finished OK"
  else
    log "Cycle failed — will retry after wait"
  fi

  sleep_sec=$(python3 - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
p = Path("data/scheduler.json")
if not p.exists():
    print(900)
    raise SystemExit(0)
s = json.loads(p.read_text())
n = s.get("next_cycle_after")
if not n:
    print(900)
    raise SystemExit(0)
wake = datetime.fromisoformat(n)
if wake.tzinfo is None:
    wake = wake.replace(tzinfo=timezone.utc)
sec = (wake - datetime.now(timezone.utc)).total_seconds()
print(max(60, int(sec)))
PY
)
  log "Sleeping ${sleep_sec}s until next cycle"
  sleep "$sleep_sec"
done
