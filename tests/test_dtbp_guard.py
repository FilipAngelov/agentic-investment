"""Tests for DTBP compliance module."""

from pathlib import Path
from textwrap import dedent

import pytest

from config.settings import RiskConfig
from data.models import AccountState, Position, ProposedOrder
from execution.dtbp_guard import DTBPGuard, ProtectedPositionError, ProtectedPositionsGuard


@pytest.fixture
def guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProtectedPositionsGuard:
    """Create a guard with a test CSV."""
    csv_file = tmp_path / "portfolio.csv"
    csv_file.write_text(dedent("""\
        symbol,shares,note
        NVDA,5,long-term hold
        AAPL,10,long-term hold
        ASML,1,long-term hold
    """))
    monkeypatch.setattr(
        "execution.dtbp_guard.load_protected_positions",
        lambda: {
            "NVDA": 5,
            "AAPL": 10,
            "ASML": 1,
        },
    )
    return ProtectedPositionsGuard()


class TestProtectedPositionsGuard:
    def test_protected_shares(self, guard: ProtectedPositionsGuard):
        assert guard.protected_shares("NVDA") == 5
        assert guard.protected_shares("AAPL") == 10
        assert guard.protected_shares("UNKNOWN") == 0

    def test_case_insensitive(self, guard: ProtectedPositionsGuard):
        assert guard.protected_shares("nvda") == 5
        assert guard.protected_shares("Aapl") == 10

    def test_sellable_shares_unprotected_symbol(self, guard: ProtectedPositionsGuard):
        # Symbol not in protected list — all shares sellable
        assert guard.sellable_shares("TSLA", 100) == 100

    def test_sellable_shares_protected_symbol(self, guard: ProtectedPositionsGuard):
        # Own 20 NVDA, 5 protected → 15 sellable
        assert guard.sellable_shares("NVDA", 20) == 15

    def test_sellable_shares_exactly_protected(self, guard: ProtectedPositionsGuard):
        # Own exactly the protected amount → 0 sellable
        assert guard.sellable_shares("NVDA", 5) == 0

    def test_sellable_shares_less_than_protected(self, guard: ProtectedPositionsGuard):
        # Somehow own fewer than protected (shouldn't happen, but guard against it)
        assert guard.sellable_shares("NVDA", 3) == 0

    def test_check_sell_allowed(self, guard: ProtectedPositionsGuard):
        # 20 NVDA, 5 protected, sell 10 → ok
        guard.check_sell("NVDA", current_position=20, sell_qty=10)

    def test_check_sell_max_sellable(self, guard: ProtectedPositionsGuard):
        # Sell exactly the sellable amount
        guard.check_sell("NVDA", current_position=20, sell_qty=15)

    def test_check_sell_blocked_all_protected(self, guard: ProtectedPositionsGuard):
        with pytest.raises(ProtectedPositionError, match="all 5 shares are protected"):
            guard.check_sell("NVDA", current_position=5, sell_qty=1)

    def test_check_sell_blocked_exceeds_sellable(self, guard: ProtectedPositionsGuard):
        with pytest.raises(ProtectedPositionError, match="only 15 sellable"):
            guard.check_sell("NVDA", current_position=20, sell_qty=16)

    def test_check_sell_unprotected_symbol(self, guard: ProtectedPositionsGuard):
        # Unprotected symbol — any sell is fine
        guard.check_sell("TSLA", current_position=100, sell_qty=100)

    def test_check_sell_invalid_qty(self, guard: ProtectedPositionsGuard):
        with pytest.raises(ValueError, match="sell_qty must be positive"):
            guard.check_sell("NVDA", current_position=20, sell_qty=0)

    def test_is_protected(self, guard: ProtectedPositionsGuard):
        assert guard.is_protected("NVDA") is True
        assert guard.is_protected("TSLA") is False

    def test_bot_bought_shares_of_protected_symbol(self, guard: ProtectedPositionsGuard):
        """If bot buys 50 ASML (Filip holds 1), can only sell 50, not 51."""
        guard.check_sell("ASML", current_position=51, sell_qty=50)
        with pytest.raises(ProtectedPositionError):
            guard.check_sell("ASML", current_position=51, sell_qty=51)


# ---------------------------------------------------------------------------
# DTBPGuard tests
# ---------------------------------------------------------------------------


def make_account(**overrides) -> AccountState:
    defaults = dict(
        net_liquidation=50_000,
        total_cash_value=20_000,
        buying_power=80_000,
        available_funds=20_000,
        excess_liquidity=5_000,
        init_margin_req=10_000,
        maint_margin_req=8_000,
        sma=50_000,
        day_trades_remaining=-1,
        daily_pnl=0.0,
    )
    defaults.update(overrides)
    return AccountState(**defaults)


def make_order(**overrides) -> ProposedOrder:
    defaults = dict(
        symbol="TEST",
        direction="LONG",
        shares=100,
        limit_price=50.0,
        stop_price=48.0,
    )
    defaults.update(overrides)
    return ProposedOrder(**defaults)


def make_position(**overrides) -> Position:
    defaults = dict(
        symbol="POS",
        direction="LONG",
        shares=100,
        entry_price=50.0,
        entry_time=1000000,
        current_price=51.0,
        stop_price=48.0,
        unrealized_pnl=100.0,
    )
    defaults.update(overrides)
    return Position(**defaults)


@pytest.fixture
def dtbp_guard(monkeypatch: pytest.MonkeyPatch) -> DTBPGuard:
    monkeypatch.setattr(
        "execution.dtbp_guard.load_protected_positions",
        lambda: {"NVDA": 5, "AAPL": 10},
    )
    return DTBPGuard()


class TestDTBPGuard:
    def test_happy_path(self, dtbp_guard: DTBPGuard):
        """All checks pass for a reasonable order."""
        order = make_order(shares=10, limit_price=50.0, stop_price=48.0)
        account = make_account()
        result = dtbp_guard.pre_trade_check(order, account, {})
        assert result.approved is True
        assert len(result.checks_failed) == 0
        assert "daily_drawdown" in result.checks_passed

    def test_daily_drawdown_kill_switch(self, dtbp_guard: DTBPGuard):
        """Reject when daily PnL breaches drawdown limit (-$200 on $10K)."""
        order = make_order()
        account = make_account(daily_pnl=-201.0)
        result = dtbp_guard.pre_trade_check(order, account, {})
        assert result.approved is False
        assert "daily_drawdown" in result.checks_failed

    def test_max_positions_buy_rejected(self, dtbp_guard: DTBPGuard):
        """Opening order rejected when at max positions."""
        positions = {f"SYM{i}": make_position(symbol=f"SYM{i}") for i in range(10)}
        order = make_order()
        account = make_account()
        result = dtbp_guard.pre_trade_check(order, account, positions)
        assert result.approved is False
        assert "max_positions" in result.checks_failed

    def test_max_positions_close_allowed(self, dtbp_guard: DTBPGuard):
        """Closing order allowed even at max positions."""
        positions = {f"SYM{i}": make_position(symbol=f"SYM{i}") for i in range(10)}
        order = make_order(is_closing=True)
        account = make_account()
        result = dtbp_guard.pre_trade_check(order, account, positions)
        assert result.approved is True

    def test_protected_shares_sell_blocked(self, dtbp_guard: DTBPGuard):
        """Cannot short a symbol with protected shares."""
        order = make_order(symbol="NVDA", direction="SHORT", stop_price=52.0)
        account = make_account()
        result = dtbp_guard.pre_trade_check(order, account, {})
        assert result.approved is False
        assert "protected_shares" in result.checks_failed

    def test_strategy_budget_exceeded(self, dtbp_guard: DTBPGuard):
        """Reject when deployed + order > strategy_capital."""
        # deployed = 100 * 95 = 9500, order = 100 * 50 = 5000 → 14500 > 10000
        positions = {"EXISTING": make_position(shares=100, entry_price=95.0)}
        order = make_order()
        account = make_account()
        result = dtbp_guard.pre_trade_check(order, account, positions)
        assert result.approved is False
        assert "strategy_budget" in result.checks_failed

    def test_strategy_budget_close_bypasses(self, dtbp_guard: DTBPGuard):
        """Close orders bypass strategy budget check."""
        positions = {"EXISTING": make_position(shares=100, entry_price=95.0)}
        order = make_order(is_closing=True)
        account = make_account()
        result = dtbp_guard.pre_trade_check(order, account, positions)
        assert result.approved is True

    def test_cash_only_violation(self, dtbp_guard: DTBPGuard):
        """Reject when order cost > cash."""
        order = make_order(shares=100, limit_price=50.0)  # cost = 5000
        account = make_account(total_cash_value=4_000)
        result = dtbp_guard.pre_trade_check(order, account, {})
        assert result.approved is False
        assert "cash_only" in result.checks_failed

    def test_dtbp_exceeded(self, dtbp_guard: DTBPGuard):
        """Reject when order cost > buying power."""
        order = make_order(shares=100, limit_price=50.0)  # cost = 5000
        account = make_account(buying_power=4_000)
        result = dtbp_guard.pre_trade_check(order, account, {})
        assert result.approved is False
        assert "dtbp" in result.checks_failed

    def test_excess_liquidity_below_minimum(self, dtbp_guard: DTBPGuard):
        """Reject when excess liquidity ratio < 5%."""
        # excess_liq / NLV = 2000 / 50000 = 4% < 5%
        order = make_order(shares=10, limit_price=50.0)
        account = make_account(excess_liquidity=2_000)
        result = dtbp_guard.pre_trade_check(order, account, {})
        assert result.approved is False
        assert "excess_liquidity" in result.checks_failed

    def test_day_trade_blocked(self, dtbp_guard: DTBPGuard):
        """Reject closing same-day when NLV < 25K and 0 day trades."""
        order = make_order(is_closing=True, opened_today=True)
        account = make_account(net_liquidation=24_000, day_trades_remaining=0, excess_liquidity=5_000)
        result = dtbp_guard.pre_trade_check(order, account, {})
        assert result.approved is False
        assert "day_trades" in result.checks_failed

    def test_day_trade_allowed_remaining(self, dtbp_guard: DTBPGuard):
        """Allow day trade when remaining > 0."""
        order = make_order(is_closing=True, opened_today=True)
        account = make_account(net_liquidation=24_000, day_trades_remaining=2, excess_liquidity=5_000)
        result = dtbp_guard.pre_trade_check(order, account, {})
        assert result.approved is True

    def test_day_trade_allowed_nlv_above_25k(self, dtbp_guard: DTBPGuard):
        """Allow day trade when NLV >= 25K regardless of remaining."""
        order = make_order(is_closing=True, opened_today=True)
        account = make_account(net_liquidation=30_000, day_trades_remaining=0, excess_liquidity=5_000)
        result = dtbp_guard.pre_trade_check(order, account, {})
        assert result.approved is True

    def test_portfolio_heat_exceeded(self, monkeypatch: pytest.MonkeyPatch):
        """Reject when portfolio heat + order heat > 6%."""
        monkeypatch.setattr(
            "execution.dtbp_guard.load_protected_positions",
            lambda: {"NVDA": 5, "AAPL": 10},
        )
        # Use high strategy_capital so budget check passes
        guard = DTBPGuard(risk_config=RiskConfig(strategy_capital=100_000))
        # pos heat = |50-20|/50 * (500*50)/50000 = 0.6 * 0.5 = 0.30
        positions = {"BIG": make_position(shares=500, entry_price=50.0, stop_price=20.0)}
        order = make_order(shares=100, limit_price=50.0, stop_price=40.0)
        account = make_account()
        result = guard.pre_trade_check(order, account, positions)
        assert result.approved is False
        assert "portfolio_heat" in result.checks_failed

    def test_sector_heat_exceeded(self, monkeypatch: pytest.MonkeyPatch):
        """Reject when sector heat > 3%."""
        monkeypatch.setattr(
            "execution.dtbp_guard.load_protected_positions",
            lambda: {"NVDA": 5, "AAPL": 10},
        )
        guard = DTBPGuard(risk_config=RiskConfig(strategy_capital=100_000, max_portfolio_heat_pct=99.0))
        # sector heat = |50-30|/50 * (200*50)/50000 = 0.4 * 0.2 = 0.08
        positions = {
            "TECH1": make_position(
                symbol="TECH1", shares=200, entry_price=50.0,
                stop_price=30.0, sector="tech",
            )
        }
        order = make_order(shares=100, limit_price=50.0, stop_price=40.0, sector="tech")
        account = make_account()
        result = guard.pre_trade_check(order, account, positions)
        assert result.approved is False
        assert "sector_heat" in result.checks_failed

    def test_reconciliation_match(self, dtbp_guard: DTBPGuard):
        """Bot + protected = IBKR → match."""
        bot = {"TEST": 50}
        ibkr = {"TEST": 50, "NVDA": 5, "AAPL": 10}
        result = dtbp_guard.reconcile(bot, ibkr)
        assert result.matches is True

    def test_reconciliation_mismatch(self, dtbp_guard: DTBPGuard):
        """Mismatch when IBKR differs from expected."""
        bot = {"TEST": 50}
        ibkr = {"TEST": 40, "NVDA": 5, "AAPL": 10}
        result = dtbp_guard.reconcile(bot, ibkr)
        assert result.matches is False
        assert any("TEST" in m for m in result.mismatches)

    def test_nlv_threshold_alert(self, dtbp_guard: DTBPGuard):
        """Alert when NLV below threshold."""
        account = make_account(net_liquidation=24_000)
        warning = dtbp_guard.check_nlv_threshold(account)
        assert warning is not None
        assert "below" in warning

    def test_nlv_threshold_no_alert(self, dtbp_guard: DTBPGuard):
        """No alert when NLV safely above threshold."""
        account = make_account(net_liquidation=30_000)
        warning = dtbp_guard.check_nlv_threshold(account)
        assert warning is None

    def test_short_circuit_first_failure(self, dtbp_guard: DTBPGuard):
        """First failing check is reported; later checks not run."""
        # Trigger drawdown (check 1) — should not see max_positions failure
        positions = {f"SYM{i}": make_position(symbol=f"SYM{i}") for i in range(10)}
        order = make_order()
        account = make_account(daily_pnl=-201.0)
        result = dtbp_guard.pre_trade_check(order, account, positions)
        assert result.approved is False
        assert result.checks_failed == ["daily_drawdown"]
        assert "max_positions" not in result.checks_passed

    def test_shares_validation(self):
        """ProposedOrder rejects shares <= 0."""
        with pytest.raises(ValueError, match="shares must be > 0"):
            make_order(shares=0)
        with pytest.raises(ValueError, match="shares must be > 0"):
            make_order(shares=-5)
