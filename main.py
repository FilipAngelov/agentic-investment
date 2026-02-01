"""Entry point: orchestrator, agent scheduling, event loop."""

import asyncio

from ib_async import IB

from config.settings import ib_config, load_protected_positions, risk_config


async def main():
    ib = IB()
    cfg = ib_config
    print(f"Connecting to IB Gateway at {cfg.host}:{cfg.port} (client_id={cfg.client_id})...")
    await ib.connectAsync(cfg.host, cfg.port, clientId=cfg.client_id)
    print("Connected.\n")

    try:
        # Account summary
        account_values = await ib.accountSummaryAsync()
        fields = {
            "NetLiquidation",
            "BuyingPower",
            "AvailableFunds",
            "DayTradesRemaining",
            "ExcessLiquidity",
        }
        print("=== Account Summary ===")
        for av in account_values:
            if av.tag in fields:
                print(f"  {av.tag}: {av.value} {av.currency}")

        print(f"\n=== Strategy Config ===")
        print(f"  Capital: ${risk_config.strategy_capital:,.0f}")
        print(f"  Max daily drawdown: {risk_config.max_daily_drawdown_pct}%")
        print(f"  Max portfolio heat: {risk_config.max_portfolio_heat_pct}%")

        # Protected positions
        protected = load_protected_positions()
        print(f"\n=== Protected Positions ({len(protected)} symbols) ===")
        for symbol, shares in sorted(protected.items()):
            print(f"  {symbol}: {shares} shares")

        # Current IBKR positions
        positions = ib.positions()
        print(f"\n=== IBKR Positions ({len(positions)}) ===")
        if positions:
            print(f"  {'Symbol':<10} {'Qty':>8} {'AvgCost':>10} {'MktValue':>12} {'UnrealPnL':>12}")
            print(f"  {'-'*10} {'-'*8} {'-'*10} {'-'*12} {'-'*12}")
            portfolio_items = ib.portfolio()
            for pos in positions:
                symbol = pos.contract.symbol
                qty = pos.position
                avg_cost = pos.avgCost
                pf = next((p for p in portfolio_items if p.contract.symbol == symbol), None)
                mkt_val = pf.marketValue if pf else 0.0
                unreal_pnl = pf.unrealizedPNL if pf else 0.0
                print(f"  {symbol:<10} {qty:>8.0f} {avg_cost:>10.2f} {mkt_val:>12.2f} {unreal_pnl:>12.2f}")
        else:
            print("  No positions.")
    finally:
        ib.disconnect()
        print("\nDisconnected.")


if __name__ == "__main__":
    asyncio.run(main())
