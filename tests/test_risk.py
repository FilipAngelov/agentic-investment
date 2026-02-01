"""Tests for execution/risk.py — RiskController."""

import pytest

from config.settings import RiskConfig
from data.models import AccountState, Position, RiskStatus
from execution.risk import RiskController


def _account(nlv: float = 50_000, daily_pnl: float = 0.0) -> AccountState:
    return AccountState(
        net_liquidation=nlv,
        total_cash_value=nlv,
        buying_power=nlv * 2,
        available_funds=nlv,
        excess_liquidity=nlv * 0.1,
        init_margin_req=0,
        maint_margin_req=0,
        sma=nlv,
        day_trades_remaining=-1,
        daily_pnl=daily_pnl,
    )


def _pos(
    symbol: str = "AAPL",
    entry: float = 100.0,
    stop: float = 95.0,
    shares: int = 100,
    sector: str | None = "tech",
    pnl: float = 0.0,
) -> Position:
    return Position(
        symbol=symbol,
        direction="LONG",
        shares=shares,
        entry_price=entry,
        entry_time=0,
        current_price=entry,
        stop_price=stop,
        unrealized_pnl=pnl,
        sector=sector,
    )


def _risk_config(**kw) -> RiskConfig:
    defaults = {
        "strategy_capital": 10_000,
        "max_daily_drawdown_pct": 2.0,
        "max_portfolio_heat_pct": 6.0,
        "max_sector_heat_pct": 3.0,
        "max_correlation_cluster": 3,
    }
    defaults.update(kw)
    return RiskConfig(**defaults)


class TestNoPositions:
    def test_empty(self):
        rc = RiskController(_risk_config())
        status = rc.evaluate(_account(), {})
        assert not status.drawdown_breached
        assert not status.heat_breached
        assert status.portfolio_heat == 0.0
        assert status.sector_breached == []
        assert not status.halt_trading


class TestDrawdown:
    def test_breached(self):
        rc = RiskController(_risk_config())
        status = rc.evaluate(_account(daily_pnl=-200), {})
        assert status.drawdown_breached
        assert status.halt_trading

    def test_not_breached(self):
        rc = RiskController(_risk_config())
        status = rc.evaluate(_account(daily_pnl=-199), {})
        assert not status.drawdown_breached
        assert not status.halt_trading

    def test_exactly_at_limit(self):
        rc = RiskController(_risk_config())
        status = rc.evaluate(_account(daily_pnl=-200), {})
        assert status.drawdown_breached

    def test_halted_persists(self):
        rc = RiskController(_risk_config())
        rc.evaluate(_account(daily_pnl=-200), {})
        assert rc.is_halted
        status = rc.evaluate(_account(daily_pnl=0), {})
        assert rc.is_halted
        assert status.halt_trading

    def test_reset_halt(self):
        rc = RiskController(_risk_config())
        rc.evaluate(_account(daily_pnl=-200), {})
        assert rc.is_halted
        rc.reset_halt()
        assert not rc.is_halted
        status = rc.evaluate(_account(daily_pnl=0), {})
        assert not status.halt_trading


class TestPortfolioHeat:
    def test_over_limit(self):
        rc = RiskController(_risk_config())
        positions = {
            f"SYM{i}": _pos(symbol=f"SYM{i}", sector=f"sec{i}")
            for i in range(7)
        }
        status = rc.evaluate(_account(), positions)
        assert status.heat_breached

    def test_under_limit(self):
        rc = RiskController(_risk_config())
        positions = {"AAPL": _pos()}
        status = rc.evaluate(_account(), positions)
        assert not status.heat_breached


class TestSectorHeat:
    def test_single_sector_over(self):
        rc = RiskController(_risk_config())
        positions = {
            f"SYM{i}": _pos(symbol=f"SYM{i}", sector="tech")
            for i in range(4)
        }
        status = rc.evaluate(_account(), positions)
        assert "tech" in status.sector_breached

    def test_multiple_sectors_one_over(self):
        rc = RiskController(_risk_config())
        positions = {
            "A1": _pos(symbol="A1", sector="tech"),
            "A2": _pos(symbol="A2", sector="tech"),
            "A3": _pos(symbol="A3", sector="tech"),
            "A4": _pos(symbol="A4", sector="tech"),
            "B1": _pos(symbol="B1", sector="energy"),
        }
        status = rc.evaluate(_account(), positions)
        assert "tech" in status.sector_breached
        assert "energy" not in status.sector_breached


class TestConcentration:
    def test_four_same_sector_flagged(self):
        rc = RiskController(_risk_config())
        positions = {
            f"S{i}": _pos(symbol=f"S{i}", sector="tech") for i in range(4)
        }
        assert "tech" in rc.check_concentration(positions)

    def test_three_same_sector_not_flagged(self):
        rc = RiskController(_risk_config())
        positions = {
            f"S{i}": _pos(symbol=f"S{i}", sector="tech") for i in range(3)
        }
        assert "tech" not in rc.check_concentration(positions)


class TestPositionsToClose:
    def test_drawdown_returns_all(self):
        rc = RiskController(_risk_config())
        positions = {
            "AAPL": _pos(symbol="AAPL", pnl=-50),
            "MSFT": _pos(symbol="MSFT", pnl=10),
        }
        status = rc.evaluate(_account(daily_pnl=-200), positions)
        to_close = rc.positions_to_close_from(status, positions)
        assert set(to_close) == {"AAPL", "MSFT"}
        assert to_close[0] == "AAPL"


class TestZeroNLV:
    def test_no_crash(self):
        rc = RiskController(_risk_config())
        positions = {"AAPL": _pos()}
        status = rc.evaluate(_account(nlv=0), positions)
        assert status.portfolio_heat == 0.0
        assert not status.heat_breached


class TestWarnings:
    def test_nlv_near_25k(self):
        rc = RiskController(_risk_config())
        status = rc.evaluate(_account(nlv=25_500), {})
        assert any("25,000" in w for w in status.warnings)

    def test_nlv_well_above_25k(self):
        rc = RiskController(_risk_config())
        status = rc.evaluate(_account(nlv=50_000), {})
        assert not any("25,000" in w for w in status.warnings)
