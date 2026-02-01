"""Entry point: orchestrator, agent scheduling, event loop."""

import asyncio
import os

from dotenv import load_dotenv
from ib_async import IB


async def main():
    load_dotenv()

    host = os.getenv("IB_HOST", "127.0.0.1")
    port = int(os.getenv("IB_PORT", "4002"))
    client_id = int(os.getenv("IB_CLIENT_ID", "1"))

    ib = IB()
    print(f"Connecting to IB Gateway at {host}:{port} (client_id={client_id})...")
    await ib.connectAsync(host, port, clientId=client_id)
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

        # Positions
        positions = ib.positions()
        print(f"\n=== Positions ({len(positions)}) ===")
        if positions:
            print(f"  {'Symbol':<10} {'Qty':>8} {'AvgCost':>10} {'MktValue':>12} {'UnrealPnL':>12}")
            print(f"  {'-'*10} {'-'*8} {'-'*10} {'-'*12} {'-'*12}")
            for pos in positions:
                symbol = pos.contract.symbol
                qty = pos.position
                avg_cost = pos.avgCost
                # Portfolio items have market value and unrealized PnL
                portfolio_items = ib.portfolio()
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
