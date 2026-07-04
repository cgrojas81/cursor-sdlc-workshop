"""Poll vote automation — main loop and single-cycle test runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .browser import cast_vote, navigate_and_prepare_poll
from .controller import Action, ControllerState, Decision, decide
from .monitor import Standings, compute_lead_pct, parse_standings_from_dom
from .scheduler import (
    should_open_browser,
    schedule_next_cycle,
    write_heartbeat,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
"""

CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]


def load_config(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def setup_logging(log_file: Path | None) -> None:
    class FlushingFileHandler(logging.FileHandler):
        def emit(self, record: logging.LogRecord) -> None:
            super().emit(record)
            self.flush()

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(FlushingFileHandler(log_file))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )


def load_state(path: Path) -> ControllerState:
    if not path.exists():
        return ControllerState()
    data = json.loads(path.read_text())
    state = ControllerState()
    state.votes_this_hour = data.get("votes_this_hour", 0)
    state.votes_today = data.get("votes_today", 0)
    state.hour_bucket = data.get("hour_bucket", "")
    state.day_bucket = data.get("day_bucket", "")
    state.total_votes_cast = data.get("total_votes_cast", 0)
    state.consecutive_in_band = data.get("consecutive_in_band", 0)
    if data.get("started_at"):
        state.started_at = datetime.fromisoformat(data["started_at"])
    return state


def save_state(path: Path, state: ControllerState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "votes_this_hour": state.votes_this_hour,
        "votes_today": state.votes_today,
        "hour_bucket": state.hour_bucket,
        "day_bucket": state.day_bucket,
        "total_votes_cast": state.total_votes_cast,
        "consecutive_in_band": state.consecutive_in_band,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "last_lead_pct": state.last_lead_pct,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2))


def save_standings_snapshot(path: Path, standings: Standings, target_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lead, target = compute_lead_pct(standings, target_name)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "captured_via": standings.captured_via,
        "lead_pct": lead,
        "target": target.name if target else None,
        "candidates": [
            {"rank": c.rank, "name": c.name, "percent": c.percent, "raw": c.raw_line}
            for c in standings.sorted_by_rank()
        ],
    }
    history = path.with_name("standings_history.jsonl")
    with history.open("a") as f:
        f.write(json.dumps(payload) + "\n")
    path.write_text(json.dumps(payload, indent=2))


class ProxyRotator:
    def __init__(self, cfg: dict[str, Any]) -> None:
        proxies: list[str] = []
        env_list = os.environ.get("PROXY_LIST", "")
        if env_list:
            proxies.extend(p.strip() for p in env_list.split(",") if p.strip())
        proxy_file = ROOT / "proxies.txt"
        if proxy_file.exists():
            proxies.extend(
                line.strip()
                for line in proxy_file.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            )
        single = cfg.get("browser", {}).get("proxy")
        if single:
            proxies.append(single)
        self.proxies = proxies
        self._index = 0

    def next(self) -> str | None:
        if not self.proxies:
            return None
        proxy = self.proxies[self._index % len(self.proxies)]
        self._index += 1
        return proxy


async def create_context(browser: Browser, cfg: dict[str, Any], proxy: str | None) -> BrowserContext:
    browser_cfg = cfg.get("browser", {})
    kwargs: dict[str, Any] = {
        "viewport": {
            "width": random.choice([1366, 1440, 1536, 1920]),
            "height": random.choice([768, 864, 900, 1080]),
        },
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "locale": "en-US",
        "timezone_id": "America/New_York",
    }
    storage = browser_cfg.get("storage_state")
    if storage:
        kwargs["storage_state"] = storage
    if proxy:
        kwargs["proxy"] = {"server": proxy}
    context = await browser.new_context(**kwargs)
    await context.add_init_script(STEALTH_INIT_SCRIPT)
    return context


async def launch_browser(playwright, cfg: dict[str, Any]):
    browser_cfg = cfg.get("browser", {})
    headless = bool(browser_cfg.get("headless", False))
    channel = browser_cfg.get("channel")
    launch_kwargs: dict[str, Any] = {
        "headless": headless,
        "slow_mo": int(browser_cfg.get("slow_mo_ms", 0)),
        "args": CHROMIUM_ARGS,
        "ignore_default_args": ["--enable-automation"],
    }
    if channel:
        launch_kwargs["channel"] = channel
    return await playwright.chromium.launch(**launch_kwargs)


async def submit_vote(
    page: Page,
    cfg: dict[str, Any],
    *,
    debug_dir: Path | None = None,
) -> Standings:
    frame = await navigate_and_prepare_poll(
        page, cfg["poll"]["url"], cfg, debug_dir=debug_dir
    )
    await cast_vote(frame, cfg["target"]["name_match"], cfg["target"].get("school_match"))
    return await parse_standings_from_dom(frame)


def log_standings(standings: Standings, target_name: str) -> None:
    lead, target = compute_lead_pct(standings, target_name)
    logger.info("--- Standings (%s) ---", standings.captured_via)
    for c in standings.sorted_by_rank()[:10]:
        marker = " <-- TARGET" if target and c.name == target.name else ""
        logger.info("  #%s  %.1f%%  %s%s", c.rank, c.percent, c.name, marker)
    if lead is not None:
        logger.info("Lead over #2: %+.1f pts", lead)


async def run_one_cycle(
    cfg: dict[str, Any],
    state: ControllerState,
    state_path: Path,
    scheduler_path: Path,
    *,
    force_vote: bool = False,
    debug: bool = False,
    proxy: str | None = None,
) -> Decision | None:
    """Launch browser, vote once, tear down completely. Returns controller decision."""
    target_name = cfg["target"]["name_match"]
    debug_dir = (ROOT / "data" / "debug") if debug else None
    heartbeat = ROOT / "data" / "heartbeat.json"

    if not force_vote:
        ok, reason = should_open_browser(cfg, state, scheduler_path)
        if not ok:
            logger.info("Skipping browser cycle: %s", reason)
            write_heartbeat(heartbeat, ok=True, detail=f"skipped: {reason}")
            return None

    logger.info("=== Cycle start (total_votes=%s) ===", state.total_votes_cast)

    async with async_playwright() as p:
        browser = await launch_browser(p, cfg)
        context = await create_context(browser, cfg, proxy)
        page = await context.new_page()
        try:
            standings = await submit_vote(page, cfg, debug_dir=debug_dir)
            save_standings_snapshot(
                ROOT / "data" / "latest_standings.json", standings, target_name
            )
            log_standings(standings, target_name)

            state.record_vote(datetime.now(timezone.utc))
            save_state(state_path, state)

            decision = decide(cfg, state, standings)
            if force_vote:
                decision = Decision(Action.VOTE, "Single test vote completed", wait_sec=0)

            logger.info("Decision: %s — %s", decision.action.value, decision.reason)
            wait = decision.wait_sec or random.uniform(120, 300)
            schedule_next_cycle(
                scheduler_path,
                wait,
                decision.action.value,
                decision.reason,
            )
            write_heartbeat(heartbeat, ok=True, detail=decision.reason)
            return decision
        except Exception as exc:
            logger.exception("Cycle failed: %s", exc)
            save_state(state_path, state)
            wait = random.uniform(180, 420)
            schedule_next_cycle(
                scheduler_path,
                wait,
                "error",
                str(exc)[:200],
            )
            write_heartbeat(heartbeat, ok=False, detail=str(exc)[:200])
            if debug_dir:
                try:
                    await page.screenshot(path=str(debug_dir / "cycle_error.png"), full_page=True)
                except Exception:
                    pass
            raise
        finally:
            await context.close()
            await browser.close()


async def run_session(
    cfg: dict[str, Any],
    state: ControllerState,
    state_path: Path,
    *,
    once: bool = False,
    force_vote: bool = False,
    debug: bool = False,
) -> None:
    rotator = ProxyRotator(cfg)
    scheduler_path = ROOT / "data" / "scheduler.json"
    browser_cfg = cfg.get("browser", {})
    headless = bool(browser_cfg.get("headless", False))
    logger.info(
        "Session starting (headless=%s, once=%s, prior_votes=%s)",
        headless,
        once,
        state.total_votes_cast,
    )

    while True:
        proxy = rotator.next()
        if proxy:
            logger.info("Using proxy: %s", proxy.split("@")[-1])

        try:
            decision = await run_one_cycle(
                cfg,
                state,
                state_path,
                scheduler_path,
                force_vote=force_vote,
                debug=debug,
                proxy=proxy,
            )
        except Exception:
            if once or force_vote:
                raise
            decision = None

        if force_vote or once:
            break

        if decision and decision.action == Action.STOP:
            logger.info("Stopping: deadline reached")
            break

        sched = json.loads(scheduler_path.read_text()) if scheduler_path.exists() else {}
        next_after = sched.get("next_cycle_after")
        if next_after:
            wake = datetime.fromisoformat(next_after)
            if wake.tzinfo is None:
                wake = wake.replace(tzinfo=timezone.utc)
            wait = max(30.0, (wake - datetime.now(timezone.utc)).total_seconds())
        else:
            wait = random.uniform(120, 300)

        logger.info("Sleeping %.0f sec until next cycle", wait)
        await asyncio.sleep(wait)


def main() -> None:
    parser = argparse.ArgumentParser(description="PhillyBurbs poll automation")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--once", action="store_true", help="One cycle then exit")
    parser.add_argument(
        "--force-vote",
        action="store_true",
        help="Cast a single test vote and print standings",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Save screenshot + iframe list on failure (data/debug/)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    log_path = ROOT / cfg.get("logging", {}).get("log_file", "data/run.log")
    setup_logging(log_path)

    logger.info("=== Poll bot process start PID=%s ===", os.getpid())
    logger.info("Using config: %s", args.config.resolve())

    state_path = ROOT / cfg.get("logging", {}).get("state_file", "data/state.json")
    state = load_state(state_path)

    asyncio.run(
        run_session(
            cfg,
            state,
            state_path,
            once=args.once or args.force_vote,
            force_vote=args.force_vote,
            debug=args.debug,
        )
    )


if __name__ == "__main__":
    main()
