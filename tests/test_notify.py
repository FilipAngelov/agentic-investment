"""Tests for portfolio.notify — WhatsApp notification agent via Clawdbot."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from portfolio import notify


@pytest.fixture(autouse=True)
def _reset_lock():
    """Reset the module-level send lock between tests."""
    notify._send_lock = asyncio.Lock()


# --- send_whatsapp ---


@pytest.mark.asyncio
async def test_send_whatsapp_not_configured():
    with patch.object(
        notify,
        "notify_config",
        MagicMock(clawdbot_url="", clawdbot_token="", fallback_log="/tmp/test_notify.log"),
    ):
        assert await notify.send_whatsapp("hi") is False


def _make_session(resp=None, side_effect=None):
    """Build a mock aiohttp session with proper context manager support."""
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    if side_effect is not None:
        mock_session.post.side_effect = side_effect
    else:
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        mock_session.post.return_value = cm

    return mock_session


@pytest.mark.asyncio
async def test_send_whatsapp_success():
    mock_resp = MagicMock(status=200)
    mock_session = _make_session(resp=mock_resp)

    with (
        patch.object(
            notify,
            "notify_config",
            MagicMock(
                clawdbot_url="http://127.0.0.1:18789",
                clawdbot_token="testtoken",
                notify_target="+1234",
                fallback_log="/tmp/test_notify.log",
            ),
        ),
        patch("portfolio.notify.aiohttp.ClientSession", return_value=mock_session),
    ):
        result = await notify.send_whatsapp("hello world")
        assert result is True
        call_kwargs = mock_session.post.call_args
        assert "tools/invoke" in call_kwargs[1].get("url", call_kwargs[0][0] if call_kwargs[0] else "")


@pytest.mark.asyncio
async def test_send_whatsapp_http_error():
    mock_resp = MagicMock(status=500)
    mock_session = _make_session(resp=mock_resp)

    with (
        patch.object(
            notify,
            "notify_config",
            MagicMock(
                clawdbot_url="http://127.0.0.1:18789",
                clawdbot_token="t",
                notify_target="+1",
                fallback_log="/tmp/test_notify.log",
            ),
        ),
        patch("portfolio.notify.aiohttp.ClientSession", return_value=mock_session),
        patch.object(notify, "_fallback_log"),
    ):
        assert await notify.send_whatsapp("test") is False


@pytest.mark.asyncio
async def test_send_whatsapp_timeout():
    mock_session = _make_session(side_effect=asyncio.TimeoutError())
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(
            notify,
            "notify_config",
            MagicMock(
                clawdbot_url="http://127.0.0.1:18789",
                clawdbot_token="t",
                notify_target="+1",
                fallback_log="/tmp/test_notify.log",
            ),
        ),
        patch("portfolio.notify.aiohttp.ClientSession", return_value=mock_session),
        patch.object(notify, "_fallback_log"),
    ):
        assert await notify.send_whatsapp("test") is False


# --- High-level notification helpers ---


@pytest.mark.asyncio
async def test_notify_entry():
    with patch.object(notify, "send_whatsapp", new_callable=AsyncMock) as mock_send:
        await notify.notify_entry("AAPL", "long", 10, 150.0, 145.0, 8.5, regime="bull")
        msg = mock_send.call_args[0][0]
        assert "AAPL" in msg
        assert "LONG" in msg
        assert "150.00" in msg
        assert "145.00" in msg
        assert "bull" in msg


@pytest.mark.asyncio
async def test_notify_exit_win():
    with patch.object(notify, "send_whatsapp", new_callable=AsyncMock) as mock_send:
        await notify.notify_exit("AAPL", "long", 10, 150.0, 160.0, 100.0, 6.67, "target")
        msg = mock_send.call_args[0][0]
        assert "[+]" in msg
        assert "+100.00" in msg


@pytest.mark.asyncio
async def test_notify_exit_loss():
    with patch.object(notify, "send_whatsapp", new_callable=AsyncMock) as mock_send:
        await notify.notify_exit("AAPL", "long", 10, 150.0, 140.0, -100.0, -6.67, "stop")
        msg = mock_send.call_args[0][0]
        assert "[-]" in msg


@pytest.mark.asyncio
async def test_notify_risk_halt():
    with patch.object(notify, "send_whatsapp", new_callable=AsyncMock) as mock_send:
        await notify.notify_risk(["Drawdown 3%", "Heat 7%"], halt=True)
        msg = mock_send.call_args[0][0]
        assert "HALT" in msg
        assert "Drawdown 3%" in msg


@pytest.mark.asyncio
async def test_notify_risk_warning():
    with patch.object(notify, "send_whatsapp", new_callable=AsyncMock) as mock_send:
        await notify.notify_risk(["Heat 5.5%"], halt=False)
        msg = mock_send.call_args[0][0]
        assert "WARNING" in msg


@pytest.mark.asyncio
async def test_notify_reconciliation_fail():
    with patch.object(notify, "send_whatsapp", new_callable=AsyncMock) as mock_send:
        await notify.notify_reconciliation_fail(["AAPL: bot=100 ibkr=90"])
        msg = mock_send.call_args[0][0]
        assert "RECONCILIATION HALT" in msg
        assert "AAPL" in msg


@pytest.mark.asyncio
async def test_notify_daily_summary():
    with patch.object(notify, "send_whatsapp", new_callable=AsyncMock) as mock_send:
        await notify.notify_daily_summary({
            "count": 5, "total_pnl": 123.45, "win_rate": 0.6,
            "sharpe": 1.5, "profit_factor": 2.1,
        })
        msg = mock_send.call_args[0][0]
        assert "5" in msg
        assert "123.45" in msg
        assert "60%" in msg


@pytest.mark.asyncio
async def test_send_whatsapp_serializes_calls():
    """Verify the lock serializes concurrent sends."""
    call_order = []

    class FakeCtx:
        def __init__(self):
            self.status = 200

        async def __aenter__(self):
            call_order.append("start")
            await asyncio.sleep(0.01)
            return self

        async def __aexit__(self, *a):
            call_order.append("end")

    mock_session = MagicMock()
    mock_session.post = MagicMock(side_effect=lambda *a, **kw: FakeCtx())
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(
            notify,
            "notify_config",
            MagicMock(
                clawdbot_url="http://127.0.0.1:18789",
                clawdbot_token="t",
                notify_target="+1",
                fallback_log="/tmp/test_notify.log",
            ),
        ),
        patch("portfolio.notify.aiohttp.ClientSession", return_value=mock_session),
    ):
        await asyncio.gather(
            notify.send_whatsapp("a"),
            notify.send_whatsapp("b"),
        )
        # With lock, calls are serialized: start,end,start,end
        assert call_order == ["start", "end", "start", "end"]
