"""Load env vars + risk parameters."""

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STARTING_PORTFOLIO_PATH = PROJECT_ROOT / "config" / "starting_portfolio.csv"
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/agentic_investment")


@dataclass(frozen=True)
class IBConfig:
    """IB Gateway connection settings."""

    host: str = os.getenv("IB_HOST", "127.0.0.1")
    port: int = int(os.getenv("IB_PORT", "4002"))
    client_id: int = int(os.getenv("IB_CLIENT_ID", "1"))
    account_id: str = os.getenv("IB_ACCOUNT_ID", "")


@dataclass(frozen=True)
class RiskConfig:
    """Risk management parameters from design doc."""

    # Strategy budget
    strategy_capital: float = float(os.getenv("STRATEGY_CAPITAL", "10000"))

    # Daily drawdown kill switch (% of strategy capital)
    max_daily_drawdown_pct: float = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "2.0"))

    # Portfolio heat limits
    max_portfolio_heat_pct: float = 6.0  # total % equity at risk
    max_sector_heat_pct: float = 3.0  # max risk in single sector
    max_correlation_cluster: int = 3  # max positions with corr > 0.7

    # Per-trade risk
    risk_per_trade_pct: float = 1.0  # max 1% of equity per trade

    # Position limits
    max_concurrent_positions: int = 10

    # Excess liquidity floor (account-wide)
    min_excess_liquidity_pct: float = 5.0

    # Trailing stop ATR multipliers (regime-adjusted via regime_factor)
    stop_k1: float = 2.0  # initial stop
    stop_k2: float = 1.5  # after +1 ATR profit
    stop_k3: float = 1.0  # after +2 ATR profit
    stop_k4: float = 0.75  # after +3 ATR profit

    # Partial exit schedule (ATR multiples)
    partial_exit_1_atr: float = 1.0  # sell 25%
    partial_exit_2_atr: float = 2.0  # sell another 25%

    # Time decay thresholds
    time_decay_hours: int = 4  # tighten to breakeven if < 0.5 ATR profit
    time_decay_days: int = 3  # exit at resistance if < 1 ATR profit
    time_decay_max_days: int = 5  # re-evaluate regardless


@dataclass(frozen=True)
class NotifyConfig:
    """Notification settings."""

    whatsapp_api_url: str = os.getenv("WHATSAPP_API_URL", "")
    whatsapp_api_key: str = os.getenv("WHATSAPP_API_KEY", "")


@dataclass(frozen=True)
class NewsConfig:
    """RSS / catalyst engine settings."""

    feed_urls: tuple[str, ...] = (
        "https://feeds.reuters.com/reuters/businessNews",
        "https://feeds.reuters.com/reuters/technologyNews",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&dateb=&owner=include&count=40&search_text=&start=0&output=atom",
        "https://www.statnews.com/feed/",
        "https://www.defenseone.com/rss/",
        "https://www.kitco.com/rss/",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://www.utilitydive.com/feeds/news/",
        "https://spacenews.com/feed/",
        "https://www.world-nuclear-news.org/rss",
    )
    poll_interval_market: int = 120  # seconds
    poll_interval_off: int = 1800  # seconds
    max_age_hours: int = 48
    llm_model: str = "claude-haiku-4-20250414"


@dataclass(frozen=True)
class LLMConfig:
    """LLM API settings (warm/cold path only)."""

    api_key: str = os.getenv("LLM_API_KEY", "")


def load_protected_positions(path: Path = STARTING_PORTFOLIO_PATH) -> dict[str, int]:
    """Load starting_portfolio.csv → {symbol: shares}.

    These positions are SACRED — never sell them.
    """
    protected: dict[str, int] = {}
    if not path.exists():
        return protected
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = row["symbol"].strip().upper()
            shares = int(row["shares"])
            protected[symbol] = shares
    return protected


# Module-level singletons
ib_config = IBConfig()
risk_config = RiskConfig()
notify_config = NotifyConfig()
llm_config = LLMConfig()
news_config = NewsConfig()


@dataclass(frozen=True)
class IngestConfig:
    """Historical bar ingestion settings."""

    max_requests_per_sec: int = 45
    pacing_delay_sec: float = 0.05
    daily_lookback_days: int = 500
    intraday_5m_lookback_days: int = 5
    intraday_15m_lookback_days: int = 5
    retention_1m_days: int = 5
    retention_5m_days: int = 90
    max_retries: int = 3


ingest_config = IngestConfig()
