#!/usr/bin/env bash
# Run one poll cycle if rate limits allow. Safe for cron every 15–30 minutes.
set -euo pipefail
cd "$(dirname "$0")/.."

CONFIG="${POLL_CONFIG:-config.cloud.yaml}"
STATE="data/state.json"

mkdir -p data

# Skip if past deadline (controller also checks, but avoid launching browser)
if python3 - "$CONFIG" <<'PY'
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml
config_path = Path(sys.argv[1])
cfg = yaml.safe_load(config_path.read_text())
deadline = datetime.fromisoformat(cfg["poll"]["deadline"]).astimezone(timezone.utc)
raise SystemExit(0 if datetime.now(timezone.utc) < deadline else 1)
PY
then
  :
else
  echo "Poll deadline passed — not running."
  exit 0
fi

# Honor hourly/daily caps without opening the browser
if python3 - "$CONFIG" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml

config_path = Path(sys.argv[1])
cfg = yaml.safe_load(config_path.read_text())
state_path = Path("data/state.json")
timing = cfg["timing"]
now = datetime.now(timezone.utc)
hour = now.strftime("%Y%m%d-%H")
day = now.strftime("%Y%m%d")

votes_hour = votes_day = 0
if state_path.exists():
    s = json.loads(state_path.read_text())
    votes_hour = s.get("votes_this_hour", 0) if s.get("hour_bucket") == hour else 0
    votes_day = s.get("votes_today", 0) if s.get("day_bucket") == day else 0

if votes_hour >= int(timing["max_votes_per_hour"]):
    print(f"Hourly cap reached ({votes_hour}) — skipping.")
    raise SystemExit(1)
if votes_day >= int(timing["max_votes_per_day"]):
    print(f"Daily cap reached ({votes_day}) — skipping.")
    raise SystemExit(1)
PY
then
  exec python3 run.py --once --config "$CONFIG"
else
  echo "Skipped (rate cap)."
  exit 0
fi
