"""Tests for scanner.news — CatalystEngine."""

from __future__ import annotations

import json
import math
import os
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from data.models import Catalyst
from data.store import SCHEMA_STATEMENTS
from scanner.news import CatalystEngine, VALID_CATALYST_TYPES

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL_TEST", "postgresql://localhost/agentic_investment_test"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    return CatalystEngine(api_key="test-key", feed_urls=["http://fake.rss/feed"])


@pytest.fixture()
async def news_db():
    """Provide a PG connection with schema, wrapped so get_db() returns it."""
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    tr = conn.transaction()
    await tr.start()
    for stmt in SCHEMA_STATEMENTS:
        await conn.execute(stmt)

    @asynccontextmanager
    async def _mock_get_db():
        yield conn

    with patch("data.store.get_db", _mock_get_db), \
         patch("scanner.news.get_db", _mock_get_db):
        yield conn

    await tr.rollback()
    await conn.close()


# ---------------------------------------------------------------------------
# _validate_classification
# ---------------------------------------------------------------------------

def test_validate_clamps_sentiment():
    result = CatalystEngine._validate_classification({"sentiment": 5.0})
    assert result["sentiment"] == 1.0

    result = CatalystEngine._validate_classification({"sentiment": -3.0})
    assert result["sentiment"] == -1.0


def test_validate_clamps_magnitude():
    result = CatalystEngine._validate_classification({"magnitude": 10})
    assert result["magnitude"] == 5

    result = CatalystEngine._validate_classification({"magnitude": 0})
    assert result["magnitude"] == 1


def test_validate_bad_catalyst_type():
    result = CatalystEngine._validate_classification({"catalyst_type": "unknown_thing"})
    assert result["catalyst_type"] == "other"


def test_validate_good_catalyst_types():
    for ct in VALID_CATALYST_TYPES:
        result = CatalystEngine._validate_classification({"catalyst_type": ct})
        assert result["catalyst_type"] == ct


def test_validate_symbols_uppercased():
    result = CatalystEngine._validate_classification({"symbols": ["aapl", "msft"]})
    assert result["symbols"] == ["AAPL", "MSFT"]


def test_validate_non_list_symbols():
    result = CatalystEngine._validate_classification({"symbols": "AAPL"})
    assert result["symbols"] == []


def test_validate_non_numeric_sentiment():
    result = CatalystEngine._validate_classification({"sentiment": "bad"})
    assert result["sentiment"] == 0.0


# ---------------------------------------------------------------------------
# _resolve_sector
# ---------------------------------------------------------------------------

def test_resolve_sector_exact():
    assert CatalystEngine._resolve_sector("Technology") == "Technology"


def test_resolve_sector_case_insensitive():
    assert CatalystEngine._resolve_sector("technology") == "Technology"
    assert CatalystEngine._resolve_sector("ENERGY") == "Energy"


def test_resolve_sector_unknown():
    assert CatalystEngine._resolve_sector("Crypto") is None


# ---------------------------------------------------------------------------
# classify_catalyst
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_classify_success(engine):
    llm_response = json.dumps({
        "sentiment": 0.8,
        "magnitude": 4,
        "catalyst_type": "earnings",
        "symbols": ["AAPL"],
        "sectors": ["Technology"],
    })
    mock_msg = SimpleNamespace(content=[SimpleNamespace(text=llm_response)])
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_msg)
    engine._client = mock_client

    result = await engine.classify_catalyst("Apple beats earnings", "Revenue up 15%")
    assert result["sentiment"] == 0.8
    assert result["magnitude"] == 4
    assert result["catalyst_type"] == "earnings"
    assert result["symbols"] == ["AAPL"]


@pytest.mark.asyncio
async def test_classify_llm_failure_returns_defaults(engine):
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API down"))
    engine._client = mock_client

    result = await engine.classify_catalyst("Some headline", "Some summary")
    assert result["sentiment"] == 0.0
    assert result["magnitude"] == 1
    assert result["catalyst_type"] == "other"


# ---------------------------------------------------------------------------
# save_catalyst / get_recent_catalysts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_and_query(engine, news_db):
    catalyst = Catalyst(
        timestamp=int(time.time()),
        symbol="AAPL",
        sector="Technology",
        headline="Test headline",
        source="TestFeed",
        sentiment=0.5,
        magnitude=3,
        catalyst_type="earnings",
    )
    row_id = await engine.save_catalyst(catalyst)
    assert isinstance(row_id, int) and row_id > 0

    recent = await engine.get_recent_catalysts(symbol="AAPL", hours=1)
    assert len(recent) == 1
    assert recent[0].headline == "Test headline"


@pytest.mark.asyncio
async def test_get_recent_filters_by_time(engine, news_db):
    old_ts = int(time.time()) - 100_000
    new_ts = int(time.time())
    for ts, hl in [(old_ts, "Old news"), (new_ts, "New news")]:
        await engine.save_catalyst(Catalyst(
            timestamp=ts, headline=hl, source="Test", symbol="X",
        ))
    recent = await engine.get_recent_catalysts(symbol="X", hours=1)
    assert len(recent) == 1
    assert recent[0].headline == "New news"


# ---------------------------------------------------------------------------
# get_catalyst_strength
# ---------------------------------------------------------------------------

def test_strength_empty():
    engine = CatalystEngine(api_key="k")
    assert engine.get_catalyst_strength([]) == 0.0


def test_strength_decay():
    engine = CatalystEngine(api_key="k")
    now = 1_000_000
    c1 = Catalyst(timestamp=now, headline="h1", source="s", sentiment=1.0, magnitude=5)
    c2 = Catalyst(timestamp=now - 7 * 3600, headline="h2", source="s", sentiment=1.0, magnitude=5)

    s1 = engine.get_catalyst_strength([c1], now_ts=now)
    s2 = engine.get_catalyst_strength([c2], now_ts=now)
    assert s1 > s2
    assert s2 == pytest.approx(5.0 * math.exp(-0.1 * 7), abs=0.01)


def test_strength_sums_multiple():
    engine = CatalystEngine(api_key="k")
    now = 1_000_000
    catalysts = [
        Catalyst(timestamp=now, headline="h1", source="s", sentiment=0.5, magnitude=3),
        Catalyst(timestamp=now, headline="h2", source="s", sentiment=-0.3, magnitude=2),
    ]
    score = engine.get_catalyst_strength(catalysts, now_ts=now)
    assert score == pytest.approx(0.5 * 3 - 0.3 * 2, abs=0.01)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dedup_skips_existing(engine, news_db):
    """Same headline polled twice → only one DB row."""
    feed_entry = {
        "entries": [
            SimpleNamespace(
                title="Dupe headline",
                summary="Body text",
                published_parsed=time.gmtime(),
            )
        ],
        "feed": {"title": "TestFeed"},
    }

    with patch("scanner.news.feedparser.parse", return_value=SimpleNamespace(
        entries=feed_entry["entries"], feed=feed_entry["feed"]
    )):
        mock_client = AsyncMock()
        llm_resp = json.dumps({
            "sentiment": 0.1, "magnitude": 1, "catalyst_type": "other",
            "symbols": [], "sectors": [],
        })
        mock_client.messages.create = AsyncMock(
            return_value=SimpleNamespace(content=[SimpleNamespace(text=llm_resp)])
        )
        engine._client = mock_client

        first = await engine.poll_feeds()
        assert len(first) == 1

        second = await engine.poll_feeds()
        assert len(second) == 0


# ---------------------------------------------------------------------------
# poll_feeds end-to-end (mocked HTTP + LLM)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_poll_feeds_e2e(engine, news_db):
    entries = [
        SimpleNamespace(
            title="FDA approves new drug",
            summary="Big pharma gets approval",
            published_parsed=time.gmtime(),
        ),
        SimpleNamespace(
            title="Tech layoffs continue",
            summary="Major tech companies cut staff",
            published_parsed=time.gmtime(),
        ),
    ]
    feed = SimpleNamespace(entries=entries, feed={"title": "Reuters"})

    with patch("scanner.news.feedparser.parse", return_value=feed):
        llm_results = [
            json.dumps({
                "sentiment": 0.7, "magnitude": 4, "catalyst_type": "fda",
                "symbols": ["PFE"], "sectors": ["Health Care"],
            }),
            json.dumps({
                "sentiment": -0.5, "magnitude": 3, "catalyst_type": "macro",
                "symbols": ["GOOG"], "sectors": ["Technology"],
            }),
        ]
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            text = llm_results[call_count]
            call_count += 1
            return SimpleNamespace(content=[SimpleNamespace(text=text)])

        mock_client = AsyncMock()
        mock_client.messages.create = mock_create
        engine._client = mock_client

        results = await engine.poll_feeds()

    assert len(results) == 2
    assert results[0].symbol == "PFE"
    assert results[0].sector == "Health Care"
    assert results[0].catalyst_type == "fda"
    assert results[1].symbol == "GOOG"
    assert results[1].sentiment == -0.5
