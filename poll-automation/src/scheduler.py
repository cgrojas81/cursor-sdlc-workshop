"""Schedule next run and pre-flight skip checks (avoid opening browser when idle)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .controller import ControllerState, _parse_deadline


def load_scheduler(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_scheduler(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def write_heartbeat(path: Path, *, ok: bool, detail: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "ok": ok,
                "detail": detail,
            },
            indent=2,
        )
    )


def should_open_browser(
    cfg: dict[str, Any],
    state: ControllerState,
    scheduler_path: Path,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """
    Return (True, reason) if we should launch the browser for a vote cycle.
    """
    now = now or datetime.now(timezone.utc)

    deadline = _parse_deadline(cfg["poll"]["deadline"])
    if now >= deadline:
        return False, "Poll deadline reached"

    timing = cfg["timing"]
    state.touch_buckets(now)

    if state.votes_this_hour >= int(timing["max_votes_per_hour"]):
        return False, f"Hourly cap ({state.votes_this_hour}/{timing['max_votes_per_hour']})"
    if state.votes_today >= int(timing["max_votes_per_day"]):
        return False, f"Daily cap ({state.votes_today}/{timing['max_votes_per_day']})"

    sched = load_scheduler(scheduler_path)
    next_after = sched.get("next_cycle_after")
    if next_after:
        wake = datetime.fromisoformat(next_after)
        if wake.tzinfo is None:
            wake = wake.replace(tzinfo=timezone.utc)
        if now < wake.astimezone(timezone.utc):
            return False, f"Waiting until {next_after} ({sched.get('last_reason', '')})"

    return True, "Ready for cycle"


def schedule_next_cycle(
    scheduler_path: Path,
    wait_sec: float,
    decision_action: str,
    reason: str,
) -> None:
    wake = datetime.now(timezone.utc).timestamp() + max(0.0, wait_sec)
    next_dt = datetime.fromtimestamp(wake, tz=timezone.utc)
    save_scheduler(
        scheduler_path,
        {
            "next_cycle_after": next_dt.isoformat(),
            "last_decision": decision_action,
            "last_reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
