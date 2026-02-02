"""Notification agent: WhatsApp alerts via Clawdbot gateway."""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from config.settings import notify_config

log = logging.getLogger(__name__)

_send_lock = asyncio.Lock()


async def send_whatsapp(message: str) -> bool:
    """Send a WhatsApp message via Clawdbot gateway. Falls back to log file."""
    # Try Clawdbot gateway first
    if notify_config.clawdbot_url and notify_config.clawdbot_token:
        async with _send_lock:
            try:
                url = f"{notify_config.clawdbot_url}/tools/invoke"
                payload = {
                    "tool": "agent_send",
                    "args": {
                        "to": notify_config.notify_target,
                        "message": message,
                        "channel": "whatsapp",
                        "deliver": True,
                    },
                }
                headers = {
                    "Authorization": f"Bearer {notify_config.clawdbot_token}",
                    "Content-Type": "application/json",
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status in (200, 201):
                            return True
                        log.warning("Clawdbot send failed: HTTP %s", resp.status)
            except asyncio.TimeoutError:
                log.warning("Clawdbot send timed out")
            except Exception:
                log.warning("Clawdbot send error", exc_info=True)

    # Fallback: append to log file
    _fallback_log(message)
    return False


def _fallback_log(message: str) -> None:
    """Append notification to fallback log file."""
    try:
        path = Path(notify_config.fallback_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with open(path, "a") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        log.warning("Fallback log write failed", exc_info=True)


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
        f"ENTRY {direction.upper()} {shares} {symbol} @ ${entry_price:.2f}\n"
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
    emoji = "+" if pnl >= 0 else "-"
    msg = (
        f"EXIT {direction.upper()} {shares} {symbol} @ ${exit_price:.2f}\n"
        f"Entry: ${entry_price:.2f} | PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%) [{emoji}]\n"
        f"Reason: {exit_reason}"
    )
    await send_whatsapp(msg)


async def notify_risk(warnings: list[str], halt: bool) -> None:
    """Risk controller alert — drawdown, heat, NLV warnings."""
    prefix = "RISK HALT" if halt else "RISK WARNING"
    body = "\n".join(f"- {w}" for w in warnings)
    await send_whatsapp(f"{prefix}\n{body}")


async def notify_reconciliation_fail(mismatches: list[str]) -> None:
    """Reconciliation mismatch — system halted."""
    body = "\n".join(f"- {m}" for m in mismatches)
    await send_whatsapp(f"RECONCILIATION HALT\n{body}")


async def notify_daily_summary(metrics: dict) -> None:
    """End-of-day performance summary."""
    msg = (
        f"DAILY SUMMARY\n"
        f"Trades: {metrics.get('count', 0)}\n"
        f"PnL: ${metrics.get('total_pnl', 0):+.2f}\n"
        f"Win rate: {metrics.get('win_rate', 0):.0%}\n"
        f"Sharpe: {metrics.get('sharpe', 0):.2f}\n"
        f"Profit factor: {metrics.get('profit_factor', 0):.2f}"
    )
    await send_whatsapp(msg)
