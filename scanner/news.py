"""News & catalyst engine: RSS, APIs, NLP classification."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from typing import Any

import anthropic
import feedparser

from config.sectors import SECTOR_ETFS
from config.settings import llm_config, news_config
from data.models import Catalyst
from data.store import get_db, headline_exists, insert_catalyst, query_catalysts

log = logging.getLogger(__name__)

VALID_CATALYST_TYPES = frozenset(
    {
        "earnings",
        "fda",
        "upgrade",
        "downgrade",
        "macro",
        "contract",
        "m_and_a",
        "geopolitical",
        "product",
        "other",
    }
)

_SECTOR_NAMES_LOWER = {s.lower(): s for s in SECTOR_ETFS}

_CLASSIFY_PROMPT = """\
You are a financial news analyst. Given the headline and summary below, return ONLY a JSON object with these fields:
- "sentiment": float from -1.0 (very bearish) to +1.0 (very bullish)
- "magnitude": int 1-5 (1=noise, 5=market-moving)
- "catalyst_type": one of earnings|fda|upgrade|downgrade|macro|contract|m_and_a|geopolitical|product|other
- "symbols": list of stock ticker symbols mentioned (uppercase, e.g. ["AAPL","MSFT"])
- "sectors": list of affected sectors from: Technology, Financials, Energy, Health Care, Industrials, Communication Services, Consumer Discretionary, Consumer Staples, Utilities, Real Estate, Materials

Headline: {headline}
Summary: {summary}

Respond with ONLY valid JSON, no markdown fences or extra text."""


class CatalystEngine:
    """RSS feed ingestion with LLM-based sentiment/catalyst classification."""

    def __init__(
        self,
        api_key: str | None = None,
        llm_model: str | None = None,
        feed_urls: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self._api_key = api_key or llm_config.api_key
        self._llm_model = llm_model or news_config.llm_model
        self._feed_urls = feed_urls or news_config.feed_urls
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def poll_feeds(self) -> list[Catalyst]:
        """Parse all RSS feeds, classify new entries, persist and return them."""
        new_catalysts: list[Catalyst] = []
        async with get_db() as conn:
            for url in self._feed_urls:
                try:
                    entries = self._parse_feed(url)
                except Exception:
                    log.exception("Failed to parse feed %s", url)
                    continue

                dupes = 0
                for entry in entries:
                    headline = entry["headline"]
                    if await headline_exists(conn, headline):
                        dupes += 1
                        continue

                    classification = await self.classify_catalyst(
                        headline, entry.get("summary", "")
                    )

                    symbol = (classification.get("symbols") or [None])[0]
                    sectors = classification.get("sectors") or []
                    sector = self._resolve_sector(sectors[0]) if sectors else None

                    catalyst = Catalyst(
                        timestamp=entry["timestamp"],
                        symbol=symbol,
                        sector=sector,
                        headline=headline,
                        source=entry["source"],
                        sentiment=classification.get("sentiment"),
                        magnitude=classification.get("magnitude"),
                        catalyst_type=classification.get("catalyst_type"),
                        raw_text=entry.get("summary"),
                        llm_analysis=json.dumps(classification),
                    )

                    row_id = await self.save_catalyst(catalyst, conn=conn)
                    catalyst.id = row_id
                    new_catalysts.append(catalyst)

                new_from_feed = len(entries) - dupes
                if entries:
                    log.info(
                        "Feed %s: %d entries, %d new, %d already seen",
                        url.split("/")[2], len(entries), new_from_feed, dupes,
                    )
                else:
                    log.warning("Feed %s: 0 entries returned", url.split("/")[2])
        return new_catalysts

    async def classify_catalyst(self, headline: str, summary: str) -> dict[str, Any]:
        """Call Claude to classify headline+summary. Returns dict with sentiment etc."""
        if not self._api_key:
            return {
                "sentiment": 0.0,
                "magnitude": 1,
                "catalyst_type": "other",
                "symbols": [],
                "sectors": [],
            }
        try:
            client = self._get_client()
            resp = await client.messages.create(
                model=self._llm_model,
                max_tokens=256,
                messages=[
                    {
                        "role": "user",
                        "content": _CLASSIFY_PROMPT.format(
                            headline=headline, summary=summary
                        ),
                    }
                ],
            )
            raw = resp.content[0].text.strip()
            # Strip markdown fences if the model wrapped the JSON
            fence = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
            if fence:
                raw = fence.group(1).strip()
            if not raw:
                log.warning("LLM returned empty response for headline: %s", headline[:80])
                raise ValueError("empty LLM response")
            data = json.loads(raw)
            return self._validate_classification(data)
        except Exception:
            log.exception("LLM classification failed, using neutral defaults")
            return {
                "sentiment": 0.0,
                "magnitude": 1,
                "catalyst_type": "other",
                "symbols": [],
                "sectors": [],
            }

    async def save_catalyst(
        self, catalyst: Catalyst, *, conn=None
    ) -> int:
        """Persist a Catalyst to the DB. Returns row id."""
        if conn is None:
            async with get_db() as conn:
                return await insert_catalyst(
                    conn,
                    timestamp=catalyst.timestamp,
                    symbol=catalyst.symbol,
                    sector=catalyst.sector,
                    headline=catalyst.headline,
                    source=catalyst.source,
                    sentiment=catalyst.sentiment,
                    magnitude=catalyst.magnitude,
                    catalyst_type=catalyst.catalyst_type,
                    raw_text=catalyst.raw_text,
                    llm_analysis=catalyst.llm_analysis,
                )
        return await insert_catalyst(
            conn,
            timestamp=catalyst.timestamp,
            symbol=catalyst.symbol,
            sector=catalyst.sector,
            headline=catalyst.headline,
            source=catalyst.source,
            sentiment=catalyst.sentiment,
            magnitude=catalyst.magnitude,
            catalyst_type=catalyst.catalyst_type,
            raw_text=catalyst.raw_text,
            llm_analysis=catalyst.llm_analysis,
        )

    async def get_recent_catalysts(
        self, symbol: str | None = None, hours: int = 24
    ) -> list[Catalyst]:
        """Return catalysts from the last *hours* hours, optionally for a symbol."""
        since = int(time.time()) - hours * 3600
        async with get_db() as conn:
            rows = await query_catalysts(conn, since_ts=since, symbol=symbol)
        return [Catalyst(**r) for r in rows]

    def get_catalyst_strength(self, catalysts: list[Catalyst], now_ts: int | None = None) -> float:
        """Aggregate catalysts into a single CSS score with exponential time-decay.

        Returns 0.0 when no catalysts are present.
        """
        if not catalysts:
            return 0.0
        now = now_ts or int(time.time())
        total = 0.0
        for c in catalysts:
            age_hours = max((now - c.timestamp) / 3600, 0)
            decay = math.exp(-0.1 * age_hours)  # ~50% weight at 7h
            sent = c.sentiment or 0.0
            mag = c.magnitude or 1
            total += sent * mag * decay
        return total

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_feed(url: str) -> list[dict[str, Any]]:
        """Parse an RSS feed URL and return normalised entries."""
        feed = feedparser.parse(url)
        entries: list[dict[str, Any]] = []
        for e in feed.entries:
            published = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
            ts = int(time.mktime(published)) if published else int(time.time())
            entries.append(
                {
                    "headline": getattr(e, "title", "").strip(),
                    "summary": (getattr(e, "summary", "") or getattr(e, "description", "") or "").strip(),
                    "source": getattr(feed.feed, "title", None) or (feed.feed.get("title", url) if isinstance(feed.feed, dict) else url),
                    "timestamp": ts,
                }
            )
        return entries

    @staticmethod
    def _validate_classification(data: dict) -> dict[str, Any]:
        """Clamp/normalise raw LLM output."""
        sentiment = data.get("sentiment", 0.0)
        if not isinstance(sentiment, (int, float)):
            sentiment = 0.0
        sentiment = max(-1.0, min(1.0, float(sentiment)))

        magnitude = data.get("magnitude", 1)
        if not isinstance(magnitude, int):
            try:
                magnitude = int(magnitude)
            except (ValueError, TypeError):
                magnitude = 1
        magnitude = max(1, min(5, magnitude))

        cat = data.get("catalyst_type", "other")
        if cat not in VALID_CATALYST_TYPES:
            cat = "other"

        symbols = data.get("symbols") or []
        if not isinstance(symbols, list):
            symbols = []
        symbols = [s.upper() for s in symbols if isinstance(s, str)]

        sectors = data.get("sectors") or []
        if not isinstance(sectors, list):
            sectors = []

        return {
            "sentiment": sentiment,
            "magnitude": magnitude,
            "catalyst_type": cat,
            "symbols": symbols,
            "sectors": sectors,
        }

    @staticmethod
    def _resolve_sector(name: str) -> str | None:
        """Map a sector name to canonical form if it matches SECTOR_ETFS keys."""
        if name in SECTOR_ETFS:
            return name
        return _SECTOR_NAMES_LOWER.get(name.lower())
