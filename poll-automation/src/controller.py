"""Vote pacing controller: 2–3% lead band with hourly/daily caps."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .monitor import Standings, compute_lead_pct, find_target


class Action(str, Enum):
    VOTE = "vote"
    MONITOR = "monitor"
    PAUSE = "pause"
    STOP = "stop"


@dataclass
class ControllerState:
    votes_this_hour: int = 0
    votes_today: int = 0
    hour_bucket: str = ""
    day_bucket: str = ""
    total_votes_cast: int = 0
    started_at: datetime | None = None
    last_standings: Standings | None = None
    last_lead_pct: float | None = None
    consecutive_in_band: int = 0

    def touch_buckets(self, now: datetime) -> None:
        hour = now.strftime("%Y%m%d-%H")
        day = now.strftime("%Y%m%d")
        if hour != self.hour_bucket:
            self.hour_bucket = hour
            self.votes_this_hour = 0
        if day != self.day_bucket:
            self.day_bucket = day
            self.votes_today = 0

    def record_vote(self, now: datetime) -> None:
        self.touch_buckets(now)
        self.votes_this_hour += 1
        self.votes_today += 1
        self.total_votes_cast += 1


@dataclass
class Decision:
    action: Action
    reason: str
    wait_sec: float = 0.0


def _parse_deadline(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _random_delay(range_sec: list[int | float]) -> float:
    lo, hi = float(range_sec[0]), float(range_sec[1])
    return random.uniform(lo, hi)


def decide(
    cfg: dict[str, Any],
    state: ControllerState,
    standings: Standings,
    now: datetime | None = None,
) -> Decision:
    now = now or datetime.now(timezone.utc)
    state.touch_buckets(now)

    deadline = _parse_deadline(cfg["poll"]["deadline"])
    if now >= deadline.astimezone(timezone.utc):
        return Decision(Action.STOP, "Poll deadline reached")

    target_name = cfg["target"]["name_match"]
    min_lead = float(cfg["margin"]["min_lead_pct"])
    max_lead = float(cfg["margin"]["max_lead_pct"])
    timing = cfg["timing"]

    lead_pct, target = compute_lead_pct(standings, target_name)
    state.last_standings = standings
    state.last_lead_pct = lead_pct

    if target is None:
        return Decision(
            Action.VOTE,
            f"Target '{target_name}' not found in results; voting anyway",
            wait_sec=0,
        )

    ordered = standings.sorted_by_rank()
    rank = next((i + 1 for i, c in enumerate(ordered) if c.name == target.name), None)
    runner = standings.runner_up()
    runner_pct = runner.percent if runner else 0.0

    # Rate limits
    if state.votes_this_hour >= int(timing["max_votes_per_hour"]):
        wait = _random_delay(timing["monitor_delay_sec"])
        return Decision(
            Action.PAUSE,
            f"Hourly cap reached ({state.votes_this_hour}/{timing['max_votes_per_hour']})",
            wait_sec=wait,
        )
    if state.votes_today >= int(timing["max_votes_per_day"]):
        wait = _random_delay(timing["monitor_delay_sec"])
        return Decision(
            Action.PAUSE,
            f"Daily cap reached ({state.votes_today}/{timing['max_votes_per_day']})",
            wait_sec=wait,
        )

    # Ramp: reduce aggression in first N hours
    ramp_hours = float(timing.get("ramp_hours", 48))
    if state.started_at is None:
        state.started_at = now
    elapsed_h = (now - state.started_at).total_seconds() / 3600.0
    ramp_factor = min(1.0, elapsed_h / ramp_hours) if ramp_hours > 0 else 1.0

    if rank != 1:
        # Not leading — vote aggressively (ramp_hours: 0 skips early throttle)
        if ramp_hours > 0 and ramp_factor < 0.25 and state.votes_this_hour >= 2:
            wait = _random_delay(timing["vote_delay_sec"])
            return Decision(
                Action.PAUSE,
                f"Ramp phase ({elapsed_h:.1f}h/{ramp_hours}h): holding pace while trailing",
                wait_sec=wait * 1.5,
            )
        wait = _random_delay(timing["vote_delay_sec"]) * (1.5 - 0.5 * ramp_factor)
        return Decision(
            Action.VOTE,
            f"Not #1 (rank {rank}, {target.percent:.1f}% vs leader {runner_pct:.1f}%)",
            wait_sec=max(60, wait),
        )

    # Target is #1
    if lead_pct is None:
        wait = _random_delay(timing["monitor_delay_sec"])
        return Decision(Action.MONITOR, "Only one candidate visible", wait_sec=wait)

    if lead_pct > max_lead:
        state.consecutive_in_band = 0
        # Long blind pause: next cycle will vote once to re-check (percentages require a vote).
        # Spreading checks out limits how many extra votes land while over the cap.
        wait = _random_delay(timing["monitor_delay_sec"]) * 8
        return Decision(
            Action.PAUSE,
            f"Lead {lead_pct:.1f}% > max {max_lead}% — long pause before re-check",
            wait_sec=max(wait, 7200),
        )

    if min_lead <= lead_pct <= max_lead:
        state.consecutive_in_band += 1
        wait = _random_delay(timing["monitor_delay_sec"])
        return Decision(
            Action.MONITOR,
            f"In band: lead {lead_pct:.1f}% (target {min_lead}-{max_lead}%)",
            wait_sec=wait,
        )

    if lead_pct < min_lead:
        state.consecutive_in_band = 0
        wait = _random_delay(timing["vote_delay_sec"]) * (1.3 - 0.3 * ramp_factor)
        return Decision(
            Action.VOTE,
            f"Lead {lead_pct:.1f}% < min {min_lead}% — adding one vote",
            wait_sec=max(90, wait),
        )

    wait = _random_delay(timing["monitor_delay_sec"])
    return Decision(Action.MONITOR, f"Lead {lead_pct:.1f}% — holding", wait_sec=wait)
