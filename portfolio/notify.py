"""Notification agent: WhatsApp alerts via CallMeBot."""

import asyncio
import logging
from urllib.parse import quote

import aiohttp

from config.settings import notify_config

log = logging.getLogger(__name__)

_send_lock = asyncio.Lock()


async def send_whatsapp(message: str) -> bool:
    """Send a WhatsApp message via CallMeBot. Returns True on success."""
    if not notify_config.whatsapp_api_url or not notify_config.whatsapp_api_key:
        return False

    url = (
        f"{notify_config.whatsapp_api_url}"
        f"&text={quote(message)}"
        f"&apikey={notify_config.whatsapp_api_key}"
    )

    async with _send_lock:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return True
                    log.warning("WhatsApp send failed: HTTP %s", resp.status)
                    return False
        except asyncio.TimeoutError:
            log.warning("WhatsApp send timed out")
            return False
        except Exception:
            log.warning("WhatsApp send error", exc_info=True)
            return False


async def notify_entry(
    symbol: str,
    direction: str,
    shares: int,
    entry_price: float,
    stop_price: float,
    signal_score: float,
    regime: str | None = None,
) -> None:
    """Trade entry alert."""
    regime_str = f" | Regime: {regime}" if regime else ""
    msg = (
        f"✅ ENTRY {direction.upper()} {shares} {symbol} @ ${entry_price:.2f}\n"
        f"Stop: ${stop_price:.2f} | Score: {signal_score:.1f}{regime_str}"
    )
    await send_whatsapp(msg)


async def notify_exit(
    symbol: str,
    direction: str,
    shares: int,
    entry_price: float,
    exit_price: float,
    pnl: float,
    pnl_pct: float,
    exit_reason: str,
) -> None:
    """Trade exit alert."""
    emoji = "🟢" if pnl >= 0 else "🔴"
    msg = (
        f"{emoji} EXIT {direction.upper()} {shares} {symbol} @ ${exit_price:.2f}\n"
        f"Entry: ${entry_price:.2f} | PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
        f"Reason: {exit_reason}"
    )
    await send_whatsapp(msg)


async def notify_risk(warnings: list[str], halt: bool) -> None:
    """Risk controller alert — drawdown, heat, NLV warnings."""
    prefix = "🛑 RISK HALT" if halt else "⚠️ RISK WARNING"
    body = "\n".join(f"• {w}" for w in warnings)
    await send_whatsapp(f"{prefix}\n{body}")


async def notify_reconciliation_fail(mismatches: list[str]) -> None:
    """Reconciliation mismatch — system halted."""
    body = "\n".join(f"• {m}" for m in mismatches)
    await send_whatsapp(f"🛑 RECONCILIATION HALT\n{body}")


async def notify_daily_summary(metrics: dict) -> None:
    """End-of-day performance summary."""
    msg = (
        f"📊 DAILY SUMMARY\n"
        f"Trades: {metrics.get('count', 0)}\n"
        f"PnL: ${metrics.get('total_pnl', 0):+.2f}\n"
        f"Win rate: {metrics.get('win_rate', 0):.0%}\n"
        f"Sharpe: {metrics.get('sharpe', 0):.2f}\n"
        f"Profit factor: {metrics.get('profit_factor', 0):.2f}"
    )
    await send_whatsapp(msg)
