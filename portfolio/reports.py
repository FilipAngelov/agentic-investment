"""Daily and weekly report generation."""

from __future__ import annotations

import datetime as dt

import aiosqlite

from data.store import query_trades
from portfolio.analytics import compute_metrics, compute_metrics_by_group
from portfolio.notify import send_whatsapp


def _start_of_today() -> int:
    today = dt.datetime.now(tz=dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(today.timestamp())


def _start_of_week() -> int:
    now = dt.datetime.now(tz=dt.timezone.utc)
    monday = now - dt.timedelta(days=now.weekday())
    start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _format_date() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).strftime("%a %Y-%m-%d")


def _format_week_start() -> str:
    now = dt.datetime.now(tz=dt.timezone.utc)
    monday = now - dt.timedelta(days=now.weekday())
    return monday.strftime("%b %d, %Y")


def _best_worst_symbols(trades: list[dict]) -> tuple[str, str]:
    """Return (best_line, worst_line) from trades by pnl."""
    if not trades:
        return "—", "—"
    closed = [t for t in trades if t.get("pnl") is not None]
    if not closed:
        return "—", "—"
    best = max(closed, key=lambda t: t["pnl"])
    worst = min(closed, key=lambda t: t["pnl"])
    return (
        f"{best['symbol']} {best['pnl']:+.2f}",
        f"{worst['symbol']} {worst['pnl']:+.2f}",
    )


# ── Pure report builders ─────────────────────────────────────────


def build_daily_report(
    trades: list[dict],
    metrics: dict,
    tracker,
    risk_halted: bool,
    recon_ok: bool,
    equity: float = 10_000.0,
) -> str:
    """Build a plain-text daily report string."""
    date_str = _format_date()
    realized = tracker.realized_pnl
    unrealized = tracker.unrealized_pnl
    total_pnl = realized + unrealized
    deployed = tracker.deployed_capital

    count = metrics.get("count", 0)
    win_count = metrics.get("win_count", 0)
    loss_count = metrics.get("loss_count", 0)
    win_rate = metrics.get("win_rate", 0.0) * 100
    pf = metrics.get("profit_factor", 0.0)

    best, worst = _best_worst_symbols(trades)

    positions = tracker.positions
    pos_lines: list[str] = []
    for pos in (positions.values() if isinstance(positions, dict) else positions):
        p = pos if isinstance(pos, dict) else pos
        sym = p.symbol if hasattr(p, "symbol") else p["symbol"]
        direction = p.direction if hasattr(p, "direction") else p["direction"]
        shares = p.shares if hasattr(p, "shares") else p["shares"]
        entry = p.entry_price if hasattr(p, "entry_price") else p["entry_price"]
        current = p.current_price if hasattr(p, "current_price") else p["current_price"]
        upnl = p.unrealized_pnl if hasattr(p, "unrealized_pnl") else p["unrealized_pnl"]
        pos_lines.append(
            f"  {sym} {direction} {shares} @ ${entry:.2f} -> ${current:.2f} ({upnl:+.2f})"
        )

    risk_str = "HALTED" if risk_halted else "OK"
    recon_str = "matched" if recon_ok else "MISMATCH"

    lines = [
        f"DAILY REPORT -- {date_str}",
        "=" * 35,
        f"PnL: {total_pnl:+.2f} (realized {realized:+.2f} + unrealized {unrealized:+.2f})",
        f"Deployed: ${deployed:,.0f} / ${equity:,.0f}",
        "",
        f"Trades: {count} ({win_count}W / {loss_count}L)",
        f"Win rate: {win_rate:.1f}% | Profit factor: {pf:.1f}",
        f"Best: {best} | Worst: {worst}",
        "",
        f"Open positions: {len(positions)}",
    ]
    lines.extend(pos_lines)
    lines.append("")
    lines.append(f"Risk: {risk_str}")
    lines.append(f"Reconciliation: {recon_str}")

    return "\n".join(lines)


def build_weekly_report(
    trades: list[dict],
    metrics: dict,
    sector_metrics: dict[str, dict],
    tracker,
    recon_history: list,
) -> str:
    """Build a plain-text weekly report string."""
    week_str = _format_week_start()
    total_pnl = metrics.get("total_pnl", 0.0)
    count = metrics.get("count", 0)
    win_count = metrics.get("win_count", 0)
    loss_count = metrics.get("loss_count", 0)
    win_rate = metrics.get("win_rate", 0.0) * 100
    sharpe = metrics.get("sharpe_ratio", 0.0)
    pf = metrics.get("profit_factor", 0.0)
    max_dd = metrics.get("max_drawdown_pct", 0.0)

    best, worst = _best_worst_symbols(trades)

    # Sector breakdown sorted by pnl
    sector_lines: list[str] = []
    sorted_sectors = sorted(
        sector_metrics.items(),
        key=lambda kv: kv[1].get("total_pnl", 0.0),
        reverse=True,
    )
    for sector, sm in sorted_sectors:
        s_pnl = sm.get("total_pnl", 0.0)
        s_count = sm.get("count", 0)
        s_wr = sm.get("win_rate", 0.0) * 100
        sector_lines.append(f"  {sector}: {s_pnl:+.2f} ({s_count} trades, {s_wr:.0f}% WR)")

    # Recon summary
    matched = sum(1 for s in recon_history if getattr(s, "matches", True))
    total_recon = len(recon_history)
    recon_str = f"{matched}/{total_recon} days matched" if total_recon else "no data"

    lines = [
        f"WEEKLY REPORT -- Week of {week_str}",
        "=" * 40,
        f"Total PnL: {total_pnl:+.2f}",
        f"Total trades: {count} ({win_count}W / {loss_count}L)",
        f"Win rate: {win_rate:.1f}% | Sharpe: {sharpe:.2f} | Profit factor: {pf:.1f}",
        f"Max drawdown: {max_dd:.1f}%",
        "",
        "Top sectors:",
    ]
    lines.extend(sector_lines) if sector_lines else lines.append("  (none)")
    lines.append("")
    lines.append(f"Best trade: {best}")
    lines.append(f"Worst trade: {worst}")
    lines.append(f"Reconciliation: {recon_str}")

    return "\n".join(lines)


# ── Async orchestrators ──────────────────────────────────────────


async def generate_daily_report(
    tracker,
    risk_controller,
    reconciler,
    db_path: str,
    equity: float = 10_000.0,
) -> str:
    """Fetch today's trades, compute metrics, build daily report."""
    since = _start_of_today()
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    try:
        trades = await query_trades(db, since_ts=since)
    finally:
        await db.close()

    metrics = compute_metrics(trades, equity)
    recon_ok = bool(reconciler.history and reconciler.history[-1].matches)
    return build_daily_report(trades, metrics, tracker, risk_controller.is_halted, recon_ok, equity)


async def generate_weekly_report(
    tracker,
    risk_controller,
    reconciler,
    db_path: str,
    equity: float = 10_000.0,
) -> str:
    """Fetch last 7 days of trades, compute metrics + sector breakdown, build weekly report."""
    since = _start_of_week()
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    try:
        trades = await query_trades(db, since_ts=since)
    finally:
        await db.close()

    metrics = compute_metrics(trades, equity)
    sector_metrics = compute_metrics_by_group(trades, equity, "sector")
    return build_weekly_report(trades, metrics, sector_metrics, tracker, reconciler.history)


async def send_daily_report(
    tracker, risk_controller, reconciler, db_path: str, equity: float = 10_000.0
) -> str:
    """Generate daily report and send via WhatsApp. Returns the report text."""
    report = await generate_daily_report(tracker, risk_controller, reconciler, db_path, equity)
    await send_whatsapp(report)
    return report


async def send_weekly_report(
    tracker, risk_controller, reconciler, db_path: str, equity: float = 10_000.0
) -> str:
    """Generate weekly report and send via WhatsApp. Returns the report text."""
    report = await generate_weekly_report(tracker, risk_controller, reconciler, db_path, equity)
    await send_whatsapp(report)
    return report
