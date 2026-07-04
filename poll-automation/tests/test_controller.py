"""Unit tests for controller pacing logic."""

from datetime import datetime, timezone

from src.controller import Action, ControllerState, decide
from src.monitor import CandidateStanding, Standings


def _standings(rows: list[tuple[str, float]]) -> Standings:
    candidates = [
        CandidateStanding(name=name, rank=i + 1, percent=pct, raw_line=f"{name} {pct}%")
        for i, (name, pct) in enumerate(rows)
    ]
    return Standings(candidates=candidates, captured_via="test")


def _cfg() -> dict:
    return {
        "poll": {"deadline": "2026-07-07T20:00:00-04:00"},
        "target": {"name_match": "Markella Hadgimallis"},
        "margin": {"min_lead_pct": 2.0, "max_lead_pct": 3.0},
        "timing": {
            "vote_delay_sec": [60, 120],
            "monitor_delay_sec": [600, 900],
            "max_votes_per_hour": 4,
            "max_votes_per_day": 60,
            "ramp_hours": 48,
        },
    }


def test_votes_when_trailing():
    s = _standings([("Other Player", 40.0), ("Markella Hadgimallis", 35.0)])
    state = ControllerState(started_at=datetime(2026, 7, 3, tzinfo=timezone.utc))
    d = decide(_cfg(), state, s, now=datetime(2026, 7, 3, 17, tzinfo=timezone.utc))
    assert d.action == Action.VOTE


def test_pauses_when_lead_too_wide():
    s = _standings([("Markella Hadgimallis", 45.0), ("Other Player", 40.0)])
    state = ControllerState(started_at=datetime(2026, 7, 3, tzinfo=timezone.utc))
    d = decide(_cfg(), state, s, now=datetime(2026, 7, 3, 17, tzinfo=timezone.utc))
    assert d.action == Action.PAUSE
    assert d.wait_sec >= 7200


def test_monitors_in_band():
    s = _standings([("Markella Hadgimallis", 42.0), ("Other Player", 40.0)])
    state = ControllerState(started_at=datetime(2026, 7, 3, tzinfo=timezone.utc))
    d = decide(_cfg(), state, s, now=datetime(2026, 7, 3, 17, tzinfo=timezone.utc))
    assert d.action == Action.MONITOR


def test_votes_when_lead_below_min():
    s = _standings([("Markella Hadgimallis", 41.0), ("Other Player", 40.0)])
    state = ControllerState(started_at=datetime(2026, 7, 3, tzinfo=timezone.utc))
    d = decide(_cfg(), state, s, now=datetime(2026, 7, 3, 17, tzinfo=timezone.utc))
    assert d.action == Action.VOTE
