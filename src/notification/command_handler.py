"""Telegram command handler — receive and process user commands from Telegram.

Supported commands:
    /ping              — Check if the bot is alive
    /status            — Current balance, positions, mode
    /strategy          — Active strategies and parameters
    /enable <name>     — Enable a strategy by name
    /disable <name>    — Disable a strategy by name
    /set <name> <param> <value>  — Change a strategy parameter
    /k <val>           — Change k_value (0.1–0.9) for all applicable strategies
    /switchstrategy <name|alias>  — Switch to a strategy exclusively (enables one, disables all others)
                                    Aliases: tfvb (trend_filtered_breakout), imb (intraday_momentum_breakout)
    /briefing          — Send the current accumulated briefing now and reset the window
    /mode <paper|live> — Switch trading mode (paper = virtual, live = real money)
    /pause             — Pause trading (no new orders)
    /resume            — Resume trading
    /stop              — Gracefully stop the trading engine
    /help              — List all commands
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
            "/ping":             self._cmd_ping,
            "/status":           self._cmd_status,
            "/strategy":         self._cmd_strategy,
            "/enable":           self._cmd_enable,
            "/disable":          self._cmd_disable,
            "/set":              self._cmd_set,
            "/k":                self._cmd_k,
            "/switchstrategy":   self._cmd_switchstrategy,
            "/briefing":         self._cmd_briefing,
            "/mode":             self._cmd_mode,
            "/pause":            self._cmd_pause,
            "/resume":           self._cmd_resume,
            "/stop":             self._cmd_stop,
            "/help":             self._cmd_help,
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

    async def _cmd_enable(self, args: list[str]) -> None:
        """Enable a strategy by name."""
        if not args:
            names = [s.name for s in self._strategies]
            await self._notifier.send(
                f"사용법: /enable &lt;전략명&gt;\n"
                f"전략 목록: <code>{', '.join(names)}</code>"
            )
            return

        target = args[0].lower()
        matched = [s for s in self._strategies if s.name.lower() == target]
        if not matched:
            names = [s.name for s in self._strategies]
            await self._notifier.send(
                f"⚠️ 전략을 찾을 수 없음: <code>{target}</code>\n"
                f"전략 목록: <code>{', '.join(names)}</code>"
            )
            return

        strategy = matched[0]
        strategy.config.enabled = True
        logger.info("Strategy '%s' enabled via Telegram", strategy.name)
        await self._notifier.notify_strategy_changed("enable", strategy.name)

    async def _cmd_disable(self, args: list[str]) -> None:
        """Disable a strategy by name."""
        if not args:
            names = [s.name for s in self._strategies]
            await self._notifier.send(
                f"사용법: /disable &lt;전략명&gt;\n"
                f"전략 목록: <code>{', '.join(names)}</code>"
            )
            return

        target = args[0].lower()
        matched = [s for s in self._strategies if s.name.lower() == target]
        if not matched:
            names = [s.name for s in self._strategies]
            await self._notifier.send(
                f"⚠️ 전략을 찾을 수 없음: <code>{target}</code>\n"
                f"전략 목록: <code>{', '.join(names)}</code>"
            )
            return

        strategy = matched[0]
        strategy.config.enabled = False
        logger.info("Strategy '%s' disabled via Telegram", strategy.name)
        await self._notifier.notify_strategy_changed("disable", strategy.name)

    async def _cmd_set(self, args: list[str]) -> None:
        """Change a specific parameter on a strategy: /set <name> <param> <value>."""
        if len(args) < 3:
            await self._notifier.send(
                "사용법: /set &lt;전략명&gt; &lt;파라미터&gt; &lt;값&gt;\n"
                "예시:\n"
                "  /set trend_filtered_breakout k_value 0.35\n"
                "  /set trend_filtered_breakout rsi_min 40\n"
                "  /set trend_filtered_breakout rsi_max 75\n"
                "  /set trend_filtered_breakout atr_risk_pct 0.015"
            )
            return

        target, param, raw_value = args[0].lower(), args[1].lower(), args[2]
        matched = [s for s in self._strategies if s.name.lower() == target]
        if not matched:
            names = [s.name for s in self._strategies]
            await self._notifier.send(
                f"⚠️ 전략을 찾을 수 없음: <code>{target}</code>\n"
                f"전략 목록: <code>{', '.join(names)}</code>"
            )
            return

        strategy = matched[0]
        if not hasattr(strategy.config, param):
            await self._notifier.send(
                f"⚠️ 파라미터를 찾을 수 없음: <code>{param}</code>\n"
                f"해당 전략의 config 속성을 확인해주세요."
            )
            return

        # Type-safe conversion
        try:
            old_val = getattr(strategy.config, param)
            if isinstance(old_val, bool):
                new_val = raw_value.lower() in ("true", "1", "yes")
            elif isinstance(old_val, int):
                new_val = int(raw_value)
            else:
                new_val = float(raw_value)
        except (ValueError, TypeError):
            await self._notifier.send(
                f"⚠️ 잘못된 값: <code>{raw_value}</code>\n숫자를 입력하세요."
            )
            return

        setattr(strategy.config, param, new_val)
        detail = f"<code>{param}</code>: <code>{old_val}</code> → <code>{new_val}</code>"
        logger.info("Strategy '%s' param %s: %s -> %s via Telegram", strategy.name, param, old_val, new_val)
        await self._notifier.notify_strategy_changed("set", strategy.name, detail)

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

    async def _cmd_switchstrategy(self, args: list[str]) -> None:
        """Exclusively switch to one strategy — enables it, disables all others.

        Supports short aliases: ``tfvb`` and ``imb``.
        """
        # Canonical name mapping (alias → full strategy name)
        _ALIASES: dict[str, str] = {
            "tfvb": "trend_filtered_breakout",
            "imb": "intraday_momentum_breakout",
            "trend_filtered_breakout": "trend_filtered_breakout",
            "intraday_momentum_breakout": "intraday_momentum_breakout",
        }

        if not args:
            strategy_list = "\n".join(
                f"  • {s.name} ({'✅' if s.config.enabled else '❌'})"
                for s in self._strategies
            )
            await self._notifier.send(
                "🔄 <b>[전략 전환]</b>\n"
                "사용법: /switchstrategy &lt;전략명 또는 단축명&gt;\n\n"
                "<b>단축명:</b>\n"
                "  tfvb — trend_filtered_breakout (일봉 전략)\n"
                "  imb  — intraday_momentum_breakout (1시간봉 전략)\n\n"
                f"<b>로드된 전략:</b>\n{strategy_list}"
            )
            return

        key = args[0].lower()
        target_name = _ALIASES.get(key)

        if target_name is None:
            loaded = [s.name for s in self._strategies]
            await self._notifier.send(
                f"⚠️ 알 수 없는 전략: <code>{key}</code>\n"
                f"단축명: <code>tfvb</code>, <code>imb</code>\n"
                f"전체 명칭: <code>{', '.join(loaded)}</code>"
            )
            return

        matched = [s for s in self._strategies if s.name == target_name]
        if not matched:
            await self._notifier.send(
                f"⚠️ 전략이 로드되지 않음: <code>{target_name}</code>\n"
                f"strategies.yaml에 해당 전략이 설정되어 있는지 확인하세요."
            )
            return

        # Enable only the target strategy; disable all others
        enabled_strategy = matched[0]
        disabled_names = []
        for s in self._strategies:
            if s.name == target_name:
                s.config.enabled = True
            else:
                if s.config.enabled:
                    disabled_names.append(s.name)
                s.config.enabled = False

        disabled_text = (
            "\n비활성화: " + ", ".join(f"<code>{n}</code>" for n in disabled_names)
            if disabled_names else ""
        )
        logger.info(
            "Strategy switched to '%s' via Telegram (disabled: %s)",
            target_name, disabled_names,
        )
        await self._notifier.send(
            f"🔄 <b>전략 전환 완료</b>\n"
            f"활성화: <code>{enabled_strategy.name}</code>"
            f"{disabled_text}"
        )

    async def _cmd_briefing(self, args: list[str]) -> None:
        """Manually send the current accumulated briefing and reset the stats window."""
        try:
            await self._engine.send_briefing_now()
            await self._notifier.send("✅ <b>브리핑 전송 완료</b>\n통계 창이 초기화되었습니다.")
        except Exception as exc:
            logger.error("Failed to send manual briefing: %s", exc)
            await self._notifier.send(f"⚠️ 브리핑 전송 실패: <code>{exc}</code>")

    async def _cmd_mode(self, args: list[str]) -> None:
        """Switch between paper (virtual) and live (real money) trading mode."""
        current_mode = self._engine._mode
        if not args:
            current_kor = "실거래" if current_mode == "live" else "모의투자"
            has_live = self._engine._live_executor is not None
            live_avail = "✅ 사용 가능" if has_live else "❌ API 키 미설정"
            await self._notifier.send(
                f"📡 <b>[현재 모드]</b> <code>{current_kor}</code>\n\n"
                f"모드 전환:\n"
                f"  /mode live  — 실거래 ({live_avail})\n"
                f"  /mode paper — 모의투자 (항상 가능)\n\n"
                f"⚠️ live 모드는 실제 자금으로 거래됩니다."
            )
            return

        new_mode = args[0].lower()
        if new_mode not in ("paper", "live"):
            await self._notifier.send(
                "⚠️ 잘못된 모드입니다.\n"
                "사용법: /mode paper 또는 /mode live"
            )
            return

        try:
            old_mode = self._engine._mode
            self._engine.switch_mode(new_mode)
            # Update command handler's own executor reference for /status queries
            self._executor = self._engine.executor
            self._mode = new_mode
            logger.info("Mode switched via Telegram: %s → %s", old_mode, new_mode)
            await self._notifier.notify_mode_changed(old_mode, new_mode)
        except ValueError as exc:
            await self._notifier.send(f"⚠️ 모드 전환 실패: <code>{exc}</code>")

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
            "<b>📊 모니터링</b>\n"
            "/ping — 봇 작동 여부 확인\n"
            "/status — 현재 잔고 및 포지션\n"
            "/strategy — 전략 현황 및 파라미터\n\n"
            "<b>⚙️ 전략 제어</b>\n"
            "/enable &lt;전략명&gt; — 전략 활성화\n"
            "/disable &lt;전략명&gt; — 전략 비활성화\n"
            "/set &lt;전략명&gt; &lt;파라미터&gt; &lt;값&gt; — 파라미터 변경\n"
            "  예: /set trend_filtered_breakout k_value 0.35\n"
            "  예: /set intraday_momentum_breakout rsi_min 52\n"
            "/k &lt;값&gt; — K값 일괄 변경 (범위 0.1~0.9)\n"
            "/switchstrategy &lt;tfvb|imb&gt; — 전략 독점 전환\n"
            "  tfvb: 일봉 전략 (EMA20/60, 일봉)\n"
            "  imb: 1시간봉 전략 (EMA24/120, 60m)\n\n"
            "<b>🎮 운영 제어</b>\n"
            "/briefing — 현재 구간 브리핑 즉시 전송 및 창 초기화\n"
            "/mode &lt;paper|live&gt; — 모드 전환 (모의투자 ↔ 실거래)\n"
            "/pause — 거래 일시정지 (모니터링 계속)\n"
            "/resume — 거래 재개\n"
            "/stop — 봇 종료\n"
            "/help — 이 메시지 표시"
        )
        await self._notifier.send(text)
