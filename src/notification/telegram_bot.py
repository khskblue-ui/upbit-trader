"""Telegram notification bot for trade events and system alerts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """Send messages to a Telegram chat via the Bot API.

    Args:
        bot_token: Telegram bot token (from BotFather).
        chat_id: Target chat or channel ID.
        enabled: When ``False`` all send calls are silently dropped.
    """

    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._enabled = enabled and bool(bot_token) and bool(chat_id)
        self._url = _TELEGRAM_API.format(token=bot_token)

    # ------------------------------------------------------------------
    # Core send
    # ------------------------------------------------------------------

    async def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send *text* to the configured chat.

        Args:
            text: Message content (HTML or Markdown).
            parse_mode: "HTML" or "Markdown".

        Returns:
            ``True`` on success, ``False`` on failure or when disabled.
        """
        if not self._enabled:
            logger.debug("[Telegram disabled] %s", text[:80])
            return False

        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self._url, json=payload)
                response.raise_for_status()
                logger.debug("Telegram message sent: %s", text[:60])
                return True
        except Exception as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Event-specific helpers
    # ------------------------------------------------------------------

    async def notify_buy(
        self,
        market: str,
        price: float,
        quantity: float,
        strategy: str,
        confidence: float,
    ) -> bool:
        """Notify on a buy order execution."""
        text = (
            f"🟢 <b>[매수 체결]</b>\n"
            f"마켓: <code>{market}</code>\n"
            f"가격: <code>{price:,.0f} KRW</code>\n"
            f"수량: <code>{quantity:.6f}</code>\n"
            f"전략: <code>{strategy}</code>\n"
            f"신뢰도: <code>{confidence:.1%}</code>"
        )
        return await self.send(text)

    async def notify_sell(
        self,
        market: str,
        price: float,
        quantity: float,
        pnl: float,
        strategy: str,
    ) -> bool:
        """Notify on a sell order execution with PnL."""
        icon = "🔴" if pnl < 0 else "🔵"
        pnl_pct = pnl / (price * quantity - pnl) * 100 if price * quantity != 0 else 0
        text = (
            f"{icon} <b>[매도 체결]</b>\n"
            f"마켓: <code>{market}</code>\n"
            f"가격: <code>{price:,.0f} KRW</code>\n"
            f"수량: <code>{quantity:.6f}</code>\n"
            f"손익: <code>{pnl:+,.0f} KRW ({pnl_pct:+.2f}%)</code>\n"
            f"전략: <code>{strategy}</code>"
        )
        return await self.send(text)

    async def notify_error(self, message: str, critical: bool = False) -> bool:
        """Notify on a system error."""
        icon = "🚨" if critical else "⚠️"
        priority = "긴급" if critical else "오류"
        text = f"{icon} <b>[{priority}]</b>\n{message}"
        return await self.send(text)

    async def notify_mdd_warning(self, current_mdd_pct: float, limit_pct: float) -> bool:
        """Notify when MDD approaches or exceeds the configured limit."""
        text = (
            f"🚨 <b>[MDD 경고]</b>\n"
            f"현재 MDD: <code>{current_mdd_pct:.2f}%</code>\n"
            f"한도: <code>{limit_pct:.2f}%</code>\n"
            f"자동매매가 일시 중단될 수 있습니다."
        )
        return await self.send(text)

    async def notify_daily_report(
        self,
        date: str,
        total_pnl: float,
        trade_count: int,
        win_rate: float,
        total_balance: float,
    ) -> bool:
        """Send a daily performance summary."""
        icon = "📈" if total_pnl >= 0 else "📉"
        text = (
            f"{icon} <b>[일일 보고 — {date}]</b>\n"
            f"총 손익: <code>{total_pnl:+,.0f} KRW</code>\n"
            f"거래 횟수: <code>{trade_count}회</code>\n"
            f"승률: <code>{win_rate:.1f}%</code>\n"
            f"총 자산: <code>{total_balance:,.0f} KRW</code>"
        )
        return await self.send(text)

    async def notify_system_start(self, mode: str) -> bool:
        """Notify when the trading engine starts."""
        text = f"✅ <b>[시스템 시작]</b>\n모드: <code>{mode}</code>"
        return await self.send(text)

    async def notify_system_stop(self, reason: str = "") -> bool:
        """Notify when the trading engine stops."""
        text = f"🛑 <b>[시스템 종료]</b>"
        if reason:
            text += f"\n사유: {reason}"
        return await self.send(text)
