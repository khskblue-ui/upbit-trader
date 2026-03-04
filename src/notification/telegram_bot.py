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
        exit_reason: str = "",
    ) -> bool:
        """Notify on a sell order execution with PnL and exit reason.

        Args:
            exit_reason: Human-readable trigger description, e.g.
                ``"HARD_STOP(2,850,000): 급락/갭다운 — 진입가 대비 -5.2%"``.
                If empty, no 사유 line is appended.
        """
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
        if exit_reason:
            text += f"\n사유: <code>{exit_reason}</code>"
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

    async def notify_order_failed(
        self,
        market: str,
        side: str,
        error: str,
        strategy: str,
    ) -> bool:
        """Notify when an order fails to execute."""
        side_kor = "매수" if side == "buy" else "매도"
        text = (
            f"⚠️ <b>[주문 실패]</b>\n"
            f"마켓: <code>{market}</code>\n"
            f"방향: <code>{side_kor}</code>\n"
            f"전략: <code>{strategy}</code>\n"
            f"사유: <code>{error}</code>"
        )
        return await self.send(text)

    async def notify_signal(
        self,
        market: str,
        strategy: str,
        signal: str,
        confidence: float,
        reason: str,
        metadata: dict | None = None,
    ) -> bool:
        """Notify when a trading signal is detected (before risk check)."""
        icon = "🔔"
        signal_kor = "매수" if signal == "buy" else "매도" if signal == "sell" else "홀드"
        lines = [
            f"{icon} <b>[신호 감지]</b>",
            f"마켓: <code>{market}</code>",
            f"전략: <code>{strategy}</code>",
            f"신호: <code>{signal_kor}</code>",
            f"신뢰도: <code>{confidence:.1%}</code>",
            f"근거: {reason}",
        ]
        if metadata:
            detail_keys = ["ema_20", "ema_60", "rsi", "atr_pct", "target_price", "k_value", "position_krw"]
            detail_lines = []
            for key in detail_keys:
                if key in metadata:
                    val = metadata[key]
                    if isinstance(val, float) and key not in ("k_value", "rsi", "atr_pct"):
                        detail_lines.append(f"  {key}: <code>{val:,.0f}</code>")
                    else:
                        detail_lines.append(f"  {key}: <code>{val}</code>")
            if detail_lines:
                lines.append("─────────────────")
                lines.extend(detail_lines)
        return await self.send("\n".join(lines))

    async def notify_risk_check(
        self,
        market: str,
        strategy: str,
        decision: str,
        reasons: list[str],
    ) -> bool:
        """Notify risk engine decision (APPROVE / MODIFY / REJECT)."""
        d = decision.lower()
        if d == "approve":
            icon, decision_kor = "✅", "승인"
        elif d == "modify":
            icon, decision_kor = "⚠️", "수정"
        else:
            icon, decision_kor = "🚫", "거절"

        lines = [
            f"{icon} <b>[리스크 점검: {decision_kor}]</b>",
            f"마켓: <code>{market}</code>",
            f"전략: <code>{strategy}</code>",
        ]
        for r in reasons:
            lines.append(f"  • {r}")
        return await self.send("\n".join(lines))

    async def notify_strategy_changed(
        self,
        action: str,
        strategy_name: str,
        detail: str = "",
    ) -> bool:
        """Notify when a strategy is changed via Telegram command."""
        icon = {"enable": "▶️", "disable": "⏹️", "set": "⚙️"}.get(action, "ℹ️")
        action_kor = {"enable": "활성화", "disable": "비활성화", "set": "파라미터 변경"}.get(action, action)
        text = (
            f"{icon} <b>[전략 변경]</b>\n"
            f"전략: <code>{strategy_name}</code>\n"
            f"작업: <code>{action_kor}</code>"
        )
        if detail:
            text += f"\n상세: {detail}"
        return await self.send(text)

    async def notify_mode_changed(
        self,
        old_mode: str,
        new_mode: str,
    ) -> bool:
        """Notify when trading mode is switched (paper ↔ live)."""
        icon = "🔴" if new_mode == "live" else "📄"
        mode_kor = {"live": "실거래", "paper": "모의투자"}.get(new_mode, new_mode)
        old_kor = {"live": "실거래", "paper": "모의투자"}.get(old_mode, old_mode)
        text = (
            f"{icon} <b>[모드 전환]</b>\n"
            f"<code>{old_kor}</code> → <code>{mode_kor}</code>\n"
        )
        if new_mode == "live":
            text += "⚠️ <b>실제 자금으로 거래합니다.</b>"
        else:
            text += "📄 가상 자금으로 거래합니다."
        return await self.send(text)
