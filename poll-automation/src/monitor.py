"""Parse standings from poll results inside the iframe."""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.async_api import Frame

from .browser import get_frame_body_text


@dataclass
class CandidateStanding:
    name: str
    rank: int
    percent: float
    raw_line: str


@dataclass
class Standings:
    candidates: list[CandidateStanding]
    captured_via: str  # "dom" or "network"

    def sorted_by_rank(self) -> list[CandidateStanding]:
        return sorted(self.candidates, key=lambda c: c.rank)

    def leader(self) -> CandidateStanding | None:
        ordered = self.sorted_by_rank()
        return ordered[0] if ordered else None

    def runner_up(self) -> CandidateStanding | None:
        ordered = self.sorted_by_rank()
        return ordered[1] if len(ordered) > 1 else None


PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
RANK_RE = re.compile(r"(?:#\s*|place\s*|rank\s*)?(\d+)", re.I)
# "1. Name 12.3%" or "Name - 12.3%"
INLINE_RESULT_RE = re.compile(
    r"^\s*(?:#?\s*)?(\d+)[.)]?\s*(.+?)\s+(\d+(?:\.\d+)?)\s*%\s*$",
    re.I,
)


def _parse_percent(text: str) -> float | None:
    match = PCT_RE.search(text)
    return float(match.group(1)) if match else None


def _parse_rank(text: str, fallback: int) -> int:
    match = RANK_RE.search(text)
    if match:
        return int(match.group(1))
    return fallback


def _clean_name(text: str) -> str:
    name = PCT_RE.sub("", text)
    name = RANK_RE.sub("", name)
    name = re.sub(r"[^\w\s\-'.]", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -.")
    return name


def _parse_from_body_text(body: str) -> list[CandidateStanding]:
    """Parse Gannett / tangstatic-style results from plain text."""
    candidates: list[CandidateStanding] = []
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in body.splitlines()]
    lines = [ln for ln in lines if ln]

    skip_fragments = (
        "return to the poll",
        "who is the",
        "cast your vote",
        "vote button",
        "powered by",
    )

    for line in lines:
        lower = line.lower()
        if any(s in lower for s in skip_fragments):
            continue

        inline = INLINE_RESULT_RE.match(line)
        if inline:
            rank = int(inline.group(1))
            name = _clean_name(inline.group(2))
            pct = float(inline.group(3))
            if len(name) >= 3:
                candidates.append(
                    CandidateStanding(name=name, rank=rank, percent=pct, raw_line=line)
                )
            continue

        pct = _parse_percent(line)
        if pct is None:
            continue

        name = _clean_name(line)
        if len(name) >= 3:
            candidates.append(
                CandidateStanding(
                    name=name,
                    rank=len(candidates) + 1,
                    percent=pct,
                    raw_line=line,
                )
            )
            continue

        # Percent on its own line — name likely on previous line
        idx = lines.index(line)
        if idx > 0:
            prev = lines[idx - 1]
            if _parse_percent(prev) is None and not any(s in prev.lower() for s in skip_fragments):
                name = _clean_name(prev)
                if len(name) >= 3:
                    candidates.append(
                        CandidateStanding(
                            name=name,
                            rank=len(candidates) + 1,
                            percent=pct,
                            raw_line=f"{prev} | {line}",
                        )
                    )

    return candidates


async def _parse_from_percent_elements(frame: Frame) -> list[CandidateStanding]:
    """Find elements containing % and walk up for candidate name."""
    candidates: list[CandidateStanding] = []
    loc = frame.locator("text=/%/")
    count = await loc.count()
    for i in range(min(count, 40)):
        try:
            el = loc.nth(i)
            text = re.sub(r"\s+", " ", (await el.inner_text()).strip())
        except Exception:
            continue
        if "return to the poll" in text.lower():
            continue
        pct = _parse_percent(text)
        if pct is None:
            continue

        name = _clean_name(text)
        if len(name) < 3:
            try:
                parent = el.locator("xpath=ancestor::*[self::li or self::tr or self::div][1]")
                if await parent.count() > 0:
                    parent_text = re.sub(
                        r"\s+", " ", (await parent.first.inner_text()).strip()
                    )
                    name = _clean_name(parent_text)
            except Exception:
                pass

        if len(name) >= 3:
            candidates.append(
                CandidateStanding(
                    name=name,
                    rank=len(candidates) + 1,
                    percent=pct,
                    raw_line=text,
                )
            )
    return candidates


async def parse_standings_from_dom(frame: Frame) -> Standings:
    """
    Extract name / rank / percent from the post-vote results view.

    Handles Gannett tangstatic embeds and generic poll DOM layouts.
    """
    candidates: list[CandidateStanding] = []

    row_selectors = [
        "li",
        "tr",
        '[class*="result" i]',
        '[class*="option" i]',
        '[class*="poll" i]',
        '[class*="stand" i]',
        "p",
    ]

    seen_lines: set[str] = set()
    for selector in row_selectors:
        loc = frame.locator(selector)
        count = await loc.count()
        for i in range(min(count, 100)):
            try:
                text = re.sub(r"\s+", " ", (await loc.nth(i).inner_text()).strip())
            except Exception:
                continue
            if not text or len(text) < 4 or text in seen_lines:
                continue
            pct = _parse_percent(text)
            if pct is None:
                continue
            if "return to the poll" in text.lower():
                continue
            seen_lines.add(text)
            rank = _parse_rank(text, len(candidates) + 1)
            name = _clean_name(text)
            if len(name) < 3:
                continue
            candidates.append(
                CandidateStanding(name=name, rank=rank, percent=pct, raw_line=text)
            )

    if len(candidates) < 2:
        candidates.extend(await _parse_from_percent_elements(frame))

    if len(candidates) < 2:
        body = await get_frame_body_text(frame)
        candidates.extend(_parse_from_body_text(body))

    # Deduplicate by similar name, keep highest percent
    deduped: dict[str, CandidateStanding] = {}
    for c in candidates:
        key = c.name.lower()[:50]
        if key not in deduped or c.percent > deduped[key].percent:
            deduped[key] = c

    final = list(deduped.values())
    if not final:
        body = await get_frame_body_text(frame)
        if body.count("%") < 1:
            raise ValueError(
                "Still on the voting form (no percentages visible). "
                "Vote may not have submitted. "
                f"Body preview: {body[:500]!r}"
            )
        raise ValueError(
            "Could not parse standings from results DOM. "
            f"Body preview: {body[:800]!r}"
        )

    final.sort(key=lambda c: c.percent, reverse=True)
    for idx, c in enumerate(final, start=1):
        c.rank = idx

    return Standings(candidates=final, captured_via="dom")


def find_target(candidates: list[CandidateStanding], name_match: str) -> CandidateStanding | None:
    needle = name_match.lower()
    for c in candidates:
        if needle in c.name.lower():
            return c
    return None


def compute_lead_pct(standings: Standings, target_name: str) -> tuple[float | None, CandidateStanding | None]:
    """Return (lead percentage points over #2, target standing)."""
    target = find_target(standings.candidates, target_name)
    if target is None:
        return None, None

    ordered = standings.sorted_by_rank()
    if not ordered or ordered[0].name != target.name:
        leader = ordered[0] if ordered else None
        if leader:
            return target.percent - leader.percent, target
        return None, target

    runner = standings.runner_up()
    if runner is None:
        return None, target
    return target.percent - runner.percent, target
