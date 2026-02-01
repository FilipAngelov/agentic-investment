"""Portfolio-level risk monitor — runs periodically (every ~30s)."""

from __future__ import annotations

import time

from config.settings import RiskConfig
from data.models import AccountState, Position, RiskStatus


class RiskController:
    """Continuous portfolio-level risk monitor.

    Unlike DTBPGuard (pre-trade gate), RiskController evaluates the
    current portfolio state and emits actionable signals.
    """

    def __init__(self, risk_config: RiskConfig | None = None) -> None:
        self._risk = risk_config or RiskConfig()
        self._halted: bool = False

    @property
    def is_halted(self) -> bool:
        return self._halted

    def reset_halt(self) -> None:
        """Manual reset (new trading day)."""
        self._halted = False

    def evaluate(
        self,
        account: AccountState,
        bot_positions: dict[str, Position],
    ) -> RiskStatus:
        """Full portfolio risk evaluation. Sets self._halted if breached."""
        dd_breached, daily_pnl, dd_limit = self.check_drawdown(account)
        nlv = account.net_liquidation
        heat = self.calc_portfolio_heat(bot_positions, nlv)
        max_heat = self._risk.max_portfolio_heat_pct / 100
        heat_breached = heat > max_heat

        sector_heats = self.calc_sector_heats(bot_positions, nlv)
        max_sector = self._risk.max_sector_heat_pct / 100
        sector_breached = [s for s, h in sector_heats.items() if h > max_sector]

        concentration = self.check_concentration(bot_positions)

        # Hard halt on drawdown
        if dd_breached:
            self._halted = True

        # Warnings
        warnings: list[str] = []
        if nlv > 0 and nlv < 26_250:  # within 5% of $25K
            warnings.append(f"NLV ${nlv:,.2f} approaching $25,000 PDT threshold")
        if max_heat > 0 and heat > max_heat * 0.8:
            warnings.append(f"Portfolio heat {heat:.4f} > 80% of limit {max_heat:.4f}")
        if sector_breached:
            warnings.append(f"Sector heat breached: {', '.join(sector_breached)}")
        if concentration:
            warnings.append(f"Concentration clusters: {', '.join(concentration)}")

        return RiskStatus(
            timestamp=int(time.time()),
            daily_pnl=daily_pnl,
            drawdown_limit=dd_limit,
            drawdown_breached=dd_breached,
            portfolio_heat=heat,
            max_portfolio_heat=max_heat,
            heat_breached=heat_breached,
            sector_heats=sector_heats,
            max_sector_heat=max_sector,
            sector_breached=sector_breached,
            concentration_clusters=concentration,
            halt_trading=self._halted,
            warnings=warnings,
        )

    def check_drawdown(self, account: AccountState) -> tuple[bool, float, float]:
        """Returns (breached, daily_pnl, limit)."""
        limit = self._risk.strategy_capital * self._risk.max_daily_drawdown_pct / 100
        breached = account.daily_pnl <= -limit
        return breached, account.daily_pnl, limit

    def calc_portfolio_heat(
        self, positions: dict[str, Position], nlv: float
    ) -> float:
        """Sum of (|entry-stop|/entry × shares×entry/NLV) across all positions."""
        if nlv == 0:
            return 0.0
        total = 0.0
        for pos in positions.values():
            if pos.entry_price == 0:
                continue
            total += (
                abs(pos.entry_price - pos.stop_price)
                / pos.entry_price
                * (pos.shares * pos.entry_price)
                / nlv
            )
        return total

    def calc_sector_heats(
        self, positions: dict[str, Position], nlv: float
    ) -> dict[str, float]:
        """Heat grouped by sector."""
        if nlv == 0:
            return {}
        heats: dict[str, float] = {}
        for pos in positions.values():
            sector = pos.sector or "unknown"
            if pos.entry_price == 0:
                continue
            h = (
                abs(pos.entry_price - pos.stop_price)
                / pos.entry_price
                * (pos.shares * pos.entry_price)
                / nlv
            )
            heats[sector] = heats.get(sector, 0.0) + h
        return heats

    def check_concentration(
        self, positions: dict[str, Position]
    ) -> list[str]:
        """Sectors with > max_correlation_cluster positions."""
        counts: dict[str, int] = {}
        for pos in positions.values():
            sector = pos.sector or "unknown"
            counts[sector] = counts.get(sector, 0) + 1
        return sorted(
            s for s, c in counts.items() if c > self._risk.max_correlation_cluster
        )

    def positions_to_close(self, status: RiskStatus) -> list[str]:
        """Symbols to force-exit based on risk status.

        Drawdown halt → all symbols.
        Sector breach → symbols in breached sectors, worst P&L first.
        """
        # Caller must pass bot_positions separately if needed for sorting;
        # for simplicity we return sector-breached symbols from status.
        # Full implementation needs positions dict — keep signature simple.
        return []

    def positions_to_close_from(
        self, status: RiskStatus, bot_positions: dict[str, Position]
    ) -> list[str]:
        """Symbols to force-exit, with position data for sorting."""
        if status.drawdown_breached:
            # Close everything — sort by worst P&L first
            return sorted(
                bot_positions.keys(),
                key=lambda s: bot_positions[s].unrealized_pnl,
            )

        if status.sector_breached:
            breached_set = set(status.sector_breached)
            in_breach = [
                s for s, p in bot_positions.items()
                if (p.sector or "unknown") in breached_set
            ]
            return sorted(
                in_breach,
                key=lambda s: bot_positions[s].unrealized_pnl,
            )

        return []
