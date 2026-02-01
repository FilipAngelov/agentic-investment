"""BacktestEngine — main event loop for event-driven backtesting."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from backtest.candidate_source import BacktestCandidateSource
from backtest.data_feed import HistoricalDataFeed
from backtest.fill_model import FillModel
from backtest.results import BacktestResults, TradeRecord
from backtest.sim_account import SimulatedAccount
from config.settings import RiskConfig
from data.indicators import atr as calc_atr
from data.models import Bar, ProposedOrder
from execution.dtbp_guard import DTBPGuard
from execution.partial_exit import PartialExitManager
from execution.risk import RiskController
from execution.sizing import PositionSizer
from execution.time_decay import TimeDecayManager
from execution.trailing_stop import TrailingStopManager
from portfolio.tracker import PositionTracker
from scanner.regime import RegimeDetector
from signals.pipeline import generate_signal

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    start_date: str = "2024-01-01"
    end_date: str = "2024-12-31"
    initial_capital: float = 10_000
    slippage_bps: float = 5.0
    commission_per_share: float = 0.005
    partial_fill_prob: float = 0.05
    universe_size: int = 50
    risk_config: RiskConfig = field(default_factory=RiskConfig)
    primary_timeframe: str = "5m"
    output_dir: str = "results"
    seed: int | None = 42


class BacktestEngine:
    """Event-driven backtester that reuses production signal/execution components."""

    def __init__(self, config: BacktestConfig, feed: HistoricalDataFeed) -> None:
        self._config = config
        self._feed = feed

        rc = config.risk_config
        self._sim = SimulatedAccount(config.initial_capital)
        self._tracker = PositionTracker()
        self._fill_model = FillModel(
            slippage_bps=config.slippage_bps,
            commission_per_share=config.commission_per_share,
            partial_fill_prob=config.partial_fill_prob,
            seed=config.seed,
        )
        self._sizer = PositionSizer(rc)
        self._guard = DTBPGuard(rc)
        self._risk = RiskController(rc)
        self._trailing = TrailingStopManager(rc)
        self._partial = PartialExitManager(rc)
        self._time_decay = TimeDecayManager(rc)
        self._regime = RegimeDetector()
        self._candidates = BacktestCandidateSource(feed, config.universe_size)
        self._results = BacktestResults(config.initial_capital)
        self._targets: dict[str, float] = {}

    async def run(self) -> BacktestResults:
        """Execute the backtest and return results."""
        daily_ts_list = self._feed.daily_timestamps()
        if not daily_ts_list:
            logger.warning("No daily timestamps found in feed")
            return self._results

        for day_idx, day_ts in enumerate(daily_ts_list):
            self._run_day_start(day_ts)

            # Regime from SPY/VIX daily bars
            regime = self._update_regime(day_ts)

            # Candidates (monthly reconstitution)
            candidates = self._candidates.get_candidates(day_ts)

            # Intraday loop
            intraday_ts_list = self._feed.intraday_timestamps(day_ts, self._config.primary_timeframe)
            for ts in intraday_ts_list:
                self._process_bar(ts, regime, candidates)

            # End-of-day snapshot
            positions = self._tracker.positions
            eq = self._sim.equity(positions)
            self._results.snapshot(day_ts, eq, self._sim.cash, len(positions), self._sim.daily_pnl)

            if (day_idx + 1) % 20 == 0:
                logger.info(
                    "Day %d/%d | equity=%.2f | positions=%d | trades=%d",
                    day_idx + 1, len(daily_ts_list), eq, len(positions),
                    len(self._results._trades),
                )

        return self._results

    def _run_day_start(self, day_ts: int) -> None:
        """New day: reset daily P&L, reset risk halt."""
        self._sim.new_day(self._tracker.positions)
        self._risk.reset_halt()

    def _update_regime(self, day_ts: int):
        """Feed SPY/VIX daily bars into regime detector."""
        spy_bars = self._feed.get_bars_up_to("SPY", "1d", day_ts, lookback=65)
        vix_bars = self._feed.get_bars_up_to("VIX", "1d", day_ts, lookback=65)
        # Also try ^VIX
        if not vix_bars:
            vix_bars = self._feed.get_bars_up_to("^VIX", "1d", day_ts, lookback=65)

        if spy_bars:
            self._regime.ingest_spy(
                [b["close"] for b in spy_bars],
                [b["timestamp"] for b in spy_bars],
            )
        if vix_bars:
            self._regime.ingest_vix(
                [b["close"] for b in vix_bars],
                [b["timestamp"] for b in vix_bars],
            )

        regime = self._regime.classify_regime()
        return regime or "choppy"

    def _process_bar(self, ts: int, regime, candidates: list[str]) -> None:
        """Process a single intraday bar: exits first, then entries."""
        tf = self._config.primary_timeframe
        positions = self._tracker.positions

        # 1. Update position prices
        current_prices: dict[str, float] = {}
        atrs: dict[str, float] = {}
        for sym in list(positions):
            bar = self._feed.get_bar_at(sym, tf, ts)
            if bar:
                self._tracker.update_price(sym, bar["close"])
                current_prices[sym] = bar["close"]
                atr_val = self._compute_atr(sym, tf, ts)
                if atr_val:
                    atrs[sym] = atr_val

        # Refresh positions after price update
        positions = self._tracker.positions

        # 2. Check stop fills
        for sym in list(positions):
            if sym not in positions:
                continue
            pos = positions[sym]
            bar = self._feed.get_bar_at(sym, tf, ts)
            if not bar:
                continue
            fill = self._fill_model.try_stop_fill(pos.stop_price, pos.direction, pos.shares, bar)
            if fill.filled:
                pnl = self._tracker.close_position(sym, fill.fill_price, ts)
                self._sim.on_fill_sell(fill.fill_shares, fill.fill_price, fill.commission, pnl)
                self._record_trade(pos, fill.fill_price, ts, fill.commission, "stop")
                self._partial.reset(sym)
                positions = self._tracker.positions
                continue

            # 2b. Check target fills
            fill = self._fill_model.try_target_fill(pos.stop_price, pos.direction, pos.shares, bar)
            # Actually check target, not stop again
            target = self._get_target_for(pos)
            if target:
                fill = self._fill_model.try_target_fill(target, pos.direction, pos.shares, bar)
                if fill.filled:
                    pnl = self._tracker.close_position(sym, fill.fill_price, ts)
                    self._sim.on_fill_sell(fill.fill_shares, fill.fill_price, fill.commission, pnl)
                    self._record_trade(pos, fill.fill_price, ts, fill.commission, "target")
                    self._partial.reset(sym)
                    positions = self._tracker.positions

        # 3. Trailing stop updates
        positions = self._tracker.positions
        if positions and atrs:
            updates = self._trailing.update_all(positions, current_prices, atrs, regime)
            for upd in updates:
                self._tracker.update_stop(upd.symbol, upd.new_stop)

        # 4. Partial exits
        positions = self._tracker.positions
        partial_signals = self._partial.check_all(positions, current_prices, atrs)
        for sig in partial_signals:
            pos = self._tracker.get(sig.symbol)
            if not pos:
                continue
            bar = self._feed.get_bar_at(sig.symbol, tf, ts)
            if not bar:
                continue
            fill = self._fill_model.try_target_fill(sig.limit_price, pos.direction, sig.shares_to_sell, bar)
            if fill.filled:
                pnl = self._tracker.reduce_shares(sig.symbol, fill.fill_shares, fill.fill_price, ts)
                self._sim.on_fill_sell(fill.fill_shares, fill.fill_price, fill.commission, pnl)
                self._record_trade_partial(pos, fill.fill_shares, fill.fill_price, ts, fill.commission, f"partial_t{sig.tier}")

        # 5. Time decay
        positions = self._tracker.positions
        td_signals = self._time_decay.check_all(positions, current_prices, atrs, ts)
        for sig in td_signals:
            pos = self._tracker.get(sig.symbol)
            if not pos:
                continue
            if sig.action == "exit":
                price = current_prices.get(sig.symbol, pos.current_price)
                pnl = self._tracker.close_position(sig.symbol, price, ts)
                self._sim.on_fill_sell(pos.shares, price, 0.0, pnl)
                self._record_trade(pos, price, ts, 0.0, f"time_decay_{sig.reason}")
                self._partial.reset(sig.symbol)
            elif sig.action == "tighten_stop" and sig.new_stop is not None:
                self._tracker.update_stop(sig.symbol, sig.new_stop)

        # 6. Risk check
        positions = self._tracker.positions
        account = self._sim.account_state(positions)
        risk_status = self._risk.evaluate(account, positions)

        if risk_status.halt_trading:
            # Force-close all positions
            to_close = self._risk.positions_to_close_from(risk_status, positions)
            for sym in to_close:
                pos = self._tracker.get(sym)
                if not pos:
                    continue
                price = current_prices.get(sym, pos.current_price)
                pnl = self._tracker.close_position(sym, price, ts)
                self._sim.on_fill_sell(pos.shares, price, 0.0, pnl)
                self._record_trade(pos, price, ts, 0.0, "risk_halt")
                self._partial.reset(sym)
            return  # Skip entries when halted

        # 7. Signal scan for entries
        positions = self._tracker.positions
        account = self._sim.account_state(positions)
        for symbol in candidates:
            if symbol in positions:
                continue
            # Skip SPY/VIX — they're benchmarks
            if symbol in ("SPY", "VIX", "^VIX"):
                continue

            bars = self._feed.get_bars_up_to(symbol, tf, ts, lookback=200)
            if len(bars) < 50:
                continue

            daily_bars = self._feed.get_bars_up_to(symbol, "1d", ts, lookback=200)
            spy_daily = self._feed.get_bars_up_to("SPY", "1d", ts, lookback=200)

            stock_bar_objs = [Bar(**{k: v for k, v in b.items() if k != "id"}) for b in daily_bars[-60:]] if daily_bars else []
            spy_bar_objs = [Bar(**{k: v for k, v in b.items() if k != "id"}) for b in spy_daily[-60:]] if spy_daily else []

            signal = generate_signal(
                bars=bars,
                regime=regime,
                stock_bars=stock_bar_objs,
                benchmark_bars=spy_bar_objs,
                catalysts=[],  # No catalysts in backtest
            )
            if signal is None:
                continue

            # Size the position
            shares = self._sizer.calculate(signal, account, positions)
            if shares <= 0:
                continue

            # DTBP guard
            order = ProposedOrder(
                symbol=signal.symbol,
                direction=signal.direction,
                shares=shares,
                limit_price=signal.entry_price,
                stop_price=signal.stop_price,
                sector=signal.sector,
            )
            check = self._guard.pre_trade_check(order, account, positions)
            if not check.approved:
                continue

            # Simulate entry fill
            bar = self._feed.get_bar_at(symbol, tf, ts)
            if not bar:
                continue
            fill = self._fill_model.try_entry_fill(signal.entry_price, signal.direction, shares, bar)
            if not fill.filled:
                continue

            # Open position
            self._sim.on_fill_buy(fill.fill_shares, fill.fill_price, fill.commission)
            self._tracker.open_position(
                symbol=signal.symbol,
                direction=signal.direction,
                shares=fill.fill_shares,
                entry_price=fill.fill_price,
                entry_time=ts,
                stop_price=signal.stop_price,
                current_price=fill.fill_price,
                sector=signal.sector,
            )
            # Store target on the signal for later
            self._targets[signal.symbol] = signal.target_price

            # Refresh for next candidate
            positions = self._tracker.positions
            account = self._sim.account_state(positions)

    def _compute_atr(self, symbol: str, timeframe: str, ts: int, period: int = 14) -> float | None:
        bars = self._feed.get_bars_up_to(symbol, timeframe, ts, lookback=period + 5)
        if len(bars) < period + 1:
            return None
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        closes = [b["close"] for b in bars]
        atr_vals = calc_atr(highs, lows, closes, period)
        # Return last non-None
        for v in reversed(atr_vals):
            if v is not None:
                return v
        return None

    def _get_target_for(self, pos) -> float | None:
        return self._targets.get(pos.symbol)

    def _record_trade(self, pos, exit_price, exit_time, commission, reason):
        if pos.direction == "LONG":
            pnl = (exit_price - pos.entry_price) * pos.shares
        else:
            pnl = (pos.entry_price - exit_price) * pos.shares
        pnl_pct = (pnl / (pos.entry_price * pos.shares)) * 100 if pos.entry_price else 0
        self._results.add_trade(TradeRecord(
            symbol=pos.symbol,
            direction=pos.direction,
            shares=pos.shares,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            pnl=pnl,
            pnl_pct=pnl_pct,
            commission=commission,
            sector=pos.sector,
        ))
        self._targets.pop(pos.symbol, None)

    def _record_trade_partial(self, pos, shares, exit_price, exit_time, commission, reason):
        if pos.direction == "LONG":
            pnl = (exit_price - pos.entry_price) * shares
        else:
            pnl = (pos.entry_price - exit_price) * shares
        pnl_pct = (pnl / (pos.entry_price * shares)) * 100 if pos.entry_price else 0
        self._results.add_trade(TradeRecord(
            symbol=pos.symbol,
            direction=pos.direction,
            shares=shares,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=exit_time,
            pnl=pnl,
            pnl_pct=pnl_pct,
            commission=commission,
            sector=pos.sector,
        ))

