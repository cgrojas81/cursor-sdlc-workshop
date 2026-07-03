"""Poll page interaction: popups, scroll, iframe, vote flow."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from playwright.async_api import Frame, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

POPUP_CLOSE_SELECTORS = [
    'button[aria-label="Close"]',
    'button[aria-label="close"]',
    'button:has-text("Close")',
    'button:has-text("No Thanks")',
    'button:has-text("No thanks")',
    'button:has-text("Not Now")',
    'button:has-text("Not now")',
    'button:has-text("Accept")',
    'button:has-text("I Accept")',
    'button:has-text("Agree")',
    'button:has-text("Continue")',
    'button:has-text("Dismiss")',
    '[class*="close" i]',
    '[data-testid="close-button"]',
    '[data-test-id="close-button"]',
]


async def close_popups(page: Page, rounds: int = 3) -> None:
    """Dismiss common modals and overlays; repeat because some stack."""
    for _ in range(rounds):
        closed_any = False
        for selector in POPUP_CLOSE_SELECTORS:
            try:
                locators = page.locator(selector)
                count = await locators.count()
                for i in range(min(count, 5)):
                    btn = locators.nth(i)
                    if await btn.is_visible():
                        await btn.click(timeout=1500)
                        closed_any = True
                        await asyncio.sleep(0.4)
            except Exception:
                continue
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        if not closed_any:
            break
        await asyncio.sleep(0.5)


async def scroll_to_poll(page: Page) -> None:
    """Scroll down so the lazy-loaded poll iframe enters the viewport."""
    for _ in range(6):
        await page.mouse.wheel(0, 900)
        await asyncio.sleep(0.6)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.55)")


async def find_poll_frame(page: Page, timeout_ms: int) -> Frame:
    """Return the iframe that contains poll vote UI."""
    deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000)
    last_error: Exception | None = None

    while asyncio.get_event_loop().time() < deadline:
        await close_popups(page, rounds=1)
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                body = await frame.locator("body").inner_text(timeout=2000)
            except Exception:
                continue
            lower = body.lower()
            if "vote" in lower and (
                "softball" in lower
                or "class of 2029" in lower
                or "hadgimallis" in lower
                or "return to the poll" in lower
            ):
                return frame
            # Radio buttons are a strong signal even before names load
            radios = frame.locator('input[type="radio"]')
            if await radios.count() >= 3:
                return frame
        await asyncio.sleep(2)

    raise PlaywrightTimeout(f"Poll iframe not found within {timeout_ms}ms") from last_error


async def wait_for_poll_options(frame: Frame, timeout_ms: int) -> None:
    """Wait until candidate radio buttons are present."""
    await frame.locator('input[type="radio"]').first.wait_for(
        state="visible", timeout=timeout_ms
    )


async def select_target_candidate(
    frame: Frame, name_match: str, school_match: str | None = None
) -> None:
    """Click the radio button for the target player."""
    labels = frame.locator("label")
    count = await labels.count()
    target_idx: int | None = None

    for i in range(count):
        text = (await labels.nth(i).inner_text()).strip()
        normalized = re.sub(r"\s+", " ", text)
        if name_match.lower() in normalized.lower():
            if school_match and school_match.lower() not in normalized.lower():
                continue
            target_idx = i
            break

    if target_idx is None:
        # Fallback: match any element containing the name near a radio input
        candidate = frame.locator(f"text=/{re.escape(name_match)}/i").first
        await candidate.wait_for(state="visible", timeout=10000)
        await candidate.click()
        return

    label = labels.nth(target_idx)
    await label.scroll_into_view_if_needed()
    await label.click()


async def click_vote_button(frame: Frame) -> None:
    """Submit the poll vote."""
    vote_btn = frame.get_by_role("button", name=re.compile(r"vote", re.I))
    if await vote_btn.count() == 0:
        vote_btn = frame.locator('button:has-text("Vote"), input[type="submit"][value*="Vote" i]')
    await vote_btn.first.scroll_into_view_if_needed()
    await vote_btn.first.click()


async def return_to_poll(frame: Frame) -> None:
    """Click 'Return to the poll!' link on the results screen."""
    link = frame.get_by_role("link", name=re.compile(r"return to the poll", re.I))
    if await link.count() == 0:
        link = frame.locator("text=/return to the poll/i")
    await link.first.scroll_into_view_if_needed()
    await link.first.click()
    await wait_for_poll_options(frame, timeout_ms=20000)


async def navigate_and_prepare_poll(page: Page, poll_url: str, cfg: dict[str, Any]) -> Frame:
    """Full page load path: URL -> popups -> scroll -> iframe."""
    timing = cfg["timing"]
    await page.goto(poll_url, wait_until="domcontentloaded", timeout=90000)
    await asyncio.sleep(timing.get("page_settle_sec", 3))
    await close_popups(page)
    await scroll_to_poll(page)
    await asyncio.sleep(2)
    frame = await find_poll_frame(page, timeout_ms=timing["iframe_load_timeout_sec"] * 1000)
    await wait_for_poll_options(frame, timeout_ms=timing["poll_load_timeout_sec"] * 1000)
    return frame
