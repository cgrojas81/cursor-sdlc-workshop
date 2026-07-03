"""Poll page interaction: popups, scroll, iframe, vote flow."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from playwright.async_api import Frame, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

# PollUnit and other common newspaper poll hosts
POLL_IFRAME_SRC_HINTS = (
    "usatodaynetworkservice.com",
    "pollunit.com",
    "crowdsignal",
    "polldaddy",
    "survey",
    "poll.",
    "vote.",
    "embed",
    "tangstatic",
)

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
    'button:has-text("×")',
    '[class*="close" i]',
    '[data-testid="close-button"]',
    '[data-test-id="close-button"]',
]


async def close_popups(page: Page, rounds: int = 4) -> None:
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


async def scroll_to_poll_section(page: Page) -> None:
    """Scroll until the poll heading / embed is in view (lazy-load trigger)."""
    heading_patterns = [
        re.compile(r"top softball player", re.I),
        re.compile(r"class of 2029", re.I),
        re.compile(r"cast your vote", re.I),
        re.compile(r"who is the top", re.I),
    ]
    for pattern in heading_patterns:
        loc = page.get_by_role("heading", name=pattern)
        if await loc.count() > 0:
            try:
                await loc.first.scroll_into_view_if_needed(timeout=5000)
                await asyncio.sleep(1)
                return
            except Exception:
                pass

    # Fallback: incremental scroll (user said poll is below the fold)
    for step in range(10):
        await page.mouse.wheel(0, 700)
        await asyncio.sleep(0.5)
    await page.evaluate(
        """() => {
            const h = document.body.scrollHeight;
            window.scrollTo(0, h * 0.5);
            window.scrollTo(0, h * 0.65);
        }"""
    )


async def wait_for_poll_embed(page: Page, timeout_ms: int) -> None:
    """Wait for PollUnit div or poll iframe to appear in the DOM."""
    selectors = [
        '[data-pollunit-src]',
        'iframe[src*="pollunit"]',
        'iframe[src*="poll"]',
        "iframe",
    ]
    per_selector = timeout_ms // len(selectors)
    for selector in selectors:
        try:
            await page.locator(selector).first.wait_for(
                state="attached", timeout=per_selector
            )
            logger.info("Poll embed marker found: %s", selector)
            return
        except PlaywrightTimeout:
            continue
    raise PlaywrightTimeout(f"No poll embed marker in DOM within {timeout_ms}ms")


async def get_frame_body_text(frame: Frame) -> str:
    """Read frame text when multiple <body> nodes exist (nested mini-DOM)."""
    best = ""
    bodies = frame.locator("body")
    try:
        count = await bodies.count()
        for i in range(count):
            try:
                text = (await bodies.nth(i).inner_text()).strip()
                if len(text) > len(best):
                    best = text
            except Exception:
                continue
    except Exception:
        pass

    if best:
        return best

    try:
        return await frame.evaluate(
            """() => {
                const bodies = document.querySelectorAll('body');
                let merged = '';
                for (const b of bodies) {
                    const t = (b.innerText || '').trim();
                    if (t.length > merged.length) merged = t;
                }
                return merged || (document.body ? document.body.innerText : '');
            }"""
        )
    except Exception:
        return ""


async def _frame_has_poll_ui(frame: Frame) -> bool:
    """Return True if this frame looks like the voting UI."""
    try:
        radios = await frame.locator('input[type="radio"]').count()
        if radios >= 2:
            return True
    except Exception:
        pass

    try:
        body = await get_frame_body_text(frame)
    except Exception:
        return False

    if not body:
        return False

    lower = body.lower()
    signals = (
        "vote",
        "softball",
        "class of 2029",
        "hadgimallis",
        "markella",
        "return to the poll",
        "villa joseph",
        "phillyburbs",
        "top freshman",
    )
    hits = sum(1 for s in signals if s in lower)
    return hits >= 2 or ("vote" in lower and len(body) > 80)


async def _frame_from_iframe_locator(page: Page, index: int = 0) -> Frame | None:
    iframe = page.locator("iframe").nth(index)
    try:
        if await iframe.count() <= index:
            return None
        await iframe.scroll_into_view_if_needed(timeout=5000)
        handle = await iframe.element_handle()
        if not handle:
            return None
        frame = await handle.content_frame()
        return frame
    except Exception:
        return None


async def find_poll_frame(page: Page, timeout_ms: int, debug_dir: Path | None = None) -> Frame:
    """
    Return the iframe containing the poll.

    PollUnit injects an iframe after scroll; it may take 30+ seconds to populate.
    """
    deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000)
    attempt = 0

    while asyncio.get_event_loop().time() < deadline:
        attempt += 1
        await close_popups(page, rounds=1)

        # Prefer iframe whose src matches known poll hosts
        iframe_count = await page.locator("iframe").count()
        logger.info("Scanning %s iframe(s), attempt %s", iframe_count, attempt)

        for i in range(iframe_count):
            iframe_loc = page.locator("iframe").nth(i)
            try:
                src = (await iframe_loc.get_attribute("src")) or ""
            except Exception:
                src = ""
            src_lower = src.lower()
            is_poll_src = any(h in src_lower for h in POLL_IFRAME_SRC_HINTS)

            frame = await _frame_from_iframe_locator(page, i)
            if frame is None:
                continue

            if is_poll_src:
                logger.info("Matched poll iframe by src: %s", src[:120])
                try:
                    await wait_for_poll_options(frame, timeout_ms=30000)
                except PlaywrightTimeout:
                    if await _frame_has_poll_ui(frame):
                        return frame
                    continue
                return frame

            if await _frame_has_poll_ui(frame):
                logger.info("Matched poll iframe by content (src=%s)", src[:80])
                return frame

        # All frames including nested
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            if await _frame_has_poll_ui(frame):
                url = frame.url or ""
                logger.info("Matched child frame by content: %s", url[:120])
                return frame

        # Nudge lazy load
        if attempt % 3 == 0:
            await scroll_to_poll_section(page)
        await asyncio.sleep(2)

    if debug_dir:
        await save_debug_artifacts(page, debug_dir)

    raise PlaywrightTimeout(
        f"Poll iframe not found within {timeout_ms}ms. "
        "Try --debug and check data/debug/ screenshots."
    )


async def save_debug_artifacts(page: Page, debug_dir: Path) -> None:
    """Save screenshot and iframe list when detection fails."""
    debug_dir.mkdir(parents=True, exist_ok=True)
    shot = debug_dir / "page_failure.png"
    await page.screenshot(path=str(shot), full_page=True)
    logger.error("Saved debug screenshot: %s", shot)

    lines = ["iframe src list:"]
    count = await page.locator("iframe").count()
    for i in range(count):
        src = await page.locator("iframe").nth(i).get_attribute("src")
        lines.append(f"  [{i}] {src}")
    lines.append("frames:")
    for frame in page.frames:
        lines.append(f"  {frame.url}")
    report = debug_dir / "frames.txt"
    report.write_text("\n".join(lines))
    logger.error("Saved frame report: %s", report)


async def wait_for_poll_options(frame: Frame, timeout_ms: int) -> None:
    """Wait until candidate radio buttons or Vote button is present."""
    deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000)
    while asyncio.get_event_loop().time() < deadline:
        radios = await frame.locator('input[type="radio"]').count()
        if radios >= 2:
            await frame.locator('input[type="radio"]').first.wait_for(
                state="visible", timeout=5000
            )
            return
        vote_btn = await frame.get_by_role("button", name=re.compile(r"vote", re.I)).count()
        if vote_btn > 0:
            return
        await asyncio.sleep(1)
    raise PlaywrightTimeout(f"Poll options not visible within {timeout_ms}ms")


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
        candidate = frame.locator(f"text=/{re.escape(name_match)}/i").first
        await candidate.wait_for(state="visible", timeout=15000)
        await candidate.click()
        return

    label = labels.nth(target_idx)
    await label.scroll_into_view_if_needed()
    await label.click()


async def click_vote_button(frame: Frame) -> None:
    """Submit the poll vote."""
    vote_btn = frame.get_by_role("button", name=re.compile(r"^vote$", re.I))
    if await vote_btn.count() == 0:
        vote_btn = frame.get_by_role("button", name=re.compile(r"vote", re.I))
    if await vote_btn.count() == 0:
        vote_btn = frame.locator(
            'button:has-text("Vote"), input[type="submit"][value*="Vote" i]'
        )
    await vote_btn.first.scroll_into_view_if_needed()
    await vote_btn.first.click()


async def return_to_poll(frame: Frame) -> None:
    """Click 'Return to the poll!' link on the results screen."""
    link = frame.get_by_role("link", name=re.compile(r"return to the poll", re.I))
    if await link.count() == 0:
        link = frame.locator("text=/return to the poll/i")
    await link.first.scroll_into_view_if_needed()
    await link.first.click()
    await wait_for_poll_options(frame, timeout_ms=30000)


async def navigate_and_prepare_poll(
    page: Page, poll_url: str, cfg: dict[str, Any], debug_dir: Path | None = None
) -> Frame:
    """Full page load path: URL -> popups -> scroll -> iframe."""
    timing = cfg["timing"]
    await page.goto(poll_url, wait_until="domcontentloaded", timeout=120000)
    await asyncio.sleep(timing.get("page_settle_sec", 5))
    await close_popups(page)
    await scroll_to_poll_section(page)
    await asyncio.sleep(2)
    await close_popups(page)

    iframe_timeout = int(timing.get("iframe_load_timeout_sec", 90)) * 1000
    try:
        await wait_for_poll_embed(page, timeout_ms=min(iframe_timeout, 60000))
    except PlaywrightTimeout:
        logger.warning("Poll embed marker not found yet; continuing scan...")

    # User reported ~30s for poll content — scroll again and wait
    await scroll_to_poll_section(page)
    await asyncio.sleep(int(timing.get("post_scroll_wait_sec", 8)))

    frame = await find_poll_frame(page, timeout_ms=iframe_timeout, debug_dir=debug_dir)
    await wait_for_poll_options(
        frame, timeout_ms=int(timing.get("poll_load_timeout_sec", 60)) * 1000
    )
    return frame
