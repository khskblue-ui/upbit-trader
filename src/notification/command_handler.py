"""Telegram command handler — receive and process user commands from Telegram.

Supported commands:
    /ping     — Check if the bot is alive
    /status   — Current balance, positions, mode
    /strategy — Active strategies and K values
    /k <val>  — Change k_value (0.1–0.9) for all volatility breakout strategies
    /pause    — Pause trading (no new orders)
    /resume   — Resume trading
    /stop     — Gracefully stop the trading engine
    /help     — List all commands
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Callable

import httpx

if TYPE_CHECKING:
    from src.notification.telegram_bot import TelegramNotifier
    from src.core.trading_engine import TradingEngine
    from src.execution.base import BaseExecutor
    from src.strategy.base import BaseStrategy

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


class TelegramCommandHandler:
    """Poll Telegram for incoming messages and handle bot commands.

    Security: only messages from ``authorized_chat_id`` are processed.
    All other senders receive no response (silent discard).

    Args:
        notifier: :class:`TelegramNotifier` used to send replies.
        engine: :class:`TradingEngine` — used to pause/resume/inspect state.
        executor: :class:`BaseExecutor` — used to query balance and positions.
        strategies: List of active strategy instances.
        mode: Trading mode string (``"paper"`` or ``"live"``).
        stop_callback: Callable invoked when the user sends ``/stop``.
        authorized_chat_id: Only this Telegram chat ID may issue commands.
    """

    def __init__(
        self,
        notifier: "TelegramNotifier",
        engine: "TradingEngine",
        executor: "BaseExecutor",
        strategies: list["BaseStrategy"],
        mode: str,
        stop_callback: Callable[[], None],
        authorized_chat_id: str,
    ) -> None:
        self._notifier = notifier
        self._engine = engine
        self._executor = executor
        self._strategies = strategies
        self._mode = mode
        self._stop_callback = stop_callback
        self._authorized_chat_id = str(authorized_chat_id)
        self._offset = 0
        self._running = False
        self._base_url = f"https://api.telegram.org/bot{notifier._token}"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main polling loop — runs until cancelled."""
        self._running = True
        logger.info("TelegramCommandHandler: polling started (chat_id=%s)", self._authorized_chat_id)
        while self._running:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._process_update(update)
                    self._offset = update["update_id"] + 1
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("CommandHandler polling error: %s", exc)
            await asyncio.sleep(2)

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    async def _get_updates(self) -> list[dict]:
        """Fetch pending updates from the Telegram getUpdates API."""
        params = {
            "offset": self._offset,
            "timeout": 1,
            "allowed_updates": ["message"],
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/getUpdates", params=params)
                data = resp.json()
                if data.get("ok"):
                    return data.get("result", [])
        except Exception as exc:
            logger.debug("getUpdates error: %s", exc)
        return []

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def _process_update(self, update: dict) -> None:
        """Route an incoming Telegram update to the right command handler."""
        message = update.get("message", {})
        if not message:
            return

        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()

        # Security: silent discard for unauthorised senders
        if chat_id != self._authorized_chat_id:
            logger.warning("Unauthorised command from chat_id=%s — ignored", chat_id)
            return

        if not text.startswith("/"):
            return

        parts = text.split()
        # Strip @BotName suffix (e.g. /status@GustjdBot)
        command = parts[0].lower().split("@")[0]
        args = parts[1:]

        logger.info("Telegram command: %s %s", command, args)

        handlers = {
            "/ping":     self._cmd_ping,
            "/status":   self._cmd_status,
            "/strategy": self._cmd_strategy,
            "/k":        self._cmd_k,
            "/pause":    self._cmd_pause,
            "/resume":   self._cmd_resume,
            "/stop":     self._cmd_stop,
            "/help":     self._cmd_help,
        }

        handler = handlers.get(command)
        if handler:
            try:
                await handler(args)
            except Exception as exc:
                logger.error("Error handling command %s: %s", command, exc)
                await self._notifier.send(f"⚠️ 명령 처리 중 오류: <code>{exc}</code>")
        else:
            await self._notifier.send(
                f"❓ 알 수 없는 명령어: <code>{command}</code>\n"
                f"/help 를 입력하면 명령어 목록을 볼 수 있습니다."
            )

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_ping(self, args: list[str]) -> None:
        """Reply with timestamp to confirm the bot is alive."""
        now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        paused = "⏸ 일시정지" if self._engine._paused else "▶ 실행 중"
        await self._notifier.send(
            f"🏓 <b>Pong!</b>\n"
            f"봇 상태: <code>{paused}</code>\n"
            f"시간: <code>{now}</code>"
        )

    async def _cmd_status(self, args: list[str]) -> None:
        """Report current balance, positions, and engine state."""
        try:
            balance = await self._executor.get_balance("KRW")
            positions = await self._executor.get_positions()
            paused = "⏸ 일시정지" if self._engine._paused else "▶ 실행 중"

            lines = [
                "📊 <b>[시스템 상태]</b>",
                f"모드: <code>{self._mode}</code>",
                f"상태: <code>{paused}</code>",
                f"KRW 잔고: <code>{balance:,.0f} KRW</code>",
            ]

            if positions:
                lines.append("\n<b>보유 포지션:</b>")
                for market, pos in positions.items():
                    qty = pos.get("quantity", 0)
                    avg = pos.get("avg_price", 0)
                    val = pos.get("current_value", qty * avg)
                    lines.append(
                        f"  • {market}: <code>{qty:.6f}</code>"
                        f" @ <code>{avg:,.0f} KRW</code>"
                        f" (평가액 <code>{val:,.0f} KRW</code>)"
                    )
            else:
                lines.append("보유 포지션: <code>없음</code>")

            await self._notifier.send("\n".join(lines))

        except Exception as exc:
            await self._notifier.send(f"⚠️ 상태 조회 실패: <code>{exc}</code>")

    async def _cmd_strategy(self, args: list[str]) -> None:
        """Show all loaded strategies with their current parameters."""
        if not self._strategies:
            await self._notifier.send("⚠️ 로드된 전략이 없습니다.")
            return

        lines = ["📈 <b>[전략 현황]</b>"]
        for s in self._strategies:
            enabled = getattr(s.config, "enabled", True)
            markets = getattr(s.config, "markets", [])
            k_val = getattr(s.config, "k_value", None)
            status = "✅ 활성" if enabled else "❌ 비활성"

            block = [
                f"\n<b>{s.name}</b> ({status})",
                f"  코인: <code>{', '.join(markets)}</code>",
            ]
            if k_val is not None:
                block.append(f"  K값: <code>{k_val}</code>")
            lines.extend(block)

        await self._notifier.send("\n".join(lines))

    async def _cmd_k(self, args: list[str]) -> None:
        """Change k_value for all volatility breakout strategies (hot-reload)."""
        if not args:
            await self._notifier.send(
                "사용법: /k &lt;값&gt;\n예시: /k 0.4\n유효 범위: 0.1 ~ 0.9"
            )
            return

        try:
            new_k = float(args[0])
        except ValueError:
            await self._notifier.send(
                f"⚠️ 잘못된 값: <code>{args[0]}</code>\n숫자를 입력하세요. 예: /k 0.4"
            )
            return

        if not (0.1 <= new_k <= 0.9):
            await self._notifier.send("⚠️ K값은 0.1 ~ 0.9 사이여야 합니다.")
            return

        updated = []
        for s in self._strategies:
            if hasattr(s.config, "k_value"):
                old_k = s.config.k_value
                s.config.k_value = new_k
                updated.append(f"  {s.name}: <code>{old_k}</code> → <code>{new_k}</code>")

        if updated:
            await self._notifier.send(
                "✅ <b>K값 변경 완료</b>\n" + "\n".join(updated)
            )
        else:
            await self._notifier.send("⚠️ K값을 사용하는 전략이 없습니다.")

    async def _cmd_pause(self, args: list[str]) -> None:
        """Pause order execution without stopping the engine."""
        self._engine._paused = True
        await self._notifier.send(
            "⏸ <b>거래 일시정지</b>\n"
            "신호가 발생해도 주문이 실행되지 않습니다.\n"
            "재개하려면 /resume 을 입력하세요."
        )

    async def _cmd_resume(self, args: list[str]) -> None:
        """Resume order execution after pause."""
        self._engine._paused = False
        await self._notifier.send("▶ <b>거래 재개</b>\n자동매매가 다시 시작되었습니다.")

    async def _cmd_stop(self, args: list[str]) -> None:
        """Gracefully stop the trading engine."""
        await self._notifier.send(
            "🛑 <b>시스템 종료 요청</b>\n잠시 후 봇이 종료됩니다."
        )
        self._stop_callback()

    async def _cmd_help(self, args: list[str]) -> None:
        """Show all available commands."""
        text = (
            "📋 <b>[사용 가능한 명령어]</b>\n\n"
            "/ping — 봇 작동 여부 확인\n"
            "/status — 현재 잔고 및 포지션\n"
            "/strategy — 전략 현황 및 설정\n"
            "/k &lt;값&gt; — K값 변경 (예: /k 0.4, 범위 0.1~0.9)\n"
            "/pause — 거래 일시정지 (모니터링은 계속)\n"
            "/resume — 거래 재개\n"
            "/stop — 봇 종료\n"
            "/help — 이 메시지 표시"
        )
        await self._notifier.send(text)
